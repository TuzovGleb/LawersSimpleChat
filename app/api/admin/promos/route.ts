import { NextRequest, NextResponse } from 'next/server';
import { requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.promos.view');
  if (adminResponse) return adminResponse;

  try {
    const { data, error } = await supabase!.rpc('admin_list_promos');

    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_promos_get_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json(
        { error: 'Не удалось загрузить список промокодов.' },
        { status: 500 },
      );
    }

    const promos = Array.isArray(data) ? data : [];
    logger.info('Admin promos listed', {
      request_id: requestId,
      event: 'admin_promos_listed',
      duration_ms: Date.now() - startedAt,
      count: promos.length,
    });
    return NextResponse.json({ promos });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_promos_get_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.promos.manage');
  if (adminResponse) return adminResponse;

  try {
    const body = await req.json();
    const code = typeof body?.code === 'string' ? body.code.trim() : '';
    const days = typeof body?.days === 'number' ? body.days : Number.NaN;
    const max = body?.max === undefined || body?.max === null ? 1 : body.max;
    const expiresAt =
      typeof body?.expiresAt === 'string' && body.expiresAt.trim() ? body.expiresAt.trim() : null;
    const note = typeof body?.note === 'string' && body.note.trim() ? body.note.trim() : null;

    if (!code) {
      return NextResponse.json({ error: 'Укажите код промокода.' }, { status: 400 });
    }
    if (!Number.isInteger(days) || days <= 0) {
      return NextResponse.json(
        { error: 'Количество дней должно быть целым положительным числом.' },
        { status: 400 },
      );
    }
    if (typeof max !== 'number' || !Number.isInteger(max) || max <= 0) {
      return NextResponse.json(
        { error: 'Максимум активаций должен быть целым положительным числом.' },
        { status: 400 },
      );
    }

    const { data, error } = await supabase!.rpc('admin_create_promo', {
      p_code: code,
      p_days: days,
      p_max: max,
      p_expires_at: expiresAt,
      p_note: note,
    });

    if (error) {
      // Дубликат кода: unique violation из promo_codes.code.
      if (
        error.code === '23505' ||
        (typeof error.message === 'string' &&
          (error.message.includes('duplicate key') || error.message.includes('23505')))
      ) {
        return NextResponse.json({ error: 'Такой промокод уже существует' }, { status: 409 });
      }
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_promos_post_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось создать промокод.' }, { status: 500 });
    }

    const result = (data ?? {}) as { ok?: boolean; code?: string };
    logger.info('Admin promo created', {
      request_id: requestId,
      event: 'admin_promo_created',
      duration_ms: Date.now() - startedAt,
      days,
      max,
    });
    return NextResponse.json({ ok: true, code: result.code ?? code.toUpperCase() });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_promos_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
