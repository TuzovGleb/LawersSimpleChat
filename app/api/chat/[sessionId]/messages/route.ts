import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { getAuthorizedChatSession } from '@/lib/chat-access';
import type { UTMData } from '@/lib/types';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache',
  Connection: 'keep-alive',
  'X-Accel-Buffering': 'no',
} as const;

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
      const details = await upstream.text().catch(() => '');
      logger.error('Chat backend returned an error loading messages', { chat_id: sessionId, request_id: requestId, event: 'backend_error', status: upstream.status, details });
      return NextResponse.json(
        { error: 'Не удалось загрузить сообщения.', details: details || `HTTP ${upstream.status}` },
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
  } catch (error) {
    logger.error('Auth check failed', { chat_id: sessionId, request_id: requestId, event: 'auth_failed', err: error });
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const utm = extractUtm(new URL(req.url));
  const { sessionId: _ignored, ...rest } = body;
  const forwardBody = { ...rest, userId, utm };

  try {
    const upstream = await fetch(
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
      },
    );

    if (!upstream.ok || !upstream.body) {
      const details = await upstream.text().catch(() => '');
      logger.error('Chat backend returned an error', { chat_id: sessionId, request_id: requestId, event: 'backend_error', status: upstream.status, details });
      return NextResponse.json(
        { error: 'Backend error', details: details || `HTTP ${upstream.status}` },
        { status: 502 },
      );
    }

    logger.info('Chat message proxied to backend', { chat_id: sessionId, request_id: requestId, event: 'chat_proxied', status: upstream.status, duration_ms: Date.now() - startedAt });
    return new Response(upstream.body, { headers: SSE_HEADERS });
  } catch (error) {
    const details = error instanceof Error ? error.message : 'Unknown error';
    logger.error('Failed to reach chat backend', { chat_id: sessionId, request_id: requestId, event: 'backend_unreachable', err: error });
    return NextResponse.json({ error: 'Failed to reach chat backend', details }, { status: 502 });
  }
}
