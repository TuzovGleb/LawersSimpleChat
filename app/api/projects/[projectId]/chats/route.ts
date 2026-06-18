import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { getSupabase } from '@/lib/supabase';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

function getProjectIdFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const projectsIndex = segments.lastIndexOf('projects');
  if (projectsIndex >= 0 && segments.length > projectsIndex + 1) {
    return segments[projectsIndex + 1];
  }
  return null;
}

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  const projectId = getProjectIdFromRequest(req);
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }
  const { searchParams } = new URL(req.url);
  const userId = searchParams.get('userId');

  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  try {
    const supabase = await getSupabase();

    if (userId) {
    const { data: project, error: projectError } = await supabase
      .from('projects')
        .select('id')
        .eq('id', projectId)
        .eq('user_id', userId)
        .maybeSingle();

      if (projectError || !project) {
        return NextResponse.json({ error: 'Проект не найден или нет доступа.' }, { status: 404 });
      }
    }

    const { data, error } = await supabase
      .from('chat_sessions')
      .select('*')
      .eq('project_id', projectId)
      .order('created_at', { ascending: false });

    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'project_chats_get_supabase_error',
        err: error,
        project_id: projectId,
      });
      return NextResponse.json({ error: 'Не удалось загрузить чаты проекта.' }, { status: 500 });
    }

    logger.info('Project chats listed', {
      request_id: requestId,
      event: 'chats_listed',
      duration_ms: Date.now() - startedAt,
      project_id: projectId,
      count: (data ?? []).length,
    });
    return NextResponse.json({ chats: data ?? [] });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'project_chats_get_unexpected_error',
      err: error,
      project_id: projectId,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  const projectId = getProjectIdFromRequest(req);
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  try {
    const body = await req.json();
    const userId = typeof body?.userId === 'string' ? body.userId.trim() : null;
    const initialMessage =
      typeof body?.initialMessage === 'string' && body.initialMessage.trim()
        ? body.initialMessage.trim()
        : 'Новый чат';

    const supabase = await getSupabase();

    if (userId) {
      const { data: project, error: projectError } = await supabase
        .from('projects')
        .select('id')
        .eq('id', projectId)
        .eq('user_id', userId)
        .maybeSingle();

      if (projectError || !project) {
        return NextResponse.json({ error: 'Проект не найден или нет доступа.' }, { status: 404 });
      }
    }

    const now = new Date().toISOString();
    const newSessionId = uuidv4();

    const newChatSession = {
      id: newSessionId,
      user_id: userId ?? null,
      project_id: projectId,
      initial_message: initialMessage,
      created_at: now,
      utm: null,
    };

    const { data, error } = await supabase
      .from('chat_sessions')
      .insert([newChatSession])
      .select('*')
      .single();

    if (error || !data) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'project_chats_post_supabase_error',
        err: error,
        project_id: projectId,
      });
      return NextResponse.json({ error: 'Не удалось создать чат.' }, { status: 500 });
    }

    await supabase
      .from('projects')
      .update({ updated_at: now })
      .eq('id', projectId)
      .limit(1);

    logger.info('Chat created', {
      request_id: requestId,
      event: 'chat_created',
      duration_ms: Date.now() - startedAt,
      project_id: projectId,
      chat_id: newSessionId,
    });
    return NextResponse.json({ chat: data }, { status: 201 });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'project_chats_post_unexpected_error',
      err: error,
      project_id: projectId,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

