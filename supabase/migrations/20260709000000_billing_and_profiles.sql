-- Billing / access gating: user profiles, append-only access grants, promo
-- codes, RBAC (roles/permissions), admin RPCs and Studio-only views.
--
-- DESIGN INVARIANTS:
--   * `access_grants` is an APPEND-ONLY journal and the single source of truth
--     for access. Status is NEVER stored as a flag — it is always computed:
--     active ⟺ a grant exists with revoked_at IS NULL AND starts_at <= now()
--     AND now() < ends_at. Revocation appends revoked_at, it never deletes.
--   * ALL writes go through SECURITY DEFINER functions (or the service role).
--     Tables carry RLS with at most a SELECT-own policy; promo_codes,
--     promo_redemptions, billing_settings, billing_events and the four RBAC
--     tables have RLS enabled with ZERO policies (deny all for
--     anon/authenticated) — promo codes must not be readable/enumerable from
--     the client.
--   * RBAC: rights live in roles / permissions / role_permissions /
--     user_roles (profiles carries NO role column). `permissions` is a
--     catalog seeded ONLY by migrations — every slug matches a real check in
--     code, nothing is created from the UI. Roles are free permission sets
--     managed from /admin; a user may hold several roles; access = holding
--     the permission through at least one role.
--   * Admin RPCs are granted to `authenticated` but self-guard as their FIRST
--     statement with public.has_permission('<concrete permission>') (list
--     RPCs for the panel itself use public.is_admin() = "any admin.*
--     permission"), so the /admin page works under the user's JWT — no
--     service key ever reaches the Next/Cloudflare layer.
--   * Role management can never be orphaned: after any mutation at least one
--     user still holds admin.roles.manage. Every mutation able to shrink the
--     set of its holders runs under
--     pg_advisory_xact_lock(hashtextextended('rbac:roles.manage', 0)) and
--     re-verifies the invariant AFTER mutating ('cannot remove last admin').
--   * The system role `admin` (is_system=true, not editable/deletable) always
--     holds ALL permissions: future migrations that add permission slugs MUST
--     repeat the insert...select top-up from the Seed section below.
--   * The handle_new_user trigger must NEVER break signup: its whole body is
--     wrapped in EXCEPTION WHEN OTHERS THEN RETURN NEW (complete_my_profile
--     self-heals via UPSERT if the trigger failed). The signup GATE trigger
--     (on_auth_user_signup_gate) is the opposite by design: its job IS to
--     fail — no exception wrapper.
--   * kind='payment' and payment_ref are reserved for a future payment
--     gateway — the model does not change when payments arrive.
--
-- Safe to re-run: create table if not exists, create or replace function,
-- drop policy/trigger if exists + create, guarded/ON CONFLICT backfill & seed.

-- ────────────────────────────────────────────────────────────────────────────
-- Tables
-- ────────────────────────────────────────────────────────────────────────────

-- promo_codes first: access_grants references it.
create table if not exists public.promo_codes (
  id              uuid primary key default gen_random_uuid(),
  code            text unique not null check (code = upper(code)),
  grant_days      int  not null check (grant_days > 0),
  max_redemptions int  not null default 1 check (max_redemptions > 0),
  redeemed_count  int  not null default 0,
  -- Until when the code can be ACTIVATED (not the access duration itself).
  expires_at      timestamptz,
  disabled_at     timestamptz,
  created_by      text,
  note            text,
  created_at      timestamptz not null default now()
);

comment on table public.promo_codes is
  'Promo codes. RLS deny-all: never readable/enumerable from the client; redeemed only via redeem_promo().';

create table if not exists public.profiles (
  id                   uuid primary key references auth.users (id) on delete cascade,
  first_name           text,
  last_name            text,
  phone                text check (
                         phone is null
                         or (phone ~ '^\+7\d{10}$' and phone !~ '^\+7(\d)\1{9}$')
                       ),
  profile_completed_at timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

comment on table public.profiles is
  'Per-user profile (name/phone). Written ONLY by the auth trigger, definer functions and the service role — no client insert/update policies. Admin rights live in user_roles, NOT here.';

create table if not exists public.access_grants (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users (id) on delete cascade,
  -- 'payment' is reserved for the future payment gateway.
  kind           text not null check (kind in ('trial', 'promo', 'manual', 'payment')),
  starts_at      timestamptz not null default now(),
  ends_at        timestamptz not null,
  revoked_at     timestamptz,
  revoked_reason text,
  promo_code_id  uuid references public.promo_codes (id),
  -- Future payment gateway transaction id.
  payment_ref    text,
  -- Admin email / 'system:signup_trial' / 'system:promo' / 'system:backfill'.
  granted_by     text,
  note           text,
  created_at     timestamptz not null default now()
);

comment on table public.access_grants is
  'APPEND-ONLY access journal — the single source of truth. Status is always computed (active ⟺ revoked_at IS NULL AND starts_at <= now() < ends_at), never stored.';

create index if not exists access_grants_user_ends_idx
  on public.access_grants (user_id, ends_at desc)
  where revoked_at is null;

create unique index if not exists access_grants_user_promo_key
  on public.access_grants (user_id, promo_code_id)
  where promo_code_id is not null;

create unique index if not exists access_grants_payment_ref_key
  on public.access_grants (payment_ref)
  where kind = 'payment';

create table if not exists public.promo_redemptions (
  id              uuid primary key default gen_random_uuid(),
  promo_code_id   uuid not null references public.promo_codes (id),
  user_id         uuid not null references auth.users (id) on delete cascade,
  access_grant_id uuid references public.access_grants (id),
  redeemed_at     timestamptz not null default now(),
  unique (promo_code_id, user_id)
);

comment on table public.promo_redemptions is
  'One row per (code, user) redemption. RLS deny-all; written only inside redeem_promo().';

create table if not exists public.billing_settings (
  id                boolean primary key default true check (id),
  signup_trial_days int not null default 7,
  signup_enabled    boolean not null default true
);

-- Re-run safety: create table if not exists above does nothing when the table
-- pre-dates the signup_enabled column.
alter table public.billing_settings
  add column if not exists signup_enabled boolean not null default true;

comment on table public.billing_settings is
  'Single-row settings (id=true enforces one row). signup_trial_days=0 disables the automatic signup trial; signup_enabled=false closes open registration at the DB level (on_auth_user_signup_gate).';

create table if not exists public.billing_events (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid,
  -- 'system:signup' / admin email / 'user:<uuid>' / 'system:backfill'.
  actor         text,
  -- 'user_created' | 'trial_granted' | 'promo_redeemed' | 'grant_created'
  -- | 'grant_revoked' | 'promo_created' | 'profile_completed'
  -- | 'profile_backfilled' | 'settings_updated' | 'role_created'
  -- | 'role_updated' | 'role_deleted' | 'role_assigned' | 'role_revoked'
  type          text not null,
  grant_id      uuid,
  promo_code_id uuid,
  payload       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

comment on table public.billing_events is
  'Append-only audit log of every billing/profile mutation. RLS deny-all; readable only from Studio / service role.';

-- ── RBAC ────────────────────────────────────────────────────────────────────

create table if not exists public.roles (
  id          uuid primary key default gen_random_uuid(),
  slug        text unique not null check (slug ~ '^[a-z0-9_]{2,40}$'),
  name        text not null,
  description text,
  is_system   boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

comment on table public.roles is
  'RBAC roles = free combinations of permissions, managed from /admin (admin_*_role RPCs). is_system roles (the seeded `admin`) cannot be edited or deleted. RLS deny-all: reads/writes only through definer RPCs.';

create table if not exists public.permissions (
  slug        text primary key,
  description text not null
);

comment on table public.permissions is
  'RBAC permission CATALOG, seeded ONLY by migrations — every slug matches a real check in code, never created from the UI. RLS deny-all.';

create table if not exists public.role_permissions (
  role_id         uuid not null references public.roles (id) on delete cascade,
  permission_slug text not null references public.permissions (slug) on delete cascade,
  primary key (role_id, permission_slug)
);

comment on table public.role_permissions is
  'Which permissions a role grants. RLS deny-all; written only inside admin_*_role RPCs and migration seeds.';

create table if not exists public.user_roles (
  user_id    uuid not null references auth.users (id) on delete cascade,
  role_id    uuid not null references public.roles (id) on delete cascade,
  -- Admin email / 'bootstrap' (first admin, see docs/billing-admin.md).
  granted_by text,
  created_at timestamptz not null default now(),
  primary key (user_id, role_id)
);

comment on table public.user_roles is
  'Role assignments (a user may hold several roles — rights are the union). RLS deny-all; written only via admin_assign_role/admin_revoke_role and the first-admin bootstrap snippet.';

-- ────────────────────────────────────────────────────────────────────────────
-- RLS
-- ────────────────────────────────────────────────────────────────────────────

alter table public.profiles          enable row level security;
alter table public.access_grants     enable row level security;
alter table public.promo_codes       enable row level security;
alter table public.promo_redemptions enable row level security;
alter table public.billing_settings  enable row level security;
alter table public.billing_events    enable row level security;
alter table public.roles             enable row level security;
alter table public.permissions       enable row level security;
alter table public.role_permissions  enable row level security;
alter table public.user_roles        enable row level security;

-- profiles: users may READ their own row only. No insert/update/delete
-- policies — all writes go through the trigger / definer functions / service
-- role.
drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select to authenticated
  using (id = auth.uid());

-- access_grants: users may READ their own grants; writes only via definer
-- functions / service role.
drop policy if exists access_grants_select_own on public.access_grants;
create policy access_grants_select_own on public.access_grants
  for select to authenticated
  using (user_id = auth.uid());

-- Column-level grants поверх политики: пользователю видны только "когда и
-- какой" у него доступ. granted_by (личный email админа), note («оплата по
-- счёту №14») и revoked_reason — внутренняя админская информация, которая
-- иначе утекала бы через PostgREST (GET /rest/v1/access_grants?select=...).
revoke select on public.access_grants from anon, authenticated;
grant select (id, user_id, kind, starts_at, ends_at, revoked_at, created_at)
  on public.access_grants to authenticated;

-- promo_codes / promo_redemptions / billing_settings / billing_events /
-- roles / permissions / role_permissions / user_roles:
-- RLS enabled with ZERO policies = deny all for anon/authenticated.

-- ────────────────────────────────────────────────────────────────────────────
-- Entitlement functions
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
  -- Runs as the function owner, so the internal call to get_entitlement()
  -- does not require the caller to hold EXECUTE on it.
  return public.get_entitlement(auth.uid());
end;
$$;

-- ВАЖНО: на Supabase 'revoke ... from public' НЕ отбирает EXECUTE у anon —
-- default privileges (ALTER DEFAULT PRIVILEGES ... GRANT ALL ON FUNCTIONS TO
-- anon, authenticated) выдают его каждой роли НАПРЯМУЮ. Поэтому у каждой
-- функции ниже явный revoke от anon (и от authenticated там, где она не
-- предназначена для вызова с пользовательским JWT).
revoke all on function public.get_my_entitlement() from public, anon;
grant execute on function public.get_my_entitlement() to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Signup trigger
-- ────────────────────────────────────────────────────────────────────────────

-- Creates the profile from signup metadata and grants the signup trial.
-- MUST NEVER break registration: any error is swallowed and the row insert
-- proceeds — complete_my_profile() self-heals the profile via UPSERT.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_first      text;
  v_last       text;
  v_phone      text;
  v_completed  timestamptz;
  v_trial_days int;
  v_grant_id   uuid;
begin
  v_first := nullif(trim(coalesce(new.raw_user_meta_data ->> 'first_name', '')), '');
  v_last  := nullif(trim(coalesce(new.raw_user_meta_data ->> 'last_name', '')), '');
  v_phone := nullif(trim(coalesce(new.raw_user_meta_data ->> 'phone', '')), '');

  -- Same validation as the profiles CHECK; an invalid phone is stored as NULL
  -- instead of failing the insert.
  if v_phone is not null
     and (v_phone !~ '^\+7\d{10}$' or v_phone ~ '^\+7(\d)\1{9}$') then
    v_phone := null;
  end if;

  if length(coalesce(v_first, '')) >= 2
     and length(coalesce(v_last, '')) >= 2
     and v_phone is not null then
    v_completed := now();
  end if;

  insert into public.profiles (id, first_name, last_name, phone, profile_completed_at)
  values (new.id, v_first, v_last, v_phone, v_completed)
  on conflict (id) do nothing;

  insert into public.billing_events (user_id, actor, type, payload)
  values (new.id, 'system:signup', 'user_created',
          jsonb_build_object('profile_completed', v_completed is not null));

  select s.signup_trial_days into v_trial_days
  from public.billing_settings s
  where s.id = true;

  if coalesce(v_trial_days, 0) > 0 then
    insert into public.access_grants (user_id, kind, ends_at, granted_by)
    values (new.id, 'trial', now() + make_interval(days => v_trial_days), 'system:signup_trial')
    returning id into v_grant_id;

    insert into public.billing_events (user_id, actor, type, grant_id, payload)
    values (new.id, 'system:signup', 'trial_granted', v_grant_id,
            jsonb_build_object('days', v_trial_days));
  end if;

  return new;
exception when others then
  -- A billing failure must never block signup.
  return new;
end;
$$;

-- Trigger-only: никто не должен уметь вызвать её через RPC.
revoke all on function public.handle_new_user() from public, anon, authenticated;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ────────────────────────────────────────────────────────────────────────────
-- Signup gate (open-registration switch)
-- ────────────────────────────────────────────────────────────────────────────

-- Честный запрет регистрации на уровне БД: пока signup_enabled=false, ЛЮБАЯ
-- вставка в auth.users падает с 'signups_disabled'. НАМЕРЕННО без
-- exception-обёртки — в отличие от handle_new_user, работа этого триггера
-- ИМЕННО падать.
-- CAVEAT: BEFORE INSERT на auth.users блокирует и приглашения из Supabase
-- Studio (Invite user / Create new user). Ручной онбординг при закрытой
-- регистрации: временно включить тумблер в /admin (или Studio-сниппетом,
-- см. docs/billing-admin.md), завести пользователя, выключить обратно.
create or replace function public.enforce_signup_enabled()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if not coalesce(
       (select s.signup_enabled from public.billing_settings s limit 1),
       true
     ) then
    raise exception 'signups_disabled';
  end if;

  return new;
end;
$$;

-- Trigger-only: никто не должен уметь вызвать её через RPC.
revoke all on function public.enforce_signup_enabled() from public, anon, authenticated;

drop trigger if exists on_auth_user_signup_gate on auth.users;
create trigger on_auth_user_signup_gate
  before insert on auth.users
  for each row execute function public.enforce_signup_enabled();

-- Виден ли таб регистрации. Вызывается АНОНИМОМ до регистрации, поэтому
-- grant to anon. Fail-open (coalesce true, нет строки настроек → открыто):
-- это только UX — настоящий запрет обеспечивает триггер выше.
create or replace function public.is_signup_enabled()
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  return coalesce(
    (select s.signup_enabled from public.billing_settings s limit 1),
    true
  );
end;
$$;

revoke all on function public.is_signup_enabled() from public;
grant execute on function public.is_signup_enabled() to anon, authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Promo redemption
-- ────────────────────────────────────────────────────────────────────────────

-- Redeems a promo code for the calling user.
--   * Rate limit lives INSIDE the function: the RPC is reachable by
--     `authenticated` directly through PostgREST, bypassing the BFF.
--   * SELECT ... FOR UPDATE serializes concurrent redemptions of one code.
--   * Anti-enumeration: every failure except already_used/rate_limited
--     returns the same 'invalid' code — callers cannot distinguish
--     nonexistent / disabled / expired / exhausted codes.
--   * With an active grant the code extends access back-to-back:
--     greatest(now(), max active ends_at) + grant_days.
create or replace function public.redeem_promo(p_code text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid      uuid := auth.uid();
  v_code     text;
  v_promo    public.promo_codes%rowtype;
  v_ends     timestamptz;
  v_grant_id uuid;
begin
  if v_uid is null then
    return jsonb_build_object('ok', false, 'error', 'invalid');
  end if;

  if not public.check_rate_limit('promo_redeem', 10, 3600) then
    return jsonb_build_object('ok', false, 'error', 'rate_limited');
  end if;

  v_code := upper(trim(coalesce(p_code, '')));
  if v_code = '' then
    return jsonb_build_object('ok', false, 'error', 'invalid');
  end if;

  select * into v_promo
  from public.promo_codes
  where code = v_code
  for update;

  if not found
     or v_promo.disabled_at is not null
     or (v_promo.expires_at is not null and v_promo.expires_at <= now())
     or v_promo.redeemed_count >= v_promo.max_redemptions then
    return jsonb_build_object('ok', false, 'error', 'invalid');
  end if;

  if exists (
    select 1 from public.promo_redemptions r
    where r.promo_code_id = v_promo.id and r.user_id = v_uid
  ) then
    return jsonb_build_object('ok', false, 'error', 'already_used');
  end if;

  -- Сериализуем ПАРАЛЛЕЛЬНЫЕ продления одного пользователя (два разных кода
  -- или код + admin_grant_access): FOR UPDATE выше держит только строку
  -- этого промокода, поэтому без per-user блокировки обе транзакции читают
  -- одну и ту же базу max(ends_at) и гранты лягут внахлёст вместо встык.
  perform pg_advisory_xact_lock(hashtextextended('access_grants:' || v_uid::text, 0));

  v_ends := greatest(
              now(),
              coalesce((select max(g.ends_at)
                        from public.access_grants g
                        where g.user_id = v_uid
                          and g.revoked_at is null
                          and g.ends_at > now()), now())
            ) + make_interval(days => v_promo.grant_days);

  insert into public.access_grants (user_id, kind, ends_at, promo_code_id, granted_by)
  values (v_uid, 'promo', v_ends, v_promo.id, 'system:promo')
  returning id into v_grant_id;

  insert into public.promo_redemptions (promo_code_id, user_id, access_grant_id)
  values (v_promo.id, v_uid, v_grant_id);

  update public.promo_codes
  set redeemed_count = redeemed_count + 1
  where id = v_promo.id;

  insert into public.billing_events (user_id, actor, type, grant_id, promo_code_id, payload)
  values (v_uid, 'user:' || v_uid::text, 'promo_redeemed', v_grant_id, v_promo.id,
          jsonb_build_object('code', v_code, 'grant_days', v_promo.grant_days));

  return jsonb_build_object('ok', true, 'entitlement', public.get_my_entitlement());
end;
$$;

revoke all on function public.redeem_promo(text) from public, anon;
grant execute on function public.redeem_promo(text) to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Profile completion (onboarding gate + self-heal)
-- ────────────────────────────────────────────────────────────────────────────

-- UPSERT so it also repairs a missing profile if the signup trigger failed.
create or replace function public.complete_my_profile(
  p_first_name text,
  p_last_name text,
  p_phone text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid   uuid := auth.uid();
  v_first text := trim(coalesce(p_first_name, ''));
  v_last  text := trim(coalesce(p_last_name, ''));
  v_phone text := trim(coalesce(p_phone, ''));
begin
  if v_uid is null then
    -- Safety net: EXECUTE is granted to authenticated only.
    raise exception 'not authenticated';
  end if;

  if length(v_first) < 2 or length(v_last) < 2 then
    return jsonb_build_object('ok', false, 'error', 'invalid_name');
  end if;

  if v_phone !~ '^\+7\d{10}$' or v_phone ~ '^\+7(\d)\1{9}$' then
    return jsonb_build_object('ok', false, 'error', 'invalid_phone');
  end if;

  insert into public.profiles (id, first_name, last_name, phone, profile_completed_at, updated_at)
  values (v_uid, v_first, v_last, v_phone, now(), now())
  on conflict (id) do update
    set first_name           = excluded.first_name,
        last_name            = excluded.last_name,
        phone                = excluded.phone,
        profile_completed_at = now(),
        updated_at           = now();

  insert into public.billing_events (user_id, actor, type, payload)
  values (v_uid, 'user:' || v_uid::text, 'profile_completed',
          jsonb_build_object('phone', v_phone));

  return jsonb_build_object('ok', true);
end;
$$;

revoke all on function public.complete_my_profile(text, text, text) from public, anon;
grant execute on function public.complete_my_profile(text, text, text) to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- RBAC primitives
-- ────────────────────────────────────────────────────────────────────────────

-- Does the CALLING user hold the permission through at least one role?
-- Every mutating/reading admin RPC self-guards with its CONCRETE permission
-- via this function (see the map in each function below).
create or replace function public.has_permission(p_perm text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  return exists (
    select 1
    from public.user_roles ur
    join public.role_permissions rp on rp.role_id = ur.role_id
    where ur.user_id = auth.uid()
      and rp.permission_slug = p_perm
  );
end;
$$;

revoke all on function public.has_permission(text) from public, anon;
grant execute on function public.has_permission(text) to authenticated;

-- Sorted array of the calling user's unique permission slugs. Drives which
-- tabs/buttons the /admin UI renders — the server-side truth stays in the
-- per-RPC has_permission() guards.
create or replace function public.my_permissions()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_result jsonb;
begin
  select coalesce(
           jsonb_agg(distinct rp.permission_slug order by rp.permission_slug),
           '[]'::jsonb
         )
    into v_result
  from public.user_roles ur
  join public.role_permissions rp on rp.role_id = ur.role_id
  where ur.user_id = auth.uid();

  return v_result;
end;
$$;

revoke all on function public.my_permissions() from public, anon;
grant execute on function public.my_permissions() to authenticated;

-- Panel access: at least one admin.* permission through any role. The name
-- survives from the pre-RBAC model — app/admin/page.tsx and lib/admin.ts
-- call it to gate the /admin page as a whole; concrete actions are always
-- re-checked per-permission inside each RPC.
create or replace function public.is_admin()
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  return exists (
    select 1
    from public.user_roles ur
    join public.role_permissions rp on rp.role_id = ur.role_id
    where ur.user_id = auth.uid()
      and rp.permission_slug like 'admin.%'
  );
end;
$$;

revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Admin functions
-- ────────────────────────────────────────────────────────────────────────────

-- Grant/extend access for a user found by email. Back-to-back extension:
-- greatest(now(), max active ends_at) + p_days.
create or replace function public.admin_grant_access(
  p_email text,
  p_days int,
  p_kind text default 'manual',
  p_note text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_user_id     uuid;
  v_ends        timestamptz;
  v_grant_id    uuid;
begin
  if not public.has_permission('admin.access.manage') then
    raise exception 'forbidden';
  end if;

  if p_kind not in ('trial', 'manual') then
    raise exception 'invalid kind';
  end if;

  if p_days is null or p_days <= 0 then
    raise exception 'invalid days';
  end if;

  -- Fail loudly on a typo'd email instead of silently doing nothing.
  select u.id into v_user_id
  from auth.users u
  where lower(u.email) = lower(trim(p_email))
  limit 1;

  if v_user_id is null then
    raise exception 'user not found';
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  -- Тот же per-user lock, что в redeem_promo: параллельные продления одного
  -- пользователя должны стыковаться, а не перекрываться.
  perform pg_advisory_xact_lock(hashtextextended('access_grants:' || v_user_id::text, 0));

  v_ends := greatest(
              now(),
              coalesce((select max(g.ends_at)
                        from public.access_grants g
                        where g.user_id = v_user_id
                          and g.revoked_at is null
                          and g.ends_at > now()), now())
            ) + make_interval(days => p_days);

  insert into public.access_grants (user_id, kind, ends_at, granted_by, note)
  values (v_user_id, p_kind, v_ends, v_admin_email, p_note)
  returning id into v_grant_id;

  insert into public.billing_events (user_id, actor, type, grant_id, payload)
  values (v_user_id, v_admin_email, 'grant_created', v_grant_id,
          jsonb_build_object('days', p_days, 'kind', p_kind, 'note', p_note));

  return jsonb_build_object('ok', true, 'expires_at', v_ends);
end;
$$;

revoke all on function public.admin_grant_access(text, int, text, text) from public, anon;
grant execute on function public.admin_grant_access(text, int, text, text) to authenticated;

-- Revoke ALL currently active grants of a user (append-only: sets revoked_at,
-- never deletes).
create or replace function public.admin_revoke_access(
  p_email text,
  p_reason text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_user_id     uuid;
  v_count       int;
begin
  if not public.has_permission('admin.access.manage') then
    raise exception 'forbidden';
  end if;

  select u.id into v_user_id
  from auth.users u
  where lower(u.email) = lower(trim(p_email))
  limit 1;

  if v_user_id is null then
    raise exception 'user not found';
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  update public.access_grants
  set revoked_at     = now(),
      revoked_reason = p_reason
  where user_id = v_user_id
    and revoked_at is null
    and ends_at > now();

  get diagnostics v_count = row_count;

  insert into public.billing_events (user_id, actor, type, payload)
  values (v_user_id, v_admin_email, 'grant_revoked',
          jsonb_build_object('reason', p_reason, 'revoked_count', v_count));

  return jsonb_build_object('ok', true, 'revoked_count', v_count);
end;
$$;

revoke all on function public.admin_revoke_access(text, text) from public, anon;
grant execute on function public.admin_revoke_access(text, text) to authenticated;

-- Create a promo code. A duplicate code raises the unique violation as-is
-- (the BFF maps it to 409).
create or replace function public.admin_create_promo(
  p_code text,
  p_days int,
  p_max int default 1,
  p_expires_at timestamptz default null,
  p_note text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_code        text;
  v_promo_id    uuid;
begin
  if not public.has_permission('admin.promos.manage') then
    raise exception 'forbidden';
  end if;

  v_code := upper(trim(coalesce(p_code, '')));
  if v_code = '' then
    raise exception 'invalid code';
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  insert into public.promo_codes (code, grant_days, max_redemptions, expires_at, created_by, note)
  values (v_code, p_days, p_max, p_expires_at, v_admin_email, p_note)
  returning id into v_promo_id;

  insert into public.billing_events (actor, type, promo_code_id, payload)
  values (v_admin_email, 'promo_created', v_promo_id,
          jsonb_build_object('code', v_code, 'grant_days', p_days, 'max_redemptions', p_max));

  return jsonb_build_object('ok', true, 'code', v_code);
end;
$$;

revoke all on function public.admin_create_promo(text, int, int, timestamptz, text) from public, anon;
grant execute on function public.admin_create_promo(text, int, int, timestamptz, text) to authenticated;

-- User list for the admin dashboard. Sorted by days_left ASC NULLS LAST so
-- the soonest-expiring users come first.
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
             when a.ends_at is not null then 'active'
             when l.ends_at is not null then 'expired'
             else 'none'
           end               as status,
           coalesce(a.kind, l.kind)       as kind,
           coalesce(a.ends_at, l.ends_at) as access_until,
           case
             when coalesce(a.ends_at, l.ends_at) is not null
               then floor(extract(epoch from (coalesce(a.ends_at, l.ends_at) - now())) / 86400)::int
             else null
           end               as days_left,
           rl.roles
    from auth.users u
    left join public.profiles p on p.id = u.id
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
    -- Best active grant (drives status='active').
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
    order by coalesce(a.ends_at, l.ends_at) asc nulls last, u.email asc
    limit p_limit offset p_offset
  ) t;

  return v_result;
end;
$$;

revoke all on function public.admin_list_users(text, int, int) from public, anon;
grant execute on function public.admin_list_users(text, int, int) to authenticated;

create or replace function public.admin_list_promos()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_result jsonb;
begin
  if not public.has_permission('admin.promos.view') then
    raise exception 'forbidden';
  end if;

  select coalesce(
           jsonb_agg(
             jsonb_build_object(
               'code',            c.code,
               'grant_days',      c.grant_days,
               'redeemed_count',  c.redeemed_count,
               'max_redemptions', c.max_redemptions,
               'expires_at',      c.expires_at,
               'disabled_at',     c.disabled_at,
               'note',            c.note,
               'created_at',      c.created_at
             )
             order by c.created_at desc
           ),
           '[]'::jsonb
         )
    into v_result
  from public.promo_codes c;

  return v_result;
end;
$$;

revoke all on function public.admin_list_promos() from public, anon;
grant execute on function public.admin_list_promos() to authenticated;

-- Current runtime settings for the /admin "Настройки" tab.
create or replace function public.admin_get_settings()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_settings public.billing_settings%rowtype;
begin
  if not public.has_permission('admin.settings.view') then
    raise exception 'forbidden';
  end if;

  select * into v_settings
  from public.billing_settings
  where id = true;

  -- Нет строки настроек → те же дефолты, что и у колонок.
  return jsonb_build_object(
    'signup_enabled',    coalesce(v_settings.signup_enabled, true),
    'signup_trial_days', coalesce(v_settings.signup_trial_days, 7)
  );
end;
$$;

revoke all on function public.admin_get_settings() from public, anon;
grant execute on function public.admin_get_settings() to authenticated;

-- Update runtime settings. NULL argument = leave the value unchanged.
-- Returns the new settings in the same shape as admin_get_settings().
create or replace function public.admin_update_settings(
  p_signup_enabled boolean default null,
  p_signup_trial_days int default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_settings    public.billing_settings%rowtype;
  v_changes     jsonb := '{}'::jsonb;
begin
  if not public.has_permission('admin.settings.manage') then
    raise exception 'forbidden';
  end if;

  if p_signup_trial_days is not null and p_signup_trial_days < 0 then
    raise exception 'invalid trial days';
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  -- UPSERT, а не UPDATE: если seed-строку удалили (Studio/service role),
  -- UPDATE молча обновил бы 0 строк и вернул NULL-настройки при «успешном»
  -- сохранении. Self-heal — пересоздаём строку с дефолтами (как в
  -- is_signup_enabled/handle_new_user, которые coalesce-ятся в дефолты).
  insert into public.billing_settings as bs (id, signup_trial_days, signup_enabled)
  values (true, coalesce(p_signup_trial_days, 7), coalesce(p_signup_enabled, true))
  on conflict (id) do update
    set signup_enabled    = coalesce(p_signup_enabled, bs.signup_enabled),
        signup_trial_days = coalesce(p_signup_trial_days, bs.signup_trial_days)
  returning * into v_settings;

  -- В аудит пишем только то, что реально прислали менять.
  if p_signup_enabled is not null then
    v_changes := v_changes || jsonb_build_object('signup_enabled', p_signup_enabled);
  end if;
  if p_signup_trial_days is not null then
    v_changes := v_changes || jsonb_build_object('signup_trial_days', p_signup_trial_days);
  end if;

  insert into public.billing_events (actor, type, payload)
  values (v_admin_email, 'settings_updated', v_changes);

  return jsonb_build_object(
    'signup_enabled',    v_settings.signup_enabled,
    'signup_trial_days', v_settings.signup_trial_days
  );
end;
$$;

revoke all on function public.admin_update_settings(boolean, int) from public, anon;
grant execute on function public.admin_update_settings(boolean, int) to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Role management RPCs
-- ────────────────────────────────────────────────────────────────────────────
-- Advisory-lock invariant: role management must never be orphaned — after any
-- mutation at least one user still holds admin.roles.manage. Every mutation
-- able to shrink the set of its holders (revoking a manage-granting role,
-- replacing a role's permission set, deleting a role) serializes through
-- pg_advisory_xact_lock(hashtextextended('rbac:roles.manage', 0)) and
-- re-verifies the invariant AFTER mutating: a violation raises
-- 'cannot remove last admin' and the transaction (and thus the mutation)
-- rolls back. Serializing through one lock closes the READ COMMITTED race of
-- two admins revoking each other's manage role concurrently.

-- Is at least one user left holding admin.roles.manage? Internal helper for
-- the invariant above — not callable via RPC.
create or replace function public.rbac_manage_holders_exist()
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  return exists (
    select 1
    from public.user_roles ur
    join public.role_permissions rp on rp.role_id = ur.role_id
    where rp.permission_slug = 'admin.roles.manage'
  );
end;
$$;

revoke all on function public.rbac_manage_holders_exist() from public, anon, authenticated;

-- Permission catalog for the /admin role editor. Panel access (any admin.*
-- permission) is enough — the catalog itself is not sensitive.
create or replace function public.admin_list_permissions()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_result jsonb;
begin
  if not public.is_admin() then
    raise exception 'forbidden';
  end if;

  select coalesce(
           jsonb_agg(
             jsonb_build_object('slug', p.slug, 'description', p.description)
             order by p.slug
           ),
           '[]'::jsonb
         )
    into v_result
  from public.permissions p;

  return v_result;
end;
$$;

revoke all on function public.admin_list_permissions() from public, anon;
grant execute on function public.admin_list_permissions() to authenticated;

-- All roles with their permissions and assignment counts. Panel access is
-- enough (the users tab shows role badges even for read-only admins).
create or replace function public.admin_list_roles()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_result jsonb;
begin
  if not public.is_admin() then
    raise exception 'forbidden';
  end if;

  select coalesce(
           jsonb_agg(
             jsonb_build_object(
               'id',          r.id,
               'slug',        r.slug,
               'name',        r.name,
               'description', r.description,
               'is_system',   r.is_system,
               'permissions', coalesce(rp.slugs, '[]'::jsonb),
               'users_count', coalesce(uc.cnt, 0)
             )
             order by r.is_system desc, r.slug asc
           ),
           '[]'::jsonb
         )
    into v_result
  from public.roles r
  left join lateral (
    select jsonb_agg(x.permission_slug order by x.permission_slug) as slugs
    from public.role_permissions x
    where x.role_id = r.id
  ) rp on true
  left join lateral (
    select count(*)::int as cnt
    from public.user_roles x
    where x.role_id = r.id
  ) uc on true;

  return v_result;
end;
$$;

revoke all on function public.admin_list_roles() from public, anon;
grant execute on function public.admin_list_roles() to authenticated;

-- Create a role from the /admin role editor. Unknown permission slug →
-- 'invalid permission' (the catalog is seed-only). A duplicate role slug
-- raises the unique violation as-is (the BFF maps it to 409). An invalid
-- slug format is rejected by the roles.slug CHECK.
create or replace function public.admin_create_role(
  p_slug text,
  p_name text,
  p_description text default null,
  p_permissions text[] default '{}'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_slug        text := lower(trim(coalesce(p_slug, '')));
  v_name        text := trim(coalesce(p_name, ''));
  v_perms       text[] := coalesce(p_permissions, '{}');
  v_role_id     uuid;
begin
  if not public.has_permission('admin.roles.manage') then
    raise exception 'forbidden';
  end if;

  if v_name = '' then
    raise exception 'invalid name';
  end if;

  if exists (
    select 1 from unnest(v_perms) as x(slug)
    where not exists (select 1 from public.permissions p where p.slug = x.slug)
  ) then
    raise exception 'invalid permission';
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  insert into public.roles (slug, name, description)
  values (v_slug, v_name, nullif(trim(coalesce(p_description, '')), ''))
  returning id into v_role_id;

  -- ON CONFLICT: duplicates inside the input array are harmless.
  insert into public.role_permissions (role_id, permission_slug)
  select v_role_id, x.slug
  from unnest(v_perms) as x(slug)
  on conflict do nothing;

  insert into public.billing_events (actor, type, payload)
  values (v_admin_email, 'role_created',
          jsonb_build_object('slug', v_slug, 'name', v_name,
                             'permissions', to_jsonb(v_perms)));

  return jsonb_build_object(
    'ok', true,
    'role', jsonb_build_object(
      'id',          v_role_id,
      'slug',        v_slug,
      'name',        v_name,
      'description', nullif(trim(coalesce(p_description, '')), ''),
      'is_system',   false,
      'permissions', (select coalesce(jsonb_agg(x.permission_slug order by x.permission_slug), '[]'::jsonb)
                      from public.role_permissions x where x.role_id = v_role_id),
      'users_count', 0
    )
  );
end;
$$;

revoke all on function public.admin_create_role(text, text, text, text[]) from public, anon;
grant execute on function public.admin_create_role(text, text, text, text[]) to authenticated;

-- Edit a non-system role. NULL argument = leave the value unchanged;
-- p_permissions NOT NULL = FULL replacement of the permission set
-- (delete+insert in this one transaction). Removing admin.roles.manage from
-- a role is checked against the last-admin invariant (see header comment).
create or replace function public.admin_update_role(
  p_slug text,
  p_name text default null,
  p_description text default null,
  p_permissions text[] default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_slug        text := lower(trim(coalesce(p_slug, '')));
  v_role        public.roles%rowtype;
  v_guard       boolean := false;
begin
  if not public.has_permission('admin.roles.manage') then
    raise exception 'forbidden';
  end if;

  select * into v_role
  from public.roles
  where slug = v_slug;

  if not found then
    raise exception 'role not found';
  end if;

  if v_role.is_system then
    raise exception 'system role';
  end if;

  if p_permissions is not null and exists (
    select 1 from unnest(p_permissions) as x(slug)
    where not exists (select 1 from public.permissions p where p.slug = x.slug)
  ) then
    raise exception 'invalid permission';
  end if;

  -- Invariant guard only when the replacement drops admin.roles.manage from
  -- a role that currently grants it.
  if p_permissions is not null
     and not ('admin.roles.manage' = any (p_permissions))
     and exists (
       select 1 from public.role_permissions x
       where x.role_id = v_role.id
         and x.permission_slug = 'admin.roles.manage'
     ) then
    perform pg_advisory_xact_lock(hashtextextended('rbac:roles.manage', 0));
    v_guard := true;
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  update public.roles
  set name        = coalesce(nullif(trim(p_name), ''), name),
      description = case when p_description is null then description
                         else nullif(trim(p_description), '') end,
      updated_at  = now()
  where id = v_role.id;

  if p_permissions is not null then
    delete from public.role_permissions where role_id = v_role.id;

    insert into public.role_permissions (role_id, permission_slug)
    select v_role.id, x.slug
    from unnest(p_permissions) as x(slug)
    on conflict do nothing;
  end if;

  if v_guard and not public.rbac_manage_holders_exist() then
    raise exception 'cannot remove last admin';
  end if;

  insert into public.billing_events (actor, type, payload)
  values (v_admin_email, 'role_updated',
          jsonb_build_object('slug', v_slug, 'name', nullif(trim(coalesce(p_name, '')), ''),
                             'permissions', case when p_permissions is null then null
                                                 else to_jsonb(p_permissions) end));

  return jsonb_build_object(
    'ok', true,
    'role', (
      select jsonb_build_object(
               'id',          r.id,
               'slug',        r.slug,
               'name',        r.name,
               'description', r.description,
               'is_system',   r.is_system,
               'permissions', (select coalesce(jsonb_agg(x.permission_slug order by x.permission_slug), '[]'::jsonb)
                               from public.role_permissions x where x.role_id = r.id),
               'users_count', (select count(*)::int from public.user_roles x where x.role_id = r.id)
             )
      from public.roles r
      where r.id = v_role.id
    )
  );
end;
$$;

revoke all on function public.admin_update_role(text, text, text, text[]) from public, anon;
grant execute on function public.admin_update_role(text, text, text, text[]) to authenticated;

-- Delete a non-system role (cascades role_permissions and user_roles).
-- Deleting a manage-granting role is checked against the last-admin
-- invariant — otherwise deleting the caller's own manage role could orphan
-- role management entirely.
create or replace function public.admin_delete_role(p_slug text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_slug        text := lower(trim(coalesce(p_slug, '')));
  v_role        public.roles%rowtype;
  v_guard       boolean := false;
begin
  if not public.has_permission('admin.roles.manage') then
    raise exception 'forbidden';
  end if;

  select * into v_role
  from public.roles
  where slug = v_slug;

  if not found then
    raise exception 'role not found';
  end if;

  if v_role.is_system then
    raise exception 'system role';
  end if;

  if exists (
    select 1 from public.role_permissions x
    where x.role_id = v_role.id
      and x.permission_slug = 'admin.roles.manage'
  ) then
    perform pg_advisory_xact_lock(hashtextextended('rbac:roles.manage', 0));
    v_guard := true;
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  delete from public.roles where id = v_role.id;

  if v_guard and not public.rbac_manage_holders_exist() then
    raise exception 'cannot remove last admin';
  end if;

  insert into public.billing_events (actor, type, payload)
  values (v_admin_email, 'role_deleted', jsonb_build_object('slug', v_slug));

  return jsonb_build_object('ok', true);
end;
$$;

revoke all on function public.admin_delete_role(text) from public, anon;
grant execute on function public.admin_delete_role(text) to authenticated;

-- Assign a role to a user by email. Own roles cannot be changed — an admin
-- cannot lock out or escalate their OWN account directly. NB: это НЕ
-- two-person control: держатель admin.roles.manage может назначить 'admin'
-- второму аккаунту, которым владеет сам (регистрация открыта по умолчанию),
-- т.е. выдача admin.roles.manage фактически эквивалентна выдаче всех прав.
create or replace function public.admin_assign_role(
  p_email text,
  p_role_slug text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_user_id     uuid;
  v_email       text;
  v_role_id     uuid;
  v_role_slug   text := lower(trim(coalesce(p_role_slug, '')));
begin
  if not public.has_permission('admin.roles.manage') then
    raise exception 'forbidden';
  end if;

  select u.id, lower(u.email) into v_user_id, v_email
  from auth.users u
  where lower(u.email) = lower(trim(p_email))
  limit 1;

  if v_user_id is null then
    raise exception 'user not found';
  end if;

  if v_user_id = auth.uid() then
    raise exception 'cannot change own roles';
  end if;

  select r.id into v_role_id
  from public.roles r
  where r.slug = v_role_slug;

  if v_role_id is null then
    raise exception 'role not found';
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  -- Idempotent: re-assigning an already-held role is a no-op.
  insert into public.user_roles (user_id, role_id, granted_by)
  values (v_user_id, v_role_id, v_admin_email)
  on conflict do nothing;

  insert into public.billing_events (user_id, actor, type, payload)
  values (v_user_id, v_admin_email, 'role_assigned',
          jsonb_build_object('slug', v_role_slug, 'email', v_email));

  return jsonb_build_object('ok', true, 'email', v_email, 'role', v_role_slug);
end;
$$;

revoke all on function public.admin_assign_role(text, text) from public, anon;
grant execute on function public.admin_assign_role(text, text) to authenticated;

-- Revoke a role from a user by email. If the role grants admin.roles.manage,
-- the removal is verified against the last-admin invariant UNDER the
-- advisory lock: the permission check at function entry runs before the
-- lock, so two admins revoking each other concurrently both pass it — the
-- post-delete re-check is what makes the second transaction fail with
-- 'cannot remove last admin' instead of orphaning role management.
create or replace function public.admin_revoke_role(
  p_email text,
  p_role_slug text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_admin_email text;
  v_user_id     uuid;
  v_email       text;
  v_role_id     uuid;
  v_role_slug   text := lower(trim(coalesce(p_role_slug, '')));
  v_guard       boolean := false;
begin
  if not public.has_permission('admin.roles.manage') then
    raise exception 'forbidden';
  end if;

  select u.id, lower(u.email) into v_user_id, v_email
  from auth.users u
  where lower(u.email) = lower(trim(p_email))
  limit 1;

  if v_user_id is null then
    raise exception 'user not found';
  end if;

  if v_user_id = auth.uid() then
    raise exception 'cannot change own roles';
  end if;

  select r.id into v_role_id
  from public.roles r
  where r.slug = v_role_slug;

  if v_role_id is null then
    raise exception 'role not found';
  end if;

  if exists (
    select 1 from public.role_permissions x
    where x.role_id = v_role_id
      and x.permission_slug = 'admin.roles.manage'
  ) then
    perform pg_advisory_xact_lock(hashtextextended('rbac:roles.manage', 0));
    v_guard := true;
  end if;

  select lower(u.email) into v_admin_email
  from auth.users u
  where u.id = auth.uid();

  delete from public.user_roles
  where user_id = v_user_id
    and role_id = v_role_id;

  if v_guard and not public.rbac_manage_holders_exist() then
    raise exception 'cannot remove last admin';
  end if;

  insert into public.billing_events (user_id, actor, type, payload)
  values (v_user_id, v_admin_email, 'role_revoked',
          jsonb_build_object('slug', v_role_slug, 'email', v_email));

  return jsonb_build_object('ok', true, 'email', v_email, 'role', v_role_slug);
end;
$$;

revoke all on function public.admin_revoke_role(text, text) from public, anon;
grant execute on function public.admin_revoke_role(text, text) to authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Studio-only views (NOT for the app)
-- ────────────────────────────────────────────────────────────────────────────
-- Views run with the OWNER's privileges and therefore BYPASS RLS — the
-- revokes below are mandatory. Query them from Supabase Studio (postgres) or
-- with the service role only.

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
         when a.ends_at is not null then 'active'
         when l.ends_at is not null then 'expired'
         else 'none'
       end                            as status,
       coalesce(a.kind, l.kind)       as kind,
       coalesce(a.ends_at, l.ends_at) as access_until,
       case
         when coalesce(a.ends_at, l.ends_at) is not null
           then floor(extract(epoch from (coalesce(a.ends_at, l.ends_at) - now())) / 86400)::int
         else null
       end as days_left
from auth.users u
left join public.profiles p on p.id = u.id
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
order by a.ends_at asc;

create or replace view public.admin_promo_stats as
select c.code,
       c.grant_days,
       c.redeemed_count,
       c.max_redemptions,
       c.expires_at,
       c.disabled_at,
       c.note,
       c.created_at,
       count(r.id)         as redemptions_logged,
       max(r.redeemed_at)  as last_redeemed_at
from public.promo_codes c
left join public.promo_redemptions r on r.promo_code_id = c.id
group by c.id
order by c.created_at desc;

revoke all on public.admin_user_overview  from public, anon, authenticated;
revoke all on public.admin_expiring_soon  from public, anon, authenticated;
revoke all on public.admin_promo_stats    from public, anon, authenticated;

-- ────────────────────────────────────────────────────────────────────────────
-- Seed & backfill
-- ────────────────────────────────────────────────────────────────────────────

insert into public.billing_settings (id, signup_trial_days)
values (true, 7)
on conflict (id) do nothing;

-- ── RBAC seeds ──────────────────────────────────────────────────────────────

-- Permission catalog: seed-only, every slug matches a real check in code.
insert into public.permissions (slug, description) values
  ('admin.access.manage',   'Выдача, продление и отзыв доступа'),
  ('admin.promos.manage',   'Создание промокодов'),
  ('admin.promos.view',     'Просмотр промокодов и статистики'),
  ('admin.roles.manage',    'Управление ролями и их назначением'),
  ('admin.settings.manage', 'Изменение настроек (регистрация, дни триала)'),
  ('admin.settings.view',   'Просмотр настроек'),
  ('admin.users.view',      'Просмотр пользователей и статусов доступа')
on conflict (slug) do nothing;

-- System role `admin`: always holds ALL permissions, not editable/deletable.
insert into public.roles (slug, name, description, is_system)
values ('admin', 'Администратор', 'Полный доступ ко всем разделам админки', true)
on conflict (slug) do nothing;

-- Top-up, NOT a fixed list: a re-run picks up any permission added later.
-- ВАЖНО: будущие миграции, добавляющие пермишены, ОБЯЗАНЫ повторять этот
-- insert...select (или перезапускать эту миграцию) — системная роль admin
-- всегда должна иметь ВСЕ пермишены.
insert into public.role_permissions (role_id, permission_slug)
select r.id, p.slug
from public.roles r
cross join public.permissions p
where r.slug = 'admin'
on conflict do nothing;

-- Editable EXAMPLE role: read-only admin (is_system=false). Its permissions
-- are seeded only when the role is FIRST created: a re-run must not
-- resurrect permissions an admin has since removed from it.
with new_role as (
  insert into public.roles (slug, name, description, is_system)
  values ('admin_readonly', 'Администратор (только чтение)',
          'Просмотр пользователей, промокодов и настроек без права изменений',
          false)
  on conflict (slug) do nothing
  returning id
)
insert into public.role_permissions (role_id, permission_slug)
select nr.id, v.slug
from new_role nr
cross join (values ('admin.users.view'),
                   ('admin.promos.view'),
                   ('admin.settings.view')) as v(slug)
on conflict do nothing;

-- Backfill 1: an (empty) profile for every existing user — they will fill
-- name/phone through the onboarding gate.
with ins as (
  insert into public.profiles (id)
  select u.id from auth.users u
  on conflict (id) do nothing
  returning id
)
insert into public.billing_events (user_id, actor, type, payload)
select ins.id, 'system:backfill', 'profile_backfilled', '{}'::jsonb
from ins;

-- Backfill 2: transitional 7-day trial for ALL existing users at gating
-- launch. NOT EXISTS on granted_by='system:backfill' keeps a re-run from
-- granting a second trial.
with ins as (
  insert into public.access_grants (user_id, kind, starts_at, ends_at, granted_by, note)
  select u.id, 'trial', now(), now() + interval '7 days', 'system:backfill',
         '7 дней при запуске gating'
  from auth.users u
  where not exists (
    select 1 from public.access_grants g
    where g.user_id = u.id and g.granted_by = 'system:backfill'
  )
  returning id, user_id
)
insert into public.billing_events (user_id, actor, type, grant_id, payload)
select ins.user_id, 'system:backfill', 'trial_granted', ins.id,
       jsonb_build_object('days', 7)
from ins;
