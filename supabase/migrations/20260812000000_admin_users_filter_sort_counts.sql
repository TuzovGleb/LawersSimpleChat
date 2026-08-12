-- ────────────────────────────────────────────────────────────────────────────
-- admin_list_users_v2: пресет-фильтр по активности, серверная сортировка,
-- счётчики для вкладки «Пользователи» админки.
-- ────────────────────────────────────────────────────────────────────────────
--
-- Зачем v2, а не правка admin_list_users: v1 возвращает jsonb-МАССИВ, v2 —
-- jsonb-ОБЪЕКТ {users, total, counts}. Смена формы ответа сломала бы старый
-- фронт в окне деплоя «миграция → фронт» (инвариант: миграция применяется
-- руками ДО выката фронта, fail-closed). v1 оставлена нетронутой и помечена
-- deprecated — кандидат на удаление отдельной миграцией после выката.
--
-- Форма ответа:
--   {
--     "users":  [ ...те же поля-строки, что у admin_list_users... ],
--     "total":  <int>,           -- всего пользователей под текущим поиском
--     "counts": {                -- разрезы ТОГО ЖЕ поискового множества,
--       "active":   <int>,       --   считаются ДО применения p_filter —
--       "inactive": <int>,       --   иначе кнопки-фильтры в UI не могли бы
--       "trial":    <int>,       --   показывать количества других разрезов
--       "promo":    <int>,
--       "paid":     <int>
--     }
--   }
--
-- p_filter (оси НЕ ортогональны, это пресеты для кнопок):
--   all      — без фильтра
--   active   — status = 'active' (включая админов с постоянным доступом)
--   inactive — status <> 'active' (истёк, отозван или доступа не было)
--   trial    — kind = 'trial': текущий ИЛИ последний грант — триал
--              (включая истёкшие триалы; статус виден бейджем в таблице)
--   promo    — kind = 'promo': доступ по промокоду, включая истёкших
--   paid     — kind in ('manual','payment'): оплатившие, включая истёкших
--
-- trial/promo/paid закрывают ось kind целиком (CHECK в access_grants:
-- trial|promo|manual|payment); вне разрезов kind остаются только админы
-- (вычисляемый kind='admin') и пользователи вовсе без грантов (kind null).
--
-- p_sort — белый список ключей; во всех вариантах NULL-ы в конце
-- (админы с постоянным доступом и пользователи без грантов), tiebreaker
-- всегда email asc:
--   access_asc/desc     — по сроку доступа (access_until)
--   email_asc/desc
--   name_asc/desc       — по «Фамилия Имя» (без ФИО — в конце)
--   registered_asc/desc — по дате регистрации
--
-- Safe to re-run: create or replace + идемпотентные revoke/grant.

