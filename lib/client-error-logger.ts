/**
 * Browser-side error reporter. Ships uncaught client errors to the server so
 * they land in the same Cloud Logging group as backend/BFF logs, keyed by
 * chat_id — previously client errors lived only in the user's devtools console
 * and were lost.
 *
 * Transport: POST /api/client-log (sendBeacon when available so the report
 * survives a page unload). The reporter NEVER throws.
 */

let currentChatId: string | null = null;
let installed = false;

/** Keep the active chat id so global (window) error handlers can attach it. */
export function setCurrentChatId(id: string | null): void {
  currentChatId = id ?? null;
}

interface ClientErrorContext {
  chat_id?: string | null;
  event?: string;
  [key: string]: unknown;
}

function normalize(error: unknown): {
  message: string;
  error_type: string;
  stack?: string;
} {
  if (error instanceof Error) {
    return { message: error.message, error_type: error.name, stack: error.stack };
  }
  if (error && typeof error === 'object') {
    const anyErr = error as Record<string, unknown>;
    return {
      message:
        typeof anyErr.message === 'string' ? anyErr.message : 'Unknown client error',
      error_type: typeof anyErr.name === 'string' ? anyErr.name : 'Error',
      stack: typeof anyErr.stack === 'string' ? anyErr.stack : undefined,
    };
  }
  return { message: String(error), error_type: 'Error' };
}

function newRequestId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `c-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
  }
}

export function reportClientError(
  error: unknown,
  context: ClientErrorContext = {},
): void {
  if (typeof window === 'undefined') return;
  try {
    const err = normalize(error);
    const payload = {
      level: 'ERROR',
      surface: 'client',
      message: err.message,
      error_type: err.error_type,
      stack: err.stack,
      chat_id: context.chat_id ?? currentChatId,
      request_id: newRequestId(),
      url: window.location?.href,
      userAgent: navigator?.userAgent,
      ...context,
    };
    const body = JSON.stringify(payload);

    if (navigator?.sendBeacon) {
      navigator.sendBeacon(
        '/api/client-log',
        new Blob([body], { type: 'application/json' }),
      );
    } else {
      void fetch('/api/client-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => {
        /* swallow: reporting must never surface to the user */
      });
    }
  } catch {
    /* never throw from the error reporter */
  }
}

/**
 * Ship a structured, non-error telemetry event to the same log pipeline as
 * reportClientError (one JSON line, surface=client). Used for auth diagnostics
 * (getSession/signIn timings, in-app-webview / Web-Locks flags) so a failing
 * mobile session is observable even though we cannot reproduce it locally.
 * NEVER throws.
 */
export function reportClientEvent(
  event: string,
  fields: Record<string, unknown> = {},
  level: 'INFO' | 'WARN' | 'ERROR' = 'INFO',
): void {
  if (typeof window === 'undefined') return;
  try {
    const payload = {
      level,
      surface: 'client',
      message: event,
      event,
      chat_id: currentChatId,
      request_id:
        typeof fields.request_id === 'string' ? fields.request_id : newRequestId(),
      url: window.location?.href,
      userAgent: navigator?.userAgent,
      ...fields,
    };
    const body = JSON.stringify(payload);

    if (navigator?.sendBeacon) {
      navigator.sendBeacon(
        '/api/client-log',
        new Blob([body], { type: 'application/json' }),
      );
    } else {
      void fetch('/api/client-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => {
        /* swallow: telemetry must never surface to the user */
      });
    }
  } catch {
    /* never throw from telemetry */
  }
}

/** Install the global window error / unhandledrejection listeners (idempotent). */
export function installGlobalErrorReporting(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  window.addEventListener('error', (e: ErrorEvent) => {
    reportClientError(e.error ?? e.message, {
      event: 'window.onerror',
      source: e.filename,
      line: e.lineno,
      column: e.colno,
    });
  });

  window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
    reportClientError(e.reason, { event: 'unhandledrejection' });
  });
}
