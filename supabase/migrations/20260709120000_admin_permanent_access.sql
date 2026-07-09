-- Admin permanent access.
--
-- CONTRACT RULE (invariant): a user holding AT LEAST ONE admin.* permission
-- (the same criterion as public.is_admin()) is ALWAYS entitled:
--   {"status":"active","kind":"admin","expires_at":null}
-- regardless of anything in access_grants (expired / revoked / no grants at
-- all) — admins must never lock themselves out of the product they
-- administer. Regular users are computed exactly as before: their code paths
-- below are VERBATIM copies from 20260709000000_billing_and_profiles.sql.
--
-- The base migration 20260709000000_billing_and_profiles.sql is ALREADY
-- APPLIED on prod and MUST NOT be edited — this file applies strictly AFTER
-- it and `create or replace`s on top:
--   * NEW internal helper user_has_admin_access(uuid) — the per-user variant
--     of is_admin() (which only checks the CALLING user); not callable via
--     RPC;
--   * get_entitlement(uuid) / get_my_entitlement() — admin override FIRST,
--     the rest of the body unchanged;
--   * admin_list_users(...) and the Studio views admin_user_overview /
--     admin_expiring_soon — an admin is listed as active with permanent
--     access (kind='admin', access_until/days_left = NULL) instead of
--     trial/expired, and admin_expiring_soon (the "call to renew" list)
--     never includes admins.
--
-- NOTE: kind='admin' is COMPUTED ONLY — it is never stored in
-- access_grants.kind (the CHECK there still allows only
-- trial/promo/manual/payment). The TS union in lib/entitlement.ts mirrors
-- this with the extra 'admin' member.
--
-- Safe to re-run: create or replace function/view + idempotent revoke/grant.

-- ────────────────────────────────────────────────────────────────────────────
-- Internal helper
-- ────────────────────────────────────────────────────────────────────────────

-- Does the GIVEN user hold at least one admin.* permission through any role?
-- Same criterion as public.is_admin(), but for an ARBITRARY user id — needed
-- because entitlement is computed both for the caller (get_my_entitlement)
-- and for arbitrary users (get_entitlement, admin_list_users, the views).
-- INTERNAL: like rbac_manage_holders_exist, never callable via RPC — a
-- definer function taking a user id must not be reachable with a user JWT.
-- `language sql` + STABLE on purpose: the function is invoked once per
-- auth.users row in admin_list_users / admin_user_overview /
-- admin_expiring_soon — a plpgsql VOLATILE-by-default wrapper would add
-- interpreter overhead and forbid the planner from treating the result as
-- stable within the statement. (Full inlining is still off the table because
-- of SECURITY DEFINER + SET search_path, which we keep for safety.)
create or replace function public.user_has_admin_access(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.user_roles ur
    join public.role_permissions rp on rp.role_id = ur.role_id
    where ur.user_id = p_user
      and rp.permission_slug like 'admin.%'
  );
$$;

revoke all on function public.user_has_admin_access(uuid) from public, anon, authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Entitlement functions: admin override first, base body verbatim after
-- ────────────────────────────────────────────────────────────────────────────

-- Shared entitlement computation for an arbitrary user. service_role ONLY
-- (reserved for the Python backend belt, phase 2). The explicit revoke from
-- anon/authenticated below is a known footgun guard: a definer function that
-- takes a user id must never be callable with a user JWT.
create or replace function public.get_entitlement(p_user uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_kind text;
  v_ends timestamptz;
begin
  if p_user is null then
    return jsonb_build_object('status', 'none', 'kind', null, 'expires_at', null);
  end if;

  -- Admin override FIRST: any admin.* permission ⇒ permanently active,
  -- whatever access_grants says. NULL expiry = never expires.
  if public.user_has_admin_access(p_user) then
    return jsonb_build_object('status', 'active', 'kind', 'admin', 'expires_at', null);
  end if;

  -- Active grant with the latest ends_at.
  select g.kind, g.ends_at
    into v_kind, v_ends
  from public.access_grants g
  where g.user_id = p_user
    and g.revoked_at is null
    and g.starts_at <= now()
    and g.ends_at > now()
  order by g.ends_at desc
  limit 1;

  if found then
    return jsonb_build_object('status', 'active', 'kind', v_kind, 'expires_at', v_ends);
  end if;

  -- No active grant, but the user had grants before → expired (keep the last
  -- kind and the EFFECTIVE expiry for banner texts). A revoked grant "ends"
  -- at the revocation moment, not at its original ends_at — otherwise a
  -- revoked future-dated grant would report expires_at in the future.
  select g.kind, least(g.ends_at, coalesce(g.revoked_at, g.ends_at))
    into v_kind, v_ends
  from public.access_grants g
  where g.user_id = p_user
  order by least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) desc
  limit 1;

  if found then
    return jsonb_build_object('status', 'expired', 'kind', v_kind, 'expires_at', v_ends);
  end if;

  return jsonb_build_object('status', 'none', 'kind', null, 'expires_at', null);
end;
$$;

revoke all on function public.get_entitlement(uuid) from public;
revoke all on function public.get_entitlement(uuid) from anon;
revoke all on function public.get_entitlement(uuid) from authenticated;
grant execute on function public.get_entitlement(uuid) to service_role;

