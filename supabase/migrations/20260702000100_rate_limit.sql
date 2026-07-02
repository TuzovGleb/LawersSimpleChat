-- Per-user rate limiting, enforced in Postgres so it works across serverless
-- instances (an in-process counter would reset per cold start / not be shared).
--
-- The BFF calls public.check_rate_limit(kind, max, window) with the user's
-- authenticated Supabase client. The bucket key is derived from auth.uid()
-- INSIDE the function, so a caller can only ever rate-limit THEMSELVES — they
-- cannot pass another user's id to lock someone else out. Fixed-window counter.

create table if not exists public.rate_limit_hits (
  bucket       text primary key,
  window_start timestamptz not null,
  count        integer not null
);

-- No direct access for anon/authenticated: only the security-definer function
-- (which runs as the owner and bypasses RLS) may touch this table.
alter table public.rate_limit_hits enable row level security;

create or replace function public.check_rate_limit(
  p_kind text,
  p_max integer,
  p_window_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid    uuid := auth.uid();
  v_bucket text;
  v_now    timestamptz := timezone('utc', now());
  v_count  integer;
begin
  -- Unauthenticated callers are denied (the BFF already requires a session, so
  -- this is just a safety net).
  if v_uid is null then
    return false;
  end if;

  v_bucket := p_kind || ':' || v_uid::text;

  insert into public.rate_limit_hits (bucket, window_start, count)
  values (v_bucket, v_now, 1)
  on conflict (bucket) do update
    set count = case
          when public.rate_limit_hits.window_start
               < v_now - make_interval(secs => p_window_seconds)
            then 1
          else public.rate_limit_hits.count + 1
        end,
        window_start = case
          when public.rate_limit_hits.window_start
               < v_now - make_interval(secs => p_window_seconds)
            then v_now
          else public.rate_limit_hits.window_start
        end
  returning rate_limit_hits.count into v_count;

  return v_count <= p_max;
end;
$$;

revoke all on function public.check_rate_limit(text, integer, integer) from public;
grant execute on function public.check_rate_limit(text, integer, integer) to authenticated;
