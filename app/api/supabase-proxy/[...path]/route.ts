import { NextRequest } from 'next/server';
import { logLine } from '@/lib/server-logger';

/**
 * Transparent same-origin reverse proxy to Supabase.
 *
 * WHY: RU mobile carriers (МТС/Билайн/Мегафон) block/throttle *.supabase.co, so
 * the browser SDK's auth calls hang on cellular (desktop/Wi-Fi are fine). But
 * the site's OWN origin is obviously reachable from the phone (the page loaded),
 * and our server (Cloudflare/Yandex) CAN reach Supabase. So the browser calls
 * /api/supabase-proxy/* on our origin and we forward to Supabase server-side.
 *
 * Same-origin => no CORS / preflight. The destination host is FIXED from env
 * (not user input), so this is not an open proxy. Only auth is called from the
 * browser SDK today (see hooks/use-auth.ts), but any Supabase path is forwarded.
 *
 * Activated client-side by NEXT_PUBLIC_SUPABASE_PROXY_URL=/api/supabase-proxy.
 */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const UPSTREAM = (process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || '').replace(
  /\/$/,
  '',
);

// The browser carries the Supabase token under this header (not `Authorization`)
// so the Yandex Serverless Container platform doesn't intercept it as an IAM
// token and 403 the request. We restore it to `Authorization` below. Shared name
// with lib/supabase/client.ts.
const RELOCATED_AUTH_HEADER = 'x-sb-authorization';

// Headers we must not forward verbatim (host-specific / hop-by-hop / cookies).
const STRIP_REQ = new Set([
  'host',
  'connection',
  'content-length',
  'cookie', // don't leak our site session cookie to Supabase; auth uses apikey/bearer headers
  RELOCATED_AUTH_HEADER, // restored to `authorization` instead of forwarded as-is
]);
// Response headers that would corrupt the relayed (already-decoded) body.
const STRIP_RES = new Set(['content-encoding', 'content-length', 'transfer-encoding', 'connection']);

async function handle(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
  if (!UPSTREAM) {
    return Response.json({ error: 'supabase upstream not configured' }, { status: 500 });
  }

  const { path } = await ctx.params;
  const subPath = (path ?? []).join('/');
  const target = `${UPSTREAM}/${subPath}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIP_REQ.has(key.toLowerCase())) headers.set(key, value);
  });
  // Restore the Supabase Authorization header the browser relocated to dodge the
  // platform's IAM check (see RELOCATED_AUTH_HEADER).
  const relocatedAuth = req.headers.get(RELOCATED_AUTH_HEADER);
  if (relocatedAuth) headers.set('authorization', relocatedAuth);

  const method = req.method;
  const body = method === 'GET' || method === 'HEAD' ? undefined : await req.arrayBuffer();

  const started = Date.now();
  try {
    const upstream = await fetch(target, { method, headers, body, redirect: 'manual' });

    const resHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!STRIP_RES.has(key.toLowerCase())) resHeaders.set(key, value);
    });

    if (upstream.status >= 500) {
      logLine('WARN', 'supabase-proxy upstream error', {
        surface: 'bff',
        event: 'supabase_proxy',
        path: subPath,
        status: upstream.status,
        elapsed_ms: Date.now() - started,
      });
    }

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: resHeaders,
    });
  } catch (err) {
    logLine('ERROR', 'supabase-proxy fetch failed', {
      surface: 'bff',
      event: 'supabase_proxy',
      path: subPath,
      elapsed_ms: Date.now() - started,
      err,
    });
    return Response.json({ error: 'upstream unreachable' }, { status: 502 });
  }
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
export const HEAD = handle;
export const OPTIONS = handle;
