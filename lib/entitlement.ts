import { NextResponse } from 'next/server';
import type { createClient } from '@/lib/supabase/server';

type ServerSupabaseClient = Awaited<ReturnType<typeof createClient>>;

/**
 * Contract shared with the Postgres get_my_entitlement() function
 * (supabase/migrations/20260709000000_billing_and_profiles.sql +
 * 20260709120000_admin_permanent_access.sql):
 *   {"status":"active"|"expired"|"none","kind":"trial"|"promo"|"manual"|"payment"|"admin"|null,"expires_at":"<ISO>"|null}
 *
 * kind='admin' is COMPUTED (never stored in access_grants): a user holding at
 * least one admin.* permission — the public.is_admin() criterion — is always
 * {"status":"active","kind":"admin","expires_at":null}, i.e. permanent access
 * with a null expiry.
 *
 * The snake_case→camelCase mapping (expires_at → expiresAt) happens HERE and
 * only here — every other layer (routes, banner, admin) works with this TS shape.
 * Gate rule everywhere: access ⟺ status === 'active'; kind/expiresAt are only
 * for banner texts.
 */
export type Entitlement = {
  status: 'active' | 'expired' | 'none';
  kind: 'trial' | 'promo' | 'manual' | 'payment' | 'admin' | null;
  expiresAt: string | null;
};

const STATUSES: ReadonlyArray<Entitlement['status']> = ['active', 'expired', 'none'];
const KINDS: ReadonlyArray<NonNullable<Entitlement['kind']>> = [
  'trial',
  'promo',
  'manual',
  'payment',
  'admin',
];

/**
 * Map the raw JSONB payload returned by SQL (get_my_entitlement / redeem_promo)
 * into the TS Entitlement shape. Returns null for anything malformed.
 */
export function mapEntitlementJson(raw: unknown): Entitlement | null {
  if (!raw || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;
  const status = STATUSES.find((s) => s === record.status);
  if (!status) return null;
  const kind = KINDS.find((k) => k === record.kind) ?? null;
  const expiresAt = typeof record.expires_at === 'string' ? record.expires_at : null;
  return { status, kind, expiresAt };
}

/**
 * Fetch the caller's entitlement via rpc('get_my_entitlement').
 * NEVER throws: any RPC/transport error (or malformed payload) → null, so a
 * read-only consumer (e.g. GET /api/projects) can degrade gracefully.
 */
export async function getEntitlement(
  supabase: ServerSupabaseClient,
): Promise<Entitlement | null> {
  try {
    const { data, error } = await supabase.rpc('get_my_entitlement');
    if (error) return null;
    return mapEntitlementJson(data);
  } catch {
    return null;
  }
}

/**
 * Access gate for expensive POST routes (shape mirrors requireAuth in
 * lib/auth-helpers.ts). FAIL-CLOSED: if the entitlement cannot be verified
 * (RPC error / malformed payload) the caller gets a 503, not a free pass.
 * Non-active status → 402 with code SUBSCRIPTION_REQUIRED so the client can
 * flip into read-only mode. Never throws.
 */
export async function requireEntitlement(
  supabase: ServerSupabaseClient,
): Promise<{ entitlement: Entitlement | null; response: NextResponse | null }> {
  const entitlement = await getEntitlement(supabase);

  if (!entitlement) {
    return {
      entitlement: null,
      response: NextResponse.json(
        { error: 'Проверка доступа временно недоступна. Попробуйте позже.' },
        { status: 503 },
      ),
    };
  }

  if (entitlement.status !== 'active') {
    return {
      entitlement,
      response: NextResponse.json(
        {
          error: 'Доступ приостановлен. Свяжитесь с нами, чтобы продолжить работу.',
          code: 'SUBSCRIPTION_REQUIRED',
          entitlement,
        },
        { status: 402 },
      ),
    };
  }

  return { entitlement, response: null };
}
