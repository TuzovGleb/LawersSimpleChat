import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { requireAuth } from '@/lib/auth-helpers';
import { checkRateLimit } from '@/lib/rate-limit';
import { mapEntitlementJson } from '@/lib/entitlement';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'nodejs';

// BFF-side limit is only the second line of defense (fail-open, see
// lib/rate-limit.ts). The authoritative limiter runs INSIDE the redeem_promo
// SQL function: the RPC is exposed to authenticated callers directly via
// PostgREST, bypassing this route entirely.
const REDEEM_RATE_MAX = 10;
const REDEEM_RATE_WINDOW_SECONDS = 3600;

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { user, response: authResponse } = await requireAuth();
  if (authResponse) return authResponse;

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const code = typeof body?.code === 'string' ? body.code.trim() : '';
  if (!code) {
    return NextResponse.json(
      { ok: false, error: 'Промокод недействителен или истёк' },
      { status: 400 },
    );
  }

  try {
    const supabase = await createClient();

    if (!(await checkRateLimit(supabase, 'promo_redeem_bff', REDEEM_RATE_MAX, REDEEM_RATE_WINDOW_SECONDS))) {
      logger.warn('Promo redeem rate limit hit (BFF)', {
        request_id: requestId,
        event: 'rate_limited',
        user_id: user!.id,
      });
      return NextResponse.json(
        { ok: false, error: 'Слишком много попыток. Попробуйте через час' },
        { status: 429 },
      );
    }

    // NB: the code itself is deliberately kept out of logs — promo codes are
    // bearer secrets (promo_codes has deny-all RLS for the same reason).
    const { data, error } = await supabase.rpc('redeem_promo', { p_code: code });

    if (error) {
      logger.error('redeem_promo RPC failed', {
        request_id: requestId,
        event: 'promo_rpc_error',
        user_id: user!.id,
        err: error,
      });
      return NextResponse.json(
        { error: 'Не удалось применить промокод. Попробуйте позже.' },
        { status: 503 },
      );
    }

    const result = (data ?? {}) as { ok?: boolean; error?: string; entitlement?: unknown };

    if (result.ok === true) {
      const entitlement = mapEntitlementJson(result.entitlement);
      logger.info('Promo code redeemed', {
        request_id: requestId,
        event: 'promo_redeemed',
        duration_ms: Date.now() - startedAt,
        user_id: user!.id,
      });
      return NextResponse.json({ ok: true, entitlement });
    }

    switch (result.error) {
      case 'already_used':
        return NextResponse.json(
          { ok: false, error: 'Вы уже использовали этот промокод' },
          { status: 400 },
        );
      case 'rate_limited':
        logger.warn('Promo redeem rate limit hit (SQL)', {
          request_id: requestId,
          event: 'rate_limited',
          user_id: user!.id,
        });
        return NextResponse.json(
          { ok: false, error: 'Слишком много попыток. Попробуйте через час' },
          { status: 429 },
        );
      default:
        // Anti-enumeration: SQL collapses "not found / disabled / expired /
        // exhausted" into a single 'invalid' — mirror that here.
        return NextResponse.json(
          { ok: false, error: 'Промокод недействителен или истёк' },
          { status: 400 },
        );
    }
  } catch (error) {
    logger.error('Unexpected error redeeming promo code', {
      request_id: requestId,
      event: 'promo_unexpected_error',
      err: error,
    });
    return NextResponse.json(
      { error: 'Не удалось применить промокод. Попробуйте позже.' },
      { status: 503 },
    );
  }
}
