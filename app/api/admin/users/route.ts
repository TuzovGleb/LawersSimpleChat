import { NextRequest, NextResponse } from 'next/server';
import { requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

// Белые списки зеркалят проверки внутри admin_list_users_v2: роут отвечает
// понятным 400, до RPC доходят только валидные значения.
const USER_FILTERS = new Set(['all', 'active', 'inactive', 'trial', 'promo', 'paid']);
const USER_SORTS = new Set([
  'access_asc',
  'access_desc',
  'email_asc',
  'email_desc',
  'name_asc',
  'name_desc',
  'registered_asc',
  'registered_desc',
]);

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.users.view');
  if (adminResponse) return adminResponse;

  try {
    const { searchParams } = new URL(req.url);
    const searchRaw = searchParams.get('search');
    const limitRaw = searchParams.get('limit');
    const offsetRaw = searchParams.get('offset');
    const filter = searchParams.get('filter') ?? 'all';
    const sort = searchParams.get('sort') ?? 'access_asc';

    const search = searchRaw && searchRaw.trim() ? searchRaw.trim() : null;
    const limit = limitRaw !== null ? Number.parseInt(limitRaw, 10) : 200;
    const offset = offsetRaw !== null ? Number.parseInt(offsetRaw, 10) : 0;

    if (!Number.isInteger(limit) || limit <= 0) {
      return NextResponse.json({ error: 'Некорректный параметр limit.' }, { status: 400 });
    }
    if (!Number.isInteger(offset) || offset < 0) {
      return NextResponse.json({ error: 'Некорректный параметр offset.' }, { status: 400 });
    }
    if (!USER_FILTERS.has(filter)) {
      return NextResponse.json({ error: 'Некорректный параметр filter.' }, { status: 400 });
    }
    if (!USER_SORTS.has(sort)) {
      return NextResponse.json({ error: 'Некорректный параметр sort.' }, { status: 400 });
    }

    const { data, error } = await supabase!.rpc('admin_list_users_v2', {
      p_search: search,
      p_filter: filter,
      p_sort: sort,
      p_limit: limit,
      p_offset: offset,
    });

    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_users_get_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json(
        { error: 'Не удалось загрузить список пользователей.' },
        { status: 500 },
      );
    }

    // RPC возвращает объект {users, total, counts} — прокидываем как есть,
    // с защитой формы на случай расхождения версий функции и роута.
    const users = Array.isArray(data?.users) ? data.users : [];
    const total = typeof data?.total === 'number' ? data.total : users.length;
    const counts =
      data?.counts && typeof data.counts === 'object' && !Array.isArray(data.counts)
        ? data.counts
        : null;

    logger.info('Admin users listed', {
      request_id: requestId,
      event: 'admin_users_listed',
      duration_ms: Date.now() - startedAt,
      count: users.length,
      total,
      filter,
      sort,
    });
    return NextResponse.json({ users, total, counts });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_users_get_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
