/**
 * Structured single-line JSON logger for the Next.js server (BFF) — SSR and
 * /api route handlers.
 *
 * WHY: Yandex Cloud Logging splits a container's stdout on every newline, so a
 * `console.error('[tag]', someObject)` (which Node pretty-prints across many
 * lines, with `\n`-laden stack strings) is shattered into ~30 unrelated log
 * records that can't be grouped or filtered. Emitting ONE line of JSON per event
 * (JSON.stringify escapes newlines to `\\n`) keeps each event a single record;
 * Serverless Containers then lift `level`/`message` and expose the rest
 * (surface, chat_id, request_id, ...) as queryable `json_payload.*` fields.
 *
 * Mirrors the backend contract in backend/app/utils.py.
 */

type Level = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';

export interface LogFields {
  surface?: string; // defaults to 'bff'
  chat_id?: string | null;
  request_id?: string | null;
  event?: string;
  /** An Error (or anything thrown) — normalized into error_type/error_message/stack. */
  err?: unknown;
  [key: string]: unknown;
}

function serializeError(err: unknown): {
  error_type?: string;
  error_message?: string;
  stack?: string;
} {
  if (err instanceof Error) {
    return {
      error_type: err.name,
      error_message: err.message,
      stack: err.stack,
    };
  }
  if (err && typeof err === 'object') {
    const anyErr = err as Record<string, unknown>;
    return {
      error_type: typeof anyErr.name === 'string' ? anyErr.name : 'Error',
      error_message:
        typeof anyErr.message === 'string' ? anyErr.message : JSON.stringify(err),
      stack: typeof anyErr.stack === 'string' ? anyErr.stack : undefined,
    };
  }
  return { error_type: 'Error', error_message: String(err) };
}

export function logLine(level: Level, message: string, fields: LogFields = {}): void {
  const { surface = 'bff', err, ...rest } = fields;

  const record: Record<string, unknown> = {
    level,
    message,
    surface,
    timestamp: new Date().toISOString(),
    ...rest,
  };

  if (err !== undefined) {
    Object.assign(record, serializeError(err));
  }

  // Single physical line — JSON.stringify escapes embedded newlines.
  const line = JSON.stringify(record);
  // Route WARN/ERROR to stderr so the platform also tags the stream correctly.
  if (level === 'ERROR' || level === 'WARN') {
    // eslint-disable-next-line no-console
    console.error(line);
  } else {
    // eslint-disable-next-line no-console
    console.log(line);
  }
}

export const logger = {
  debug: (message: string, fields?: LogFields) => logLine('DEBUG', message, fields),
  info: (message: string, fields?: LogFields) => logLine('INFO', message, fields),
  warn: (message: string, fields?: LogFields) => logLine('WARN', message, fields),
  error: (message: string, fields?: LogFields) => logLine('ERROR', message, fields),
};

/**
 * Resolve a request id for correlation: reuse an inbound `X-Request-Id` (the
 * one the backend echoes / the browser sends) or mint a fresh one.
 */
export function requestIdFrom(req: { headers: Headers }): string {
  return req.headers.get('x-request-id') || crypto.randomUUID();
}
