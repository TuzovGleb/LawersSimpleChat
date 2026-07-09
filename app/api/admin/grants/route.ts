import { NextRequest, NextResponse } from 'next/server';
import { requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.access.manage');
  if (adminResponse) return adminResponse;

  try {
    const body = await req.json();
    const email = typeof body?.email === 'string' ? body.email.trim() : '';
    const days = typeof body?.days === 'number' ? body.days : Number.NaN;
    const kind = typeof body?.kind === 'string' && body.kind ? body.kind : 'manual';
    const note = typeof body?.note === 'string' && body.note.trim() ? body.note.trim() : null;

    if (!email) {
      return NextResponse.json({ error: 'Укажите email пользователя.' }, { status: 400 });
    }
    if (!Number.isInteger(days) || days <= 0) {
      return NextResponse.json(
        { error: 'Количество дней должно быть целым положительным числом.' },
        { status: 400 },
      );
    }
    if (kind !== 'trial' && kind !== 'manual') {
      return NextResponse.json({ error: 'Недопустимый тип доступа.' }, { status: 400 });
    }

    const { data, error } = await supabase!.rpc('admin_grant_access', {
      p_email: email,
      p_days: days,
      p_kind: kind,
      p_note: note,
    });

    if (error) {
      if (typeof error.message === 'string' && error.message.includes('user not found')) {
        return NextResponse.json(
          { error: 'Пользователь с таким email не найден' },
          { status: 404 },
        );
      }
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_grants_post_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось выдать доступ.' }, { status: 500 });
    }

    const result = (data ?? {}) as { ok?: boolean; expires_at?: string | null };
    logger.info('Admin grant created', {
      request_id: requestId,
      event: 'admin_grant_created',
      duration_ms: Date.now() - startedAt,
      kind,
      days,
    });
    return NextResponse.json({ ok: true, expiresAt: result.expires_at ?? null });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_grants_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
