import { NextRequest, NextResponse } from 'next/server';
import { requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

function getSlugFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  // Якоримся на фиксированный префикс admin/roles: lastIndexOf('roles')
  // ломался на роли со slug'ом «roles» (/api/admin/roles/roles → null).
  const adminIndex = segments.indexOf('admin');
  if (
    adminIndex >= 0 &&
    segments[adminIndex + 1] === 'roles' &&
    segments.length > adminIndex + 2
  ) {
    try {
      return decodeURIComponent(segments[adminIndex + 2]);
    } catch {
      // Битая percent-последовательность в пути → считаем slug отсутствующим.
      return null;
    }
  }
  return null;
}

// Общий маппинг ошибок RPC admin_update_role / admin_delete_role → HTTP.
// null → ошибка не распознана (роут логирует и отдаёт 500 сам).
function mapRoleRpcError(message: unknown): NextResponse | null {
  if (typeof message !== 'string') return null;
  if (message.includes('system role')) {
    return NextResponse.json(
      { error: 'Системную роль нельзя изменить или удалить' },
      { status: 400 },
    );
  }
  if (message.includes('role not found')) {
    return NextResponse.json({ error: 'Роль не найдена' }, { status: 404 });
  }
  if (message.includes('invalid permission')) {
    return NextResponse.json({ error: 'Указан несуществующий пермишен.' }, { status: 400 });
  }
  if (message.includes('cannot remove last admin')) {
    return NextResponse.json(
      { error: 'Нельзя снять роль у последнего администратора' },
      { status: 400 },
    );
  }
  return null;
}

export async function PATCH(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  const slug = getSlugFromRequest(req);
  if (!slug) {
    return NextResponse.json({ error: 'Укажите идентификатор роли.' }, { status: 400 });
  }

  const { supabase, response: adminResponse } = await requirePermission('admin.roles.manage');
  if (adminResponse) return adminResponse;

  try {
    const body = await req.json();
    const hasName = body?.name !== undefined && body?.name !== null;
    const hasDescription = body?.description !== undefined && body?.description !== null;
    const hasPermissions = body?.permissions !== undefined && body?.permissions !== null;

    if (hasName && (typeof body.name !== 'string' || !body.name.trim())) {
      return NextResponse.json({ error: 'Название роли не может быть пустым.' }, { status: 400 });
    }
    if (hasDescription && typeof body.description !== 'string') {
      return NextResponse.json({ error: 'Некорректное описание роли.' }, { status: 400 });
    }
    if (
      hasPermissions &&
      (!Array.isArray(body.permissions) ||
        body.permissions.some((perm: unknown) => typeof perm !== 'string'))
    ) {
      return NextResponse.json({ error: 'Некорректный список пермишенов.' }, { status: 400 });
    }
    if (!hasName && !hasDescription && !hasPermissions) {
      return NextResponse.json({ error: 'Укажите, что нужно изменить.' }, { status: 400 });
    }

    // NULL в RPC = «поле не менять»; p_permissions NOT NULL = полная замена набора.
    const { error } = await supabase!.rpc('admin_update_role', {
      p_slug: slug,
      p_name: hasName ? body.name.trim() : null,
      p_description: hasDescription ? body.description.trim() : null,
      p_permissions: hasPermissions ? body.permissions : null,
    });

    if (error) {
      const mapped = mapRoleRpcError(error.message);
      if (mapped) return mapped;
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_role_patch_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось изменить роль.' }, { status: 500 });
    }

    logger.info('Admin role updated', {
      request_id: requestId,
      event: 'admin_role_updated',
      duration_ms: Date.now() - startedAt,
      slug,
      name_changed: hasName,
      permissions_changed: hasPermissions,
    });
    return NextResponse.json({ ok: true, slug });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_role_patch_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  const slug = getSlugFromRequest(req);
  if (!slug) {
    return NextResponse.json({ error: 'Укажите идентификатор роли.' }, { status: 400 });
  }

  const { supabase, response: adminResponse } = await requirePermission('admin.roles.manage');
  if (adminResponse) return adminResponse;

  try {
    const { error } = await supabase!.rpc('admin_delete_role', { p_slug: slug });

    if (error) {
      const mapped = mapRoleRpcError(error.message);
      if (mapped) return mapped;
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_role_delete_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось удалить роль.' }, { status: 500 });
    }

    logger.info('Admin role deleted', {
      request_id: requestId,
      event: 'admin_role_deleted',
      duration_ms: Date.now() - startedAt,
      slug,
    });
    return NextResponse.json({ ok: true, slug });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_role_delete_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
