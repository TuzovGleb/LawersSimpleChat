import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { getAuthorizedChatSession } from '@/lib/chat-access';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

// NextResponse.json ставит Content-Type без charset. Пока тело читает fetch, это
// незаметно, но ссылка на скачивание уводит вкладку браузера на этот URL, и при
// ошибке Safari разбирает русский текст как Windows-1251 — юрист видел
// «РќРµ СѓРґР°Р»РѕСЃСЊ...». Кодировку задаём явно.
function jsonError(body: Record<string, unknown>, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

// GET /api/chat/{sessionId}/documents/{draftId}
// Render-on-demand: the backend rebuilds the .docx from the blocks stored in the
// draft tool's tool_state. We authorize the user here and proxy the binary.
export async function GET(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const chatIndex = segments.lastIndexOf('chat');
  const sessionId = chatIndex >= 0 ? segments[chatIndex + 1] : null;
  const draftId = segments[segments.length - 1];
  const requestId = requestIdFrom(req);

  if (!sessionId || !draftId) {
    return jsonError({ error: 'sessionId and draftId are required' }, 400);
  }

  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    logger.error('Document backend is not configured (missing BACKEND_URL)', { chat_id: sessionId, request_id: requestId, event: 'config_error' });
    return jsonError(
      { error: 'Document backend is not configured', details: 'Missing BACKEND_URL' },
      503,
    );
  }

  try {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return jsonError({ error: 'Unauthorized' }, 401);
    }
    const session = await getAuthorizedChatSession(supabase, sessionId, user.id);
    if (!session) {
      return jsonError({ error: 'Чат не найден или нет доступа.' }, 404);
    }
  } catch (error) {
    logger.error('Auth check failed', { chat_id: sessionId, request_id: requestId, event: 'auth_failed', err: error });
    return jsonError({ error: 'Unauthorized' }, 401);
  }

  try {
    const upstream = await fetch(
      `${backendUrl.replace(/\/$/, '')}/chats/${encodeURIComponent(sessionId)}/documents/${encodeURIComponent(draftId)}`,
      {
        method: 'GET',
        headers: {
          'X-Backend-Secret': process.env.BACKEND_SHARED_SECRET ?? '',
          'X-Chat-Id': sessionId,
          'X-Request-Id': requestId,
        },
        signal: AbortSignal.timeout(60000),
      },
    );

    if (!upstream.ok || !upstream.body) {
      // Log upstream body server-side only; never echo it to the client.
      const details = await upstream.text().catch(() => '');
      logger.error('Document backend returned an error', { chat_id: sessionId, request_id: requestId, event: 'backend_error', status: upstream.status, details });
      // 404 — документ не сохранён, повтор бесполезен. 503 — хранилище
      // недоступно, повтор осмыслен. Схлопывать их в один код нельзя: клиент
      // по этому различию выбирает, что предложить юристу.
      const status =
        upstream.status === 404 ? 404 : upstream.status === 503 ? 503 : 502;
      return jsonError(
        {
          error:
            status === 503
              ? 'Хранилище временно недоступно. Попробуйте ещё раз.'
              : 'Не удалось сформировать документ.',
        },
        status,
      );
    }

    const headers = new Headers();
    headers.set(
      'Content-Type',
      upstream.headers.get('Content-Type') ??
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    );
    const disposition = upstream.headers.get('Content-Disposition');
    if (disposition) headers.set('Content-Disposition', disposition);
    return new Response(upstream.body, { headers });
  } catch (error) {
    logger.error('Failed to reach document backend', { chat_id: sessionId, request_id: requestId, event: 'backend_unreachable', err: error });
    return jsonError({ error: 'Не удалось сформировать документ.' }, 502);
  }
}
