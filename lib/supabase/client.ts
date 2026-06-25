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
// supabase-js sends `Authorization: Bearer <jwt>` on every call. Our proxy host
// (Yandex Serverless Container) intercepts any `Authorization: Bearer …` and
// tries to validate it as its own IAM token — the Supabase JWT isn't one, so the
// platform rejects the request with 403 "Not authorized" BEFORE it reaches our
// route. So on the browser->proxy hop we carry the token under a header the
// platform ignores (x-sb-authorization); the proxy route restores it to
// Authorization before forwarding to Supabase. The header name is shared with
// app/api/supabase-proxy.
const RELOCATED_AUTH_HEADER = 'x-sb-authorization';

function relocateAuthHeader(headers: Headers): Headers {
  const auth = headers.get('authorization');
  if (auth) {
    headers.delete('authorization');
    headers.set(RELOCATED_AUTH_HEADER, auth);
  }
  return headers;
}

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
        if (typeof input === 'string' || input instanceof URL) {
          const headers = relocateAuthHeader(new Headers(init?.headers));
          return fetch(rewritten, { ...init, headers });
        }
        // Request object (rare for supabase-js): override headers via init.
        const headers = relocateAuthHeader(new Headers(input.headers));
        return fetch(new Request(rewritten, input), { ...init, headers });
      }
    } catch {
      /* fall back to a direct fetch below */
    }
    return fetch(input as RequestInfo | URL, init);
  }) as typeof fetch;
}

// The browser routes its Supabase calls through our same-origin proxy by
// DEFAULT — RU mobile carriers block *.supabase.co directly, so a direct call
// hangs on cellular. This path ships with the app (app/api/supabase-proxy), so
// it needs no env/infra. Override via NEXT_PUBLIC_SUPABASE_PROXY_URL to point at
// a different proxy, or set it to "direct"/"off" to talk to Supabase straight.
const DEFAULT_PROXY_BASE = '/api/supabase-proxy';

function resolveProxyBase(): string {
  const env = (process.env.NEXT_PUBLIC_SUPABASE_PROXY_URL || '').trim();
  if (env.toLowerCase() === 'direct' || env.toLowerCase() === 'off') return '';
  return (env || DEFAULT_PROXY_BASE).replace(/\/$/, '');
}

export function createClient() {
  // Проверяем наличие переменных окружения (могут отсутствовать во время сборки)
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '';
  const proxyBase = resolveProxyBase();

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
