import { NextRequest, NextResponse } from 'next/server';
import { Agent, fetch as undiciFetch } from 'undici';
import { createClient } from '@/lib/supabase/server';
import { getAuthorizedChatSession } from '@/lib/chat-access';
import { checkRateLimit } from '@/lib/rate-limit';
import { requireEntitlement } from '@/lib/entitlement';
import type { UTMData } from '@/lib/types';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

// Per-user chat send limit: generous for a human, but caps scripted hammering
// that would burn LLM tokens. Enforced in Postgres (see lib/rate-limit.ts).
const CHAT_RATE_MAX = 30;
const CHAT_RATE_WINDOW_SECONDS = 60;

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache',
  Connection: 'keep-alive',
  'X-Accel-Buffering': 'no',
} as const;

// Yandex Serverless buffers the backend's entire SSE body, so response HEADERS
// only reach us when the whole turn finishes. Node's built-in fetch (undici)
// applies a 300s headersTimeout by default — which killed every turn longer
// than 5 minutes with "TypeError: fetch failed" (AbortSignal.timeout below does
// NOT override it). Use undici's own fetch with limits sized to the backend
// container's 1800s execution timeout.
const chatBackendAgent = new Agent({
  headersTimeout: 1_900_000,
  bodyTimeout: 1_900_000,
});

function getSessionIdFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const chatIndex = segments.lastIndexOf('chat');
  if (chatIndex >= 0 && segments.length > chatIndex + 1) {
    return segments[chatIndex + 1];
  }
  return null;
}

function extractUtm(url: URL): UTMData {
  const utm: UTMData = {};
  url.searchParams.forEach((value, key) => {
    if (key.startsWith('utm_')) {
      utm[key as keyof UTMData] = value;
    }
  });
  return utm;
}

// Reload path. The backend owns message serialization (one source of truth:
// user-facing filtering, attached-document enrichment, and draft-document
// artifacts). We authorize the user here, then proxy to the backend's
// GET /chats/{id}/messages.
export async function GET(req: NextRequest) {
  const sessionId = getSessionIdFromRequest(req);
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  if (!sessionId) {
    return NextResponse.json({ error: 'sessionId is required' }, { status: 400 });
  }

  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    logger.error('Chat backend is not configured (missing BACKEND_URL)', { chat_id: sessionId, request_id: requestId, event: 'config_error' });
    return NextResponse.json(
      { error: 'Chat backend is not configured', details: 'Missing BACKEND_URL' },
      { status: 503 },
    );
  }

  try {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    const session = await getAuthorizedChatSession(supabase, sessionId, user.id);
    if (!session) {
      return NextResponse.json({ error: 'Чат не найден или нет доступа.' }, { status: 404 });
    }
  } catch (error) {
    logger.error('Auth check failed', { chat_id: sessionId, request_id: requestId, event: 'auth_failed', err: error });
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  try {
    const upstream = await fetch(
      `${backendUrl.replace(/\/$/, '')}/chats/${encodeURIComponent(sessionId)}/messages`,
      {
        method: 'GET',
        headers: {
          'X-Backend-Secret': process.env.BACKEND_SHARED_SECRET ?? '',
          'X-Chat-Id': sessionId,
          'X-Request-Id': requestId,
        },
        signal: AbortSignal.timeout(30000),
      },
    );

    if (!upstream.ok) {
      // Log the upstream body server-side for diagnosis, but never echo it to the
      // client — it can carry model ids / provider details (prompt.py [12]).
      const details = await upstream.text().catch(() => '');
      logger.error('Chat backend returned an error loading messages', { chat_id: sessionId, request_id: requestId, event: 'backend_error', status: upstream.status, details });
      return NextResponse.json(
        { error: 'Не удалось загрузить сообщения.' },
        { status: 502 },
      );
    }

    const data = await upstream.json();
    logger.info('Chat history loaded via backend', { chat_id: sessionId, request_id: requestId, event: 'history_loaded', message_count: Array.isArray(data?.messages) ? data.messages.length : 0, duration_ms: Date.now() - startedAt });
    return NextResponse.json(data);
  } catch (error) {
    logger.error('Failed to reach chat backend for messages', { chat_id: sessionId, request_id: requestId, event: 'backend_unreachable', err: error });
    return NextResponse.json({ error: 'Не удалось загрузить сообщения.' }, { status: 502 });
  }
}

