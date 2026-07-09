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
    const reason =
      typeof body?.reason === 'string' && body.reason.trim() ? body.reason.trim() : null;

    if (!email) {
      return NextResponse.json({ error: 'Укажите email пользователя.' }, { status: 400 });
    }

    const { data, error } = await supabase!.rpc('admin_revoke_access', {
      p_email: email,
      p_reason: reason,
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
        event: 'admin_revoke_post_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось отозвать доступ.' }, { status: 500 });
    }

    const result = (data ?? {}) as { ok?: boolean; revoked_count?: number };
    logger.info('Admin access revoked', {
      request_id: requestId,
      event: 'admin_access_revoked',
      duration_ms: Date.now() - startedAt,
      revoked_count: result.revoked_count ?? 0,
    });
    return NextResponse.json({ ok: true, revokedCount: result.revoked_count ?? 0 });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_revoke_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
