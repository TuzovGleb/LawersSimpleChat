import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { getAuthorizedChatSession } from '@/lib/chat-access';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

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
    return NextResponse.json({ error: 'sessionId and draftId are required' }, { status: 400 });
  }

  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    logger.error('Document backend is not configured (missing BACKEND_URL)', { chat_id: sessionId, request_id: requestId, event: 'config_error' });
    return NextResponse.json(
      { error: 'Document backend is not configured', details: 'Missing BACKEND_URL' },
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
      return NextResponse.json(
        { error: 'Не удалось сформировать документ.' },
        { status: upstream.status === 404 ? 404 : 502 },
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
    return NextResponse.json({ error: 'Не удалось сформировать документ.' }, { status: 502 });
  }
}
