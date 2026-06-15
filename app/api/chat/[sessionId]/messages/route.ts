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

export async function GET(req: NextRequest) {
  const sessionId = getSessionIdFromRequest(req);
  const requestId = requestIdFrom(req);

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

    const { data, error } = await supabase
      .from('chat_messages')
      .select('*')
      .eq('session_id', sessionId)
      .order('seq', { ascending: true })
      .order('created_at', { ascending: true });

    if (error) {
      logger.error('Failed to load chat messages', { chat_id: sessionId, request_id: requestId, event: 'supabase_error', err: error });
      return NextResponse.json({ error: 'Не удалось загрузить сообщения.' }, { status: 500 });
    }

    // Show only user-facing rows. The chat log also stores tool rows and
    // intermediate assistant messages (those carrying tool_calls) for the LLM
    // context; those are not rendered in the UI.
    const messages = (data ?? []).filter(
      (m: any) => m.role === 'user' || (m.role === 'assistant' && !m.tool_calls),
    );
    const attachedIds = Array.from(
      new Set(
        messages.flatMap((message: any) =>
          Array.isArray(message.attached_document_ids) ? message.attached_document_ids : [],
        ),
      ),
    ).filter((id): id is string => typeof id === 'string' && id.trim().length > 0);

    let documentsById = new Map<string, { id: string; name: string; mimeType: string; size: number }>();
    if (attachedIds.length > 0) {
      if (session.project_id) {
        const { data: documentsData, error: documentsError } = await supabase
          .from('project_documents')
          .select('id, name, mime_type, size')
          .eq('project_id', session.project_id)
          .in('id', attachedIds);

        if (documentsError) {
          logger.error('Attached documents lookup failed', { chat_id: sessionId, request_id: requestId, event: 'documents_lookup_error', err: documentsError });
        } else {
          documentsById = new Map(
            (documentsData ?? []).map((document: any) => [
              document.id,
              {
                id: document.id,
                name: document.name,
                mimeType: document.mime_type,
                size: Number(document.size) || 0,
              },
            ]),
          );
        }
      }
    }

    return NextResponse.json({
      messages: messages.map((message: any) => {
        const messageAttachedIds = Array.isArray(message.attached_document_ids)
          ? message.attached_document_ids.filter((id: unknown): id is string => typeof id === 'string')
          : [];

        return {
          ...message,
          attachedDocumentIds: messageAttachedIds,
          attachedDocuments: messageAttachedIds
            .map((id: string) => documentsById.get(id))
            .filter(Boolean),
        };
      }),
    });
  } catch (error) {
    logger.error('Unexpected error loading chat messages', { chat_id: sessionId, request_id: requestId, event: 'unexpected_error', err: error });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const sessionId = getSessionIdFromRequest(req);
  const requestId = requestIdFrom(req);
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

    return new Response(upstream.body, { headers: SSE_HEADERS });
  } catch (error) {
    const details = error instanceof Error ? error.message : 'Unknown error';
    logger.error('Failed to reach chat backend', { chat_id: sessionId, request_id: requestId, event: 'backend_unreachable', err: error });
    return NextResponse.json({ error: 'Failed to reach chat backend', details }, { status: 502 });
  }
}
