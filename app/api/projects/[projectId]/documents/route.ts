import { NextRequest, NextResponse } from 'next/server';
import { getSupabase } from '@/lib/supabase';
import { isProjectOwnedBy } from '@/lib/chat-access';
import { checkRateLimit } from '@/lib/rate-limit';
import { requireEntitlement } from '@/lib/entitlement';
import { mapProjectDocument } from '@/lib/projects';
import { logger, requestIdFrom } from '@/lib/server-logger';

// OCR is expensive (S3 download + per-page LLM vision). Cap how many extractions
// one user can trigger in a window. Enforced in Postgres (see lib/rate-limit.ts).
const OCR_RATE_MAX = 20;
const OCR_RATE_WINDOW_SECONDS = 300;

// NOTE: Для Yandex Cloud Serverless Containers используем Node.js runtime.
// GET читает список документов из Supabase. POST теперь только проксирует
// извлечение текста в Python-бэкенд (см. /documents/extract); тяжёлая обработка
// (скачивание из S3 + OCR + запись строки) живёт там.
export const runtime = 'nodejs';

function getProjectIdFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const projectsIndex = segments.lastIndexOf('projects');
  if (projectsIndex >= 0 && segments.length > projectsIndex + 1) {
    return segments[projectsIndex + 1];
  }
  return null;
}

export async function GET(req: NextRequest) {
  const projectId = getProjectIdFromRequest(req);
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  // Validate environment variables before proceeding
  const hasSupabaseUrl = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
  const hasSupabaseKey = !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!hasSupabaseUrl || !hasSupabaseKey) {
    const missing = [];
    if (!hasSupabaseUrl) missing.push('NEXT_PUBLIC_SUPABASE_URL');
    if (!hasSupabaseKey) missing.push('NEXT_PUBLIC_SUPABASE_ANON_KEY');

    logger.error('Missing environment variables', { project_id: projectId, request_id: requestId, event: 'missing_env', missing, hasSupabaseUrl, hasSupabaseKey });

    return NextResponse.json(
      { error: 'Server configuration error. Please contact support.' },
      { status: 500 }
    );
  }

  try {
    const supabase = await getSupabase();

    if (!supabase) {
      logger.error('Failed to create Supabase client', { project_id: projectId, request_id: requestId, event: 'supabase_client_error', hasSupabaseUrl, hasSupabaseKey });
      return NextResponse.json(
        { error: 'Не удалось подключиться к базе данных.' },
        { status: 500 }
      );
    }

    // Require an authenticated user who owns this project before returning any
    // document data (the extracted `text` column is sensitive tenant content).
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    if (!(await isProjectOwnedBy(supabase, projectId, user.id))) {
      return NextResponse.json({ error: 'Проект не найден или нет доступа.' }, { status: 404 });
    }

    const { data, error } = await supabase
      .from('project_documents')
      .select('*')
      .eq('project_id', projectId)
      .order('uploaded_at', { ascending: false });

    if (error) {
      logger.error('Supabase query failed', { project_id: projectId, request_id: requestId, event: 'supabase_query_error', err: error });
      return NextResponse.json(
        { error: 'Не удалось получить документы проекта.' },
        { status: 500 }
      );
    }

    try {
      const documents = (data ?? []).map(mapProjectDocument);
      logger.info('Project documents listed', { project_id: projectId, request_id: requestId, event: 'documents_listed', count: documents.length, duration_ms: Date.now() - startedAt });
      return NextResponse.json({ documents });
    } catch (mappingError) {
      logger.error('Document mapping failed', { project_id: projectId, request_id: requestId, event: 'document_mapping_error', dataCount: data?.length ?? 0, err: mappingError });
      return NextResponse.json(
        { error: 'Ошибка при обработке данных документов.' },
        { status: 500 }
      );
    }
  } catch (error) {
    logger.error('Unexpected error listing project documents', { project_id: projectId, request_id: requestId, event: 'unexpected_error', err: error, hasSupabaseUrl, hasSupabaseKey });
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера. Попробуйте позже.' },
      { status: 500 }
    );
  }
}

