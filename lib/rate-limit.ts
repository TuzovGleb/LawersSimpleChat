import type { createClient } from '@/lib/supabase/server';

type ServerSupabaseClient = Awaited<ReturnType<typeof createClient>>;

/**
 * Per-user rate limit backed by the Postgres check_rate_limit() function
 * (see supabase/migrations/20260702000100_rate_limit.sql). Shared across
 * serverless instances because the counter lives in the DB.
 *
 * Returns true when the action is ALLOWED, false when the limit is exceeded.
 *
 * FAILS OPEN: any error (function not deployed yet, transient DB error) returns
 * true, so a limiter fault can never take down the underlying feature. The
 * bucket key is derived from auth.uid() inside the SQL function, so a caller can
 * only ever rate-limit themselves — the `kind` is the only client-supplied part.
 */
export async function checkRateLimit(
  supabase: ServerSupabaseClient,
  kind: string,
  max: number,
  windowSeconds: number,
): Promise<boolean> {
  try {
    const { data, error } = await supabase.rpc('check_rate_limit', {
      p_kind: kind,
      p_max: max,
      p_window_seconds: windowSeconds,
    });
    if (error) return true; // fail open
    return data !== false;
  } catch {
    return true; // fail open
  }
}
