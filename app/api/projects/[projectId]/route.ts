import { NextRequest, NextResponse } from 'next/server';
import { getSupabase } from '@/lib/supabase';
import { mapProject } from '@/lib/projects';
import { slugify } from '@/lib/utils';
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
    let query = supabase.from('projects').select('*').eq('id', projectId).limit(1);

    if (userId) {
      query = query.eq('user_id', userId);
    }

    const { data, error } = await query.single();

    if (error || !data) {
      return NextResponse.json({ error: 'Проект не найден.' }, { status: 404 });
    }

    return NextResponse.json({ project: mapProject(data) });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'project_get_unexpected_error',
      err: error,
      project_id: projectId,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function PATCH(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const projectId = getProjectIdFromRequest(req);
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  try {
    const body = await req.json();
    const name = typeof body?.name === 'string' ? body.name.trim() : '';
    const slug = typeof body?.slug === 'string' ? body.slug.trim() : '';
    const userId = typeof body?.userId === 'string' ? body.userId.trim() : null;

    if (!name && !slug) {
      return NextResponse.json({ error: 'Нет изменений для обновления.' }, { status: 400 });
    }

    const supabase = await getSupabase();

    const updates: Record<string, any> = { updated_at: new Date().toISOString() };
    if (name) {
      updates.name = name;
    }
    if (slug) {
      updates.slug = slugify(slug, slug) || slug;
    }

    let query = supabase.from('projects').update(updates).eq('id', projectId);
    if (userId) {
      query = query.eq('user_id', userId);
    }

    const { data, error } = await query.select('*').single();

    if (error || !data) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'project_patch_supabase_error',
        err: error,
        project_id: projectId,
      });
      return NextResponse.json({ error: 'Не удалось обновить проект.' }, { status: 500 });
    }

    return NextResponse.json({ project: mapProject(data) });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'project_patch_unexpected_error',
      err: error,
      project_id: projectId,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const projectId = getProjectIdFromRequest(req);
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }
  const body = await req.json().catch(() => ({}));
  const userId = typeof body?.userId === 'string' ? body.userId.trim() : null;

  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  try {
    const supabase = await getSupabase();
    let query = supabase.from('projects').delete().eq('id', projectId);

    if (userId) {
      query = query.eq('user_id', userId);
    }

    const { error } = await query;

    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'project_delete_supabase_error',
        err: error,
        project_id: projectId,
      });
      return NextResponse.json({ error: 'Не удалось удалить проект.' }, { status: 500 });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'project_delete_unexpected_error',
      err: error,
      project_id: projectId,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