create or replace function public.admin_list_users_v2(
  p_search text default null,
  p_filter text default 'all',
  p_sort   text default 'access_asc',
  p_limit  int  default 200,
  p_offset int  default 0
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_search text := nullif(trim(coalesce(p_search, '')), '');
  v_result jsonb;
begin
  if not public.has_permission('admin.users.view') then
    raise exception 'forbidden';
  end if;
  -- Белые списки: роут валидирует раньше и отвечает 400, здесь — страховка
  -- от прямых RPC-вызовов мимо роута.
  if p_filter not in ('all', 'active', 'inactive', 'trial', 'promo', 'paid') then
    raise exception 'invalid filter';
  end if;
  if p_sort not in ('access_asc', 'access_desc', 'email_asc', 'email_desc',
                    'name_asc', 'name_desc', 'registered_asc', 'registered_desc') then
    raise exception 'invalid sort';
  end if;
  if p_limit is null or p_limit <= 0 then
    raise exception 'invalid limit';
  end if;
  if p_offset is null or p_offset < 0 then
    raise exception 'invalid offset';
  end if;

  -- base повторяет тело admin_list_users (v1) дословно: та же семантика
  -- status/kind/access_until/days_left, см. 20260709120000.
  with base as (
    select u.email,
           p.first_name,
           p.last_name,
           p.phone,
           u.created_at      as registered_at,
           u.last_sign_in_at,
           case
             when adm.is_admin          then 'active'
             when a.ends_at is not null then 'active'
             when l.ends_at is not null then 'expired'
             else 'none'
           end               as status,
           case when adm.is_admin then 'admin'
                else coalesce(a.kind, l.kind) end as kind,
           case when adm.is_admin then null
                else coalesce(a.ends_at, l.ends_at) end as access_until,
           case
             when adm.is_admin then null
             when coalesce(a.ends_at, l.ends_at) is not null
               then floor(extract(epoch from (coalesce(a.ends_at, l.ends_at) - now())) / 86400)::int
             else null
           end               as days_left,
           rl.roles
    from auth.users u
    left join public.profiles p on p.id = u.id
    -- Admin override: любой admin.* пермишен → постоянный доступ
    -- (kind='admin', без срока) — критерий тот же, что в public.is_admin().
    cross join lateral (
      select public.user_has_admin_access(u.id) as is_admin
    ) adm
    -- Назначенные RBAC-роли как [{slug,name}] для бейджей в таблице.
    left join lateral (
      select coalesce(
               jsonb_agg(
                 jsonb_build_object('slug', r.slug, 'name', r.name)
                 order by r.slug
               ),
               '[]'::jsonb
             ) as roles
      from public.user_roles ur
      join public.roles r on r.id = ur.role_id
      where ur.user_id = u.id
    ) rl on true
    -- Лучший активный грант (даёт status='active' не-админам).
    left join lateral (
      select g.kind, g.ends_at
      from public.access_grants g
      where g.user_id = u.id
        and g.revoked_at is null
        and g.starts_at <= now()
        and g.ends_at > now()
      order by g.ends_at desc
      limit 1
    ) a on true
    -- Последний грант вообще (даёт status='expired'). Эффективный срок:
    -- отозванный грант «кончается» в revoked_at, не в исходный ends_at.
    left join lateral (
      select g.kind, least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) as ends_at
      from public.access_grants g
      where g.user_id = u.id
      order by least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) desc
      limit 1
    ) l on true
    where v_search is null
       or u.email      ilike '%' || v_search || '%'
       or p.first_name ilike '%' || v_search || '%'
       or p.last_name  ilike '%' || v_search || '%'
       or p.phone      ilike '%' || v_search || '%'
  ),
  counts as (
    select count(*)::int                                                as total,
           (count(*) filter (where status = 'active'))::int             as active,
           (count(*) filter (where status <> 'active'))::int            as inactive,
           (count(*) filter (where kind = 'trial'))::int                as trial,
           (count(*) filter (where kind = 'promo'))::int                as promo,
           (count(*) filter (where kind in ('manual', 'payment')))::int as paid
    from base
  ),
  filtered as (
    select b.*
    from base b
    where case p_filter
            when 'active'   then b.status = 'active'
            when 'inactive' then b.status <> 'active'
            when 'trial'    then b.kind = 'trial'
            when 'promo'    then b.kind = 'promo'
            when 'paid'     then b.kind in ('manual', 'payment')
            else true
          end
  ),
  -- rn фиксирует порядок сортировки: и пагинация (order by rn limit/offset),
  -- и jsonb_agg (order by rn) обязаны использовать один и тот же порядок.
  -- Ветки case для «чужих» p_sort дают NULL-константу и на порядок не влияют.
  page as (
    select f.*,
           row_number() over (
             order by
               case when p_sort = 'access_asc'      then f.access_until end asc nulls last,
               case when p_sort = 'access_desc'     then f.access_until end desc nulls last,
               case when p_sort = 'email_asc'       then f.email end asc,
               case when p_sort = 'email_desc'      then f.email end desc,
               case when p_sort = 'name_asc'
                    then lower(nullif(trim(coalesce(f.last_name, '') || ' ' || coalesce(f.first_name, '')), '')) end asc nulls last,
               case when p_sort = 'name_desc'
                    then lower(nullif(trim(coalesce(f.last_name, '') || ' ' || coalesce(f.first_name, '')), '')) end desc nulls last,
               case when p_sort = 'registered_asc'  then f.registered_at end asc,
               case when p_sort = 'registered_desc' then f.registered_at end desc,
               f.email asc
           ) as rn
    from filtered f
  )
  select jsonb_build_object(
           'users',
           coalesce(
             (select jsonb_agg(
                       jsonb_build_object(
                         'email',           p2.email,
                         'first_name',      p2.first_name,
                         'last_name',       p2.last_name,
                         'phone',           p2.phone,
                         'registered_at',   p2.registered_at,
                         'last_sign_in_at', p2.last_sign_in_at,
                         'status',          p2.status,
                         'kind',            p2.kind,
                         'access_until',    p2.access_until,
                         'days_left',       p2.days_left,
                         'roles',           p2.roles
                       )
                       order by p2.rn
                     )
              from (select * from page order by rn limit p_limit offset p_offset) p2),
             '[]'::jsonb
           ),
           'total',  c.total,
           'counts', jsonb_build_object(
                       'active',   c.active,
                       'inactive', c.inactive,
                       'trial',    c.trial,
                       'promo',    c.promo,
                       'paid',     c.paid
                     )
         )
    into v_result
  from counts c;

  return v_result;
end;
$$;

-- ВАЖНО: на Supabase 'revoke ... from public' НЕ отбирает EXECUTE у anon —
-- default privileges выдают его каждой роли напрямую, поэтому revoke явный.
revoke all on function public.admin_list_users_v2(text, text, text, int, int) from public, anon;
grant execute on function public.admin_list_users_v2(text, text, text, int, int) to authenticated;

comment on function public.admin_list_users(text, int, int) is
  'DEPRECATED: заменена admin_list_users_v2 (фильтр/сортировка/счётчики). '
  'Оставлена, чтобы старый фронт работал в окне деплоя «миграция → фронт»; '
  'кандидат на удаление отдельной миграцией после выката фронта.';