// Thin proxy to the Python backend. The file is already in S3 (objectKey); the
// backend downloads it, extracts text, writes the project_documents row (sole
// writer) and returns the camelCase document shape the client expects.
export async function POST(req: NextRequest) {
  const projectId = getProjectIdFromRequest(req);
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    logger.error('Document backend is not configured (missing BACKEND_URL)', { project_id: projectId, request_id: requestId, event: 'config_error' });
    return NextResponse.json(
      { error: 'Document backend is not configured', details: 'Missing BACKEND_URL' },
      { status: 503 },
    );
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const { objectKey, filename, mimeType, size, chatId } = body ?? {};

  if (!objectKey || typeof objectKey !== 'string') {
    return NextResponse.json({ error: 'objectKey is required' }, { status: 400 });
  }
  if (!filename || typeof filename !== 'string') {
    return NextResponse.json({ error: 'filename is required' }, { status: 400 });
  }
  if (!mimeType || typeof mimeType !== 'string') {
    return NextResponse.json({ error: 'mimeType is required' }, { status: 400 });
  }
  if (!size || typeof size !== 'number' || size <= 0) {
    return NextResponse.json({ error: 'size must be a positive number' }, { status: 400 });
  }

  // OCR extraction is expensive (S3 download + per-page LLM vision). Require an
  // authenticated owner of the project so it can't be triggered anonymously or
  // against another tenant's project. userId comes from the session, not the body.
  const supabase = await getSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
  if (!(await isProjectOwnedBy(supabase, projectId, user.id))) {
    return NextResponse.json({ error: 'Проект не найден или нет доступа.' }, { status: 404 });
  }

  if (!(await checkRateLimit(supabase, 'ocr', OCR_RATE_MAX, OCR_RATE_WINDOW_SECONDS))) {
    logger.warn('OCR rate limit hit', { project_id: projectId, request_id: requestId, event: 'rate_limited', user_id: user.id });
    return NextResponse.json(
      { error: 'Слишком много загрузок подряд. Немного подождите и повторите.' },
      { status: 429 },
    );
  }

  // Access gate (fail-closed): extraction is the most expensive call in the
  // system (S3 download + per-page LLM vision) — an expired/absent subscription
  // must stop here, before the backend call. requireEntitlement never throws.
  const { response: entitlementResponse } = await requireEntitlement(supabase);
  if (entitlementResponse) {
    logger.warn('Document extraction blocked by entitlement gate', { project_id: projectId, request_id: requestId, event: 'entitlement_blocked', user_id: user.id, status: entitlementResponse.status });
    return entitlementResponse;
  }

  const forwardBody = {
    objectKey,
    filename,
    mimeType,
    size,
    projectId,
    userId: user.id,
  };

  try {
    const upstream = await fetch(`${backendUrl.replace(/\/$/, '')}/documents/extract`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Backend-Secret': process.env.BACKEND_SHARED_SECRET ?? '',
        'X-Request-Id': requestId,
        // Forward the stable chat id (from the body) so the backend tags the
        // extraction trace and logs with chat_id.
        ...(typeof chatId === 'string' && chatId ? { 'X-Chat-Id': chatId } : {}),
      },
      body: JSON.stringify(forwardBody),
      // The backend bounds itself to PDF_EXTRACTION_HARD_BUDGET (1500s) + reserve
      // and returns a partial result before the container's 1800s kill. Waiting
      // LONGER than the container lives (the old 35min) only ever produced
      // orphaned requests — stay under the kill with headroom for the response.
      signal: AbortSignal.timeout(1740000),
    });

    const payload = await upstream.json().catch(() => null);

    if (!upstream.ok) {
      const rawDetail = payload && ((payload as any).detail ?? (payload as any).error);
      logger.error('Document backend returned an error', { project_id: projectId, request_id: requestId, event: 'backend_error', status: upstream.status, detail: rawDetail });
      // Preserve 4xx (e.g. 422 unreadable document) so the client shows a useful
      // message; collapse 5xx to 502.
      const status = upstream.status >= 400 && upstream.status < 500 ? upstream.status : 502;
      let error: string;
      if (upstream.status === 422) {
        error = 'Не удалось извлечь текст из документа. Попробуйте другой файл или формат.';
      } else if (typeof rawDetail === 'string' && rawDetail.trim()) {
        error = rawDetail;
      } else {
        error = 'Не удалось обработать документ.';
      }
      return NextResponse.json({ error }, { status });
    }

    logger.info('Document proxied to backend', { project_id: projectId, request_id: requestId, event: 'document_proxied', status: upstream.status, duration_ms: Date.now() - startedAt });
    return NextResponse.json(payload ?? {}, { status: upstream.status });
  } catch (error) {
    const details = error instanceof Error ? error.message : 'Unknown error';
    logger.error('Failed to reach document backend', { project_id: projectId, request_id: requestId, event: 'backend_unreachable', err: error });
    return NextResponse.json(
      { error: 'Во время обработки документа произошла ошибка. Попробуйте ещё раз позже.', details },
      { status: 502 },
    );
  }
}
