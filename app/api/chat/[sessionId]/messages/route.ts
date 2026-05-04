import { NextRequest, NextResponse } from 'next/server';
import { getSupabase } from '@/lib/supabase';

export const runtime = 'edge';

function getSessionIdFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const chatIndex = segments.lastIndexOf('chat');
  if (chatIndex >= 0 && segments.length > chatIndex + 1) {
    return segments[chatIndex + 1];
  }
  return null;
}

export async function GET(req: NextRequest) {
  const sessionId = getSessionIdFromRequest(req);
  
  if (!sessionId) {
    return NextResponse.json({ error: 'sessionId is required' }, { status: 400 });
  }

  try {
    const supabase = await getSupabase();

    // Fetch all messages for this session
    const { data, error } = await supabase
      .from('chat_messages')
      .select('*')
      .eq('session_id', sessionId)
      .order('created_at', { ascending: true });

    if (error) {
      console.error('[chat-messages][GET] Supabase error:', error);
      return NextResponse.json({ error: 'Не удалось загрузить сообщения.' }, { status: 500 });
    }

    const messages = data ?? [];
    const attachedIds = Array.from(
      new Set(
        messages.flatMap((message: any) =>
          Array.isArray(message.attached_document_ids) ? message.attached_document_ids : [],
        ),
      ),
    ).filter((id): id is string => typeof id === 'string' && id.trim().length > 0);

    let documentsById = new Map<string, { id: string; name: string; mimeType: string; size: number }>();
    if (attachedIds.length > 0) {
      const { data: sessionRow, error: sessionError } = await supabase
        .from('chat_sessions')
        .select('project_id')
        .eq('id', sessionId)
        .maybeSingle();

      if (sessionError) {
        console.error('[chat-messages][GET] Session lookup error:', sessionError);
      }

      if (sessionRow?.project_id) {
        const { data: documentsData, error: documentsError } = await supabase
          .from('project_documents')
          .select('id, name, mime_type, size')
          .eq('project_id', sessionRow.project_id)
          .in('id', attachedIds);

        if (documentsError) {
          console.error('[chat-messages][GET] Attached documents lookup error:', documentsError);
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
    console.error('[chat-messages][GET] Unexpected error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

