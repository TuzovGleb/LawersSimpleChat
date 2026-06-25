import { createBrowserClient } from '@supabase/ssr'

/**
 * Web Lock wrapper with a finite acquisition timeout.
 *
 * supabase-js serializes every auth call (getSession, signInWithPassword, token
 * refresh, …) behind an exclusive `navigator.locks` lock acquired with
 * acquireTimeout = -1 — i.e. WAIT FOREVER (see @supabase/auth-js
 * GoTrueClient `_acquireLock(-1, …)`). In a frozen background tab or a messenger
 * in-app WebView the lock holder can stall, so a fresh call would hang
 * indefinitely and the auth bootstrap would never resolve. We cap the wait: if
 * the lock isn't granted within `timeoutMs`, run the operation WITHOUT the lock
 * rather than hang. (The network call itself is separately timed out in
 * useAuth, so neither layer can stick forever.)
 */
function lockWithTimeout(timeoutMs: number) {
  return async function lock<R>(
    name: string,
    _acquireTimeout: number,
    fn: () => Promise<R>,
  ): Promise<R> {
    const locks =
      typeof navigator !== 'undefined'
        ? (navigator as Navigator).locks
        : undefined
    if (!locks) {
      // Old WebView / private mode without Web Locks — supabase's own fallback
      // is also "run without a lock".
      return await fn()
    }

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      return await locks.request(
        name,
        { mode: 'exclusive', signal: controller.signal },
        async () => await fn(),
      )
    } catch (err) {
      // AbortError => we waited too long to ACQUIRE the lock; proceed without it
      // instead of leaving the user stuck. Any other error is a real failure.
      if (err && (err as { name?: string }).name === 'AbortError') {
        return await fn()
      }
      throw err
    } finally {
      clearTimeout(timer)
    }
  }
}

/**
 * When NEXT_PUBLIC_SUPABASE_PROXY_URL is set, route the browser SDK's calls to
 * Supabase through it instead of hitting *.supabase.co directly (which RU mobile
 * carriers block). Recommended value: "/api/supabase-proxy" — a same-origin path
 * served by app/api/supabase-proxy, reachable from the phone since the site
 * itself loaded. We rewrite only requests aimed at the Supabase ORIGIN, so the
 * Supabase URL (and thus the auth cookie name / project ref) is unchanged and
 * the server-side clients keep talking to Supabase directly.
 */
function proxiedFetch(supabaseUrl: string, proxyBase: string): typeof fetch {
  let origin = '';
  try {
    origin = new URL(supabaseUrl).origin;
  } catch {
    /* leave empty => no rewriting */
  }
  return ((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    try {
      const reqUrl =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (origin && reqUrl.startsWith(origin)) {
        const rewritten = proxyBase + reqUrl.slice(origin.length);
        return typeof input === 'string' || input instanceof URL
          ? fetch(rewritten, init)
          : fetch(new Request(rewritten, input), init);
      }
    } catch {
      /* fall back to a direct fetch below */
    }
    return fetch(input as RequestInfo | URL, init);
  }) as typeof fetch;
}

export function createClient() {
  // Проверяем наличие переменных окружения (могут отсутствовать во время сборки)
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '';
  const proxyBase = (process.env.NEXT_PUBLIC_SUPABASE_PROXY_URL || '').replace(/\/$/, '');

  // Если переменные отсутствуют, используем пустые строки
  // createBrowserClient все равно выбросит ошибку, но это произойдет во время выполнения,
  // а не во время сборки, если обернуть это в проверку на клиенте
  return createBrowserClient(supabaseUrl, supabaseAnonKey, {
    auth: {
      // Never let lock contention (frozen tab / in-app WebView) hang auth forever.
      lock: lockWithTimeout(5_000),
    },
    ...(proxyBase ? { global: { fetch: proxiedFetch(supabaseUrl, proxyBase) } } : {}),
  });
}
