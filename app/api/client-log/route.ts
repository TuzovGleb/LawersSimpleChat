import { NextRequest, NextResponse } from 'next/server';
import { logLine } from '@/lib/server-logger';

// Receives browser error reports and writes them as one structured JSON line to
// stdout (surface=client), so they reach the same Cloud Logging group as
// backend/BFF logs and are filterable by json_payload.chat_id.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MAX_BODY_BYTES = 32 * 1024;
const MAX_MESSAGE = 2_000;
const MAX_STACK = 8_000;

type Level = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';
const LEVELS: readonly Level[] = ['DEBUG', 'INFO', 'WARN', 'ERROR'];

function clip(value: unknown, max: number): string | undefined {
  return typeof value === 'string' ? value.slice(0, max) : undefined;
}

function asLevel(value: unknown): Level {
  return typeof value === 'string' && (LEVELS as readonly string[]).includes(value)
    ? (value as Level)
    : 'ERROR';
}

function asFiniteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export async function POST(req: NextRequest) {
  try {
    const raw = await req.text();
    if (raw.length > MAX_BODY_BYTES) {
      return NextResponse.json({ ok: false, error: 'payload too large' }, { status: 413 });
    }

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return NextResponse.json({ ok: false, error: 'invalid JSON' }, { status: 400 });
    }

    logLine(asLevel(data.level), clip(data.message, MAX_MESSAGE) ?? 'client error', {
      surface: 'client',
      chat_id: typeof data.chat_id === 'string' ? data.chat_id : null,
      request_id: typeof data.request_id === 'string' ? data.request_id : null,
      event: typeof data.event === 'string' ? data.event : 'client-error',
      error_type: typeof data.error_type === 'string' ? data.error_type : undefined,
      stack: clip(data.stack, MAX_STACK),
      url: clip(data.url, 1_000),
      user_agent: clip(data.userAgent, 500),
      // Auth diagnostics (see hooks/use-auth.ts): the decisive signals for the
      // mobile-login hang are elapsed_ms (huge/never = network/lock hang) plus
      // the environment flags.
      elapsed_ms: asFiniteNumber(data.elapsed_ms),
      auth_status: asFiniteNumber(data.auth_status),
      phase: clip(data.phase, 64),
      supabase_host: clip(data.supabase_host, 256),
      has_web_locks: typeof data.has_web_locks === 'boolean' ? data.has_web_locks : undefined,
      is_inapp_webview: typeof data.is_inapp_webview === 'boolean' ? data.is_inapp_webview : undefined,
      has_session: typeof data.has_session === 'boolean' ? data.has_session : undefined,
    });

    return NextResponse.json({ ok: true });
  } catch (error) {
    logLine('ERROR', 'client-log handler failed', { surface: 'bff', err: error });
    return NextResponse.json({ ok: false }, { status: 500 });
  }
}
