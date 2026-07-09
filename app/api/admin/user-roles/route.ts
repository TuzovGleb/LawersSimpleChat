import { NextRequest, NextResponse } from 'next/server';
import { requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.roles.manage');
  if (adminResponse) return adminResponse;

  try {
    const body = await req.json();
    const email = typeof body?.email === 'string' ? body.email.trim() : '';
    const roleSlug = typeof body?.roleSlug === 'string' ? body.roleSlug.trim() : '';
    const action = typeof body?.action === 'string' ? body.action : '';

    if (!email) {
      return NextResponse.json({ error: 'Укажите email пользователя.' }, { status: 400 });
    }
    if (!roleSlug) {
      return NextResponse.json({ error: 'Укажите идентификатор роли.' }, { status: 400 });
    }
    if (action !== 'assign' && action !== 'revoke') {
      return NextResponse.json({ error: 'Недопустимое действие.' }, { status: 400 });
    }

    const rpcName = action === 'assign' ? 'admin_assign_role' : 'admin_revoke_role';
    const { error } = await supabase!.rpc(rpcName, {
      p_email: email,
      p_role_slug: roleSlug,
    });

    if (error) {
      if (typeof error.message === 'string') {
        if (error.message.includes('user not found')) {
          return NextResponse.json(
            { error: 'Пользователь с таким email не найден' },
            { status: 404 },
          );
        }
        if (error.message.includes('role not found')) {
          return NextResponse.json({ error: 'Роль не найдена' }, { status: 404 });
        }
        if (error.message.includes('cannot change own roles')) {
          return NextResponse.json(
            { error: 'Нельзя изменять собственные роли' },
            { status: 400 },
          );
        }
        if (error.message.includes('cannot remove last admin')) {
          return NextResponse.json(
            { error: 'Нельзя снять роль у последнего администратора' },
            { status: 400 },
          );
        }
      }
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_user_roles_post_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json(
        { error: 'Не удалось изменить роли пользователя.' },
        { status: 500 },
      );
    }

    logger.info('Admin user role changed', {
      request_id: requestId,
      event: 'admin_user_role_changed',
      duration_ms: Date.now() - startedAt,
      action,
      role_slug: roleSlug,
    });
    return NextResponse.json({ ok: true, action, roleSlug });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_user_roles_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
