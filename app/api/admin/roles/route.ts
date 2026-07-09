import { NextRequest, NextResponse } from 'next/server';
import { requireAdmin, requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

// Slug роли: тот же формат, что CHECK-констрейнт roles.slug в миграции.
const ROLE_SLUG_RE = /^[a-z0-9_]{2,40}$/;

// Форма jsonb из admin_list_roles / admin_create_role.
interface RawRole {
  id?: string;
  slug?: string;
  name?: string;
  description?: string | null;
  is_system?: boolean;
  permissions?: string[];
  users_count?: number;
}

// Маппинг snake_case (SQL) → camelCase (клиент).
function toClientRole(raw: RawRole) {
  return {
    id: raw.id ?? null,
    slug: raw.slug ?? '',
    name: raw.name ?? '',
    description: raw.description ?? null,
    isSystem: raw.is_system === true,
    permissions: Array.isArray(raw.permissions) ? raw.permissions : [],
    usersCount: typeof raw.users_count === 'number' ? raw.users_count : 0,
  };
}

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  // Панельный доступ: список ролей и каталог пермишенов нужны любому админу
  // (в т.ч. рид-онли — для отображения бейджей ролей).
  const { supabase, response: adminResponse } = await requireAdmin();
  if (adminResponse) return adminResponse;

  try {
    const [rolesResult, permissionsResult] = await Promise.all([
      supabase!.rpc('admin_list_roles'),
      supabase!.rpc('admin_list_permissions'),
    ]);

    const error = rolesResult.error ?? permissionsResult.error;
    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_roles_get_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось загрузить список ролей.' }, { status: 500 });
    }

    const roles = (Array.isArray(rolesResult.data) ? rolesResult.data : []).map(
      (raw: RawRole) => toClientRole(raw),
    );
    const permissions = Array.isArray(permissionsResult.data) ? permissionsResult.data : [];

    logger.info('Admin roles listed', {
      request_id: requestId,
      event: 'admin_roles_listed',
      duration_ms: Date.now() - startedAt,
      roles_count: roles.length,
      permissions_count: permissions.length,
    });
    return NextResponse.json({ roles, permissions });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_roles_get_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.roles.manage');
  if (adminResponse) return adminResponse;

  try {
    const body = await req.json();
    const slug = typeof body?.slug === 'string' ? body.slug.trim() : '';
    const name = typeof body?.name === 'string' ? body.name.trim() : '';
    const description =
      typeof body?.description === 'string' && body.description.trim()
        ? body.description.trim()
        : null;
    const permissions = body?.permissions === undefined ? [] : body.permissions;

    if (!ROLE_SLUG_RE.test(slug)) {
      return NextResponse.json(
        { error: 'Идентификатор роли: 2–40 символов, только a-z, 0-9 и «_».' },
        { status: 400 },
      );
    }
    if (!name) {
      return NextResponse.json({ error: 'Укажите название роли.' }, { status: 400 });
    }
    if (
      !Array.isArray(permissions) ||
      permissions.some((perm: unknown) => typeof perm !== 'string')
    ) {
      return NextResponse.json({ error: 'Некорректный список пермишенов.' }, { status: 400 });
    }

    const { data, error } = await supabase!.rpc('admin_create_role', {
      p_slug: slug,
      p_name: name,
      p_description: description,
      p_permissions: permissions,
    });

    if (error) {
      // Дубль slug: unique violation из roles.slug.
      if (
        error.code === '23505' ||
        (typeof error.message === 'string' &&
          (error.message.includes('duplicate key') || error.message.includes('23505')))
      ) {
        return NextResponse.json(
          { error: 'Роль с таким идентификатором уже существует' },
          { status: 409 },
        );
      }
      if (typeof error.message === 'string' && error.message.includes('invalid permission')) {
        return NextResponse.json({ error: 'Указан несуществующий пермишен.' }, { status: 400 });
      }
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_roles_post_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось создать роль.' }, { status: 500 });
    }

    logger.info('Admin role created', {
      request_id: requestId,
      event: 'admin_role_created',
      duration_ms: Date.now() - startedAt,
      slug,
      permissions_count: permissions.length,
    });
    // admin_create_role возвращает обёртку { ok, role: {...} } — маппим
    // вложенный объект роли, а не саму обёртку.
    const raw = ((data as { role?: RawRole } | null)?.role ?? null) as RawRole | null;
    return NextResponse.json({ ok: true, role: raw ? toClientRole(raw) : null });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_roles_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