-- Entitlement of the CALLING user (the BFF calls this with the user's JWT).
create or replace function public.get_my_entitlement()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Admin override FIRST. Also enforced inside get_entitlement() — repeated
  -- here explicitly so the "admin ⇒ always active" invariant survives even
  -- if the delegation below ever changes.
  if public.user_has_admin_access(auth.uid()) then
    return jsonb_build_object('status', 'active', 'kind', 'admin', 'expires_at', null);
  end if;

  -- Runs as the function owner, so the internal call to get_entitlement()
  -- does not require the caller to hold EXECUTE on it.
  return public.get_entitlement(auth.uid());
end;
$$;

-- ВАЖНО: на Supabase 'revoke ... from public' НЕ отбирает EXECUTE у anon —
-- default privileges выдают его каждой роли НАПРЯМУЮ, поэтому явный revoke
-- (create or replace сохраняет ACL, но стиль репо — явные statements).
revoke all on function public.get_my_entitlement() from public, anon;
grant execute on function public.get_my_entitlement() to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- admin_list_users: admins shown as active with permanent access
-- ────────────────────────────────────────────────────────────────────────────

-- User list for the admin dashboard. Sorted by days_left ASC NULLS LAST so
-- the soonest-expiring users come first; admins (days_left = NULL — access
-- never expires) therefore sort last, together with users without grants.
create or replace function public.admin_list_users(
  p_search text default null,
  p_limit int default 200,
  p_offset int default 0
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

  select coalesce(
           jsonb_agg(
             jsonb_build_object(
               'email',           t.email,
               'first_name',      t.first_name,
               'last_name',       t.last_name,
               'phone',           t.phone,
               'registered_at',   t.registered_at,
               'last_sign_in_at', t.last_sign_in_at,
               'status',          t.status,
               'kind',            t.kind,
               'access_until',    t.access_until,
               'days_left',       t.days_left,
               'roles',           t.roles
             )
             order by t.days_left asc nulls last, t.email asc
           ),
           '[]'::jsonb
         )
    into v_result
  from (
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
    -- Admin override: any admin.* permission → permanent access row
    -- (kind='admin', no expiry) — same criterion as public.is_admin().
    cross join lateral (
      select public.user_has_admin_access(u.id) as is_admin
    ) adm
    -- Assigned RBAC roles as [{slug,name}] for badges in the dashboard.
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
    -- Best active grant (drives status='active' for non-admins).
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
    -- Last grant ever (drives status='expired' + banner texts). Effective
    -- expiry: a revoked grant "ends" at revoked_at, not its original ends_at.
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
    -- Pagination order must match the outer jsonb_agg order: an admin's
    -- effective expiry is NULL (permanent), not their raw grants' ends_at.
    order by case when adm.is_admin then null
                  else coalesce(a.ends_at, l.ends_at) end asc nulls last,
             u.email asc
    limit p_limit offset p_offset
  ) t;

  return v_result;
end;
$$;

revoke all on function public.admin_list_users(text, int, int) from public, anon;
grant execute on function public.admin_list_users(text, int, int) to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Studio-only views (NOT for the app)
-- ────────────────────────────────────────────────────────────────────────────
-- Views run with the OWNER's privileges and therefore BYPASS RLS — the
-- revokes below are mandatory (create or replace keeps existing ACLs, they
-- are repeated in the explicit style of the base migration).

create or replace view public.admin_user_overview as
select u.id,
       u.email,
       p.first_name,
       p.last_name,
       p.phone,
       coalesce(rl.roles, '')   as roles,
       u.created_at             as registered_at,
       u.last_sign_in_at,
       case
         when adm.is_admin          then 'active'
         when a.ends_at is not null then 'active'
         when l.ends_at is not null then 'expired'
         else 'none'
       end as status,
       case when adm.is_admin then 'admin'
            else coalesce(a.kind, l.kind) end as kind,
       case when adm.is_admin then null
            else coalesce(a.ends_at, l.ends_at) end as access_until,
       case
         when adm.is_admin then null
         when coalesce(a.ends_at, l.ends_at) is not null
           then floor(extract(epoch from (coalesce(a.ends_at, l.ends_at) - now())) / 86400)::int
         else null
       end as days_left
from auth.users u
left join public.profiles p on p.id = u.id
-- Admin override: any admin.* permission → permanent access (kind='admin').
cross join lateral (
  select public.user_has_admin_access(u.id) as is_admin
) adm
-- Assigned RBAC roles as one string, e.g. 'admin' or 'admin_readonly, support'.
left join lateral (
  select string_agg(r.slug, ', ' order by r.slug) as roles
  from public.user_roles ur
  join public.roles r on r.id = ur.role_id
  where ur.user_id = u.id
) rl on true
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
-- Effective expiry: a revoked grant "ends" at revoked_at (see get_entitlement).
left join lateral (
  select g.kind, least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) as ends_at
  from public.access_grants g
  where g.user_id = u.id
  order by least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) desc
  limit 1
) l on true;

-- "Call to renew" list: admins are EXCLUDED — their access never expires, an
-- expiring grant on an admin account is irrelevant noise for the call list.
create or replace view public.admin_expiring_soon as
select u.email,
       p.first_name,
       p.last_name,
       p.phone,
       a.kind,
       a.ends_at as access_until,
       floor(extract(epoch from (a.ends_at - now())) / 86400)::int as days_left
from auth.users u
left join public.profiles p on p.id = u.id
join lateral (
  select g.kind, g.ends_at
  from public.access_grants g
  where g.user_id = u.id
    and g.revoked_at is null
    and g.starts_at <= now()
    and g.ends_at > now()
  order by g.ends_at desc
  limit 1
) a on true
where a.ends_at < now() + interval '7 days'
  and not public.user_has_admin_access(u.id)
order by a.ends_at asc;

revoke all on public.admin_user_overview  from public, anon, authenticated;
revoke all on public.admin_expiring_soon  from public, anon, authenticated;
