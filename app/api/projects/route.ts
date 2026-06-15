import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { createClient } from '@/lib/supabase/server';
import { requireAuth } from '@/lib/auth-helpers';
import { mapProject } from '@/lib/projects';
import { slugify } from '@/lib/utils';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  // Check authentication
  const { user, response: authResponse } = await requireAuth();
  if (authResponse) return authResponse;

  try {
    const supabase = await createClient();
    const { data, error } = await supabase
      .from('projects')
      .select('*')
      .eq('user_id', user!.id)
      .order('updated_at', { ascending: false });

    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'projects_get_supabase_error',
        err: error,
      });
      return NextResponse.json({ error: 'Не удалось загрузить проекты.' }, { status: 500 });
    }

    const projects = (data ?? []).map(mapProject);
    logger.info('Projects listed', {
      request_id: requestId,
      event: 'projects_listed',
      duration_ms: Date.now() - startedAt,
      count: projects.length,
    });
    return NextResponse.json({ projects });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'projects_get_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  // Check authentication
  const { user, response: authResponse } = await requireAuth();
  if (authResponse) return authResponse;

  try {
    const body = await req.json();
    const name = typeof body?.name === 'string' ? body.name.trim() : '';
    const providedSlug = typeof body?.slug === 'string' ? body.slug.trim() : '';

    if (!name) {
      return NextResponse.json({ error: 'Название проекта обязательно.' }, { status: 400 });
    }

    const supabase = await createClient();
    const baseSlug = providedSlug || slugify(name);
    let slugCandidate = baseSlug || uuidv4();

    slugCandidate = await ensureProjectSlugIsUnique(supabase, slugCandidate, user!.id);

    const now = new Date().toISOString();
    const newProject = {
      id: uuidv4(),
      user_id: user!.id,
      name,
      slug: slugCandidate,
      created_at: now,
      updated_at: now,
    };

    const { data, error } = await supabase
      .from('projects')
      .insert([newProject])
      .select('*')
      .single();

    if (error || !data) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'projects_post_supabase_error',
        err: error,
      });

      // Обработка ошибки дубликата slug
      if (error?.code === '23505') {
        return NextResponse.json({ 
          error: 'Проект с таким названием уже существует. Попробуйте другое название.' 
        }, { status: 409 });
      }
      
      return NextResponse.json({ error: 'Не удалось создать проект.' }, { status: 500 });
    }

    logger.info('Project created', {
      request_id: requestId,
      event: 'project_created',
      duration_ms: Date.now() - startedAt,
      project_id: data.id,
      name,
    });
    return NextResponse.json({ project: mapProject(data) }, { status: 201 });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'projects_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

async function ensureProjectSlugIsUnique(
  supabase: Awaited<ReturnType<typeof createClient>>,
  slugCandidate: string,
  userId: string,
) {
  let candidate = slugCandidate;
  let attempts = 0;

  while (attempts < 10) {
    const { data, error } = await supabase
      .from('projects')
      .select('id')
      .eq('slug', candidate)
      .maybeSingle();

    if (error) {
      logger.warn('Supabase lookup error', {
        event: 'projects_slug_lookup_error',
        err: error,
      });
      break;
    }

    if (!data) {
      return candidate;
    }

    attempts += 1;
    candidate = `${slugCandidate}-${attempts}`;
  }

  // Если не нашли уникальный после 10 попыток, добавляем UUID
  return `${slugCandidate}-${uuidv4().slice(0, 8)}`;
}