export async function POST(req: NextRequest) {
  const sessionId = getSessionIdFromRequest(req);
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  if (!sessionId) {
    return NextResponse.json({ error: 'sessionId is required' }, { status: 400 });
  }

  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    logger.error('Chat backend is not configured (missing BACKEND_URL)', { chat_id: sessionId, request_id: requestId, event: 'config_error' });
    return NextResponse.json(
      { error: 'Chat backend is not configured', details: 'Missing BACKEND_URL' },
      { status: 503 },
    );
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  let userId: string | undefined;
  try {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    userId = user.id;

    // Defense in depth: if this session already exists it must belong to the
    // caller (a brand-new session won't exist yet — the backend creates it, so
    // that case is allowed). The backend also enforces this authoritatively via
    // the service role, which is the guard that survives RLS being enabled.
    const { data: existing } = await supabase
      .from('chat_sessions')
      .select('id')
      .eq('id', sessionId)
      .maybeSingle();
    if (existing && !(await getAuthorizedChatSession(supabase, sessionId, user.id))) {
      return NextResponse.json({ error: 'Чат не найден или нет доступа.' }, { status: 404 });
    }

    if (!(await checkRateLimit(supabase, 'chat', CHAT_RATE_MAX, CHAT_RATE_WINDOW_SECONDS))) {
      logger.warn('Chat rate limit hit', { chat_id: sessionId, request_id: requestId, event: 'rate_limited', user_id: user.id });
      return NextResponse.json(
        { error: 'Слишком много сообщений подряд. Немного подождите и повторите.' },
        { status: 429 },
      );
    }

    // Access gate (fail-closed): sending a message burns LLM tokens, so an
    // expired/absent subscription must stop here, before the backend call.
    // requireEntitlement never throws — 402/503 come back as a response.
    const { response: entitlementResponse } = await requireEntitlement(supabase);
    if (entitlementResponse) {
      logger.warn('Chat message blocked by entitlement gate', { chat_id: sessionId, request_id: requestId, event: 'entitlement_blocked', user_id: user.id, status: entitlementResponse.status });
      return entitlementResponse;
    }
  } catch (error) {
    logger.error('Auth check failed', { chat_id: sessionId, request_id: requestId, event: 'auth_failed', err: error });
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const utm = extractUtm(new URL(req.url));
  const { sessionId: _ignored, ...rest } = body;
  const forwardBody = { ...rest, userId, utm };

  try {
    const upstream = await undiciFetch(
      `${backendUrl.replace(/\/$/, '')}/chats/${encodeURIComponent(sessionId)}/messages`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Backend-Secret': process.env.BACKEND_SHARED_SECRET ?? '',
          'X-Chat-Id': sessionId,
          'X-Request-Id': requestId,
        },
        body: JSON.stringify(forwardBody),
        signal: AbortSignal.timeout(2100000),
        dispatcher: chatBackendAgent,
      },
    );

    if (!upstream.ok || !upstream.body) {
      // Log upstream body server-side only; do not return it (may contain model/
      // provider identifiers from the fallback chain — prompt.py [12]).
      const details = await upstream.text().catch(() => '');
      logger.error('Chat backend returned an error', { chat_id: sessionId, request_id: requestId, event: 'backend_error', status: upstream.status, details });
      return NextResponse.json(
        { error: 'Не удалось получить ответ. Попробуйте ещё раз.' },
        { status: 502 },
      );
    }

    logger.info('Chat message proxied to backend', { chat_id: sessionId, request_id: requestId, event: 'chat_proxied', status: upstream.status, duration_ms: Date.now() - startedAt });
    return new Response(upstream.body as unknown as ReadableStream, { headers: SSE_HEADERS });
  } catch (error) {
    logger.error('Failed to reach chat backend', { chat_id: sessionId, request_id: requestId, event: 'backend_unreachable', err: error });
    return NextResponse.json({ error: 'Не удалось получить ответ. Попробуйте ещё раз.' }, { status: 502 });
  }
}
