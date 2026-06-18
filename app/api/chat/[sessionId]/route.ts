import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { getAuthorizedChatSession } from '@/lib/chat-access';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

function getSessionIdFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const chatIndex = segments.lastIndexOf('chat');
  if (chatIndex >= 0 && segments.length > chatIndex + 1) {
    return segments[chatIndex + 1];
  }
  return null;
}

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  const sessionId = getSessionIdFromRequest(req);
  if (!sessionId) {
    return NextResponse.json({ error: 'sessionId is required' }, { status: 400 });
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

    logger.info('Chat session loaded', {
      request_id: requestId,
      event: 'chat_session_loaded',
      duration_ms: Date.now() - startedAt,
      chat_id: sessionId,
    });
    return NextResponse.json({ chat: session });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'chat_get_unexpected_error',
      err: error,
      chat_id: sessionId,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
