import { useEffect, useState, useMemo } from 'react'
import { createClient } from '@/lib/supabase/client'
import { reportClientEvent } from '@/lib/client-error-logger'
import type { User } from '@supabase/supabase-js'

// supabase-js gives auth network calls NO timeout and serializes them behind a
// Web Lock acquired with "wait forever" (@supabase/auth-js `_acquireLock(-1)`).
// On a throttled/blocked mobile network (e.g. RU carriers vs *.supabase.co), or
// inside a frozen messenger in-app-WebView tab holding the lock,
// getSession()/signInWithPassword() can hang indefinitely. Behind the
// full-screen auth spinner that reads as "can't log in". We cap every auth call
// so the UI always makes forward progress — render the form, or show a real
// error — instead of hanging on "Загрузка…".
const SESSION_BOOTSTRAP_TIMEOUT_MS = 8_000
const SIGN_IN_TIMEOUT_MS = 12_000

class AuthTimeoutError extends Error {
  phase: string
  constructor(phase: string) {
    super(
      'Не удалось связаться с сервером авторизации. Проверьте подключение к интернету и попробуйте снова.',
    )
    this.name = 'AuthTimeoutError'
    this.phase = phase
  }
}

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function elapsedMsSince(t0: number): number {
  return Math.round(now() - t0)
}

function withTimeout<T>(promise: Promise<T>, ms: number, phase: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new AuthTimeoutError(phase)), ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

function safeHost(url?: string): string | undefined {
  try {
    return url ? new URL(url).host : undefined
  } catch {
    return undefined
  }
}

function hasWebLocks(): boolean {
  return typeof navigator !== 'undefined' && !!(navigator as Navigator).locks
}

// Best-effort detection of messenger in-app browsers (Telegram/VK/WhatsApp/…),
// where Web Locks + background freezing behave differently from a real browser.
const INAPP_WEBVIEW_RE =
  /FBAN|FBAV|Instagram|VKAndroidApp|Telegram|Line\/|MiuiBrowser|GSA\/|OKApp|WhatsApp/i

function isInAppWebview(): boolean {
  if (typeof navigator === 'undefined') return false
  return INAPP_WEBVIEW_RE.test(navigator.userAgent || '')
}

function envContext() {
  return {
    supabase_host: safeHost(process.env.NEXT_PUBLIC_SUPABASE_URL),
    has_web_locks: hasWebLocks(),
    is_inapp_webview: isInAppWebview(),
  }
}

export function useAuth() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // One id per mounted page so every auth event (bootstrap + sign-in) is
  // correlatable in the logs by request_id.
  const authRequestId = useMemo(() => {
    try {
      return crypto.randomUUID()
    } catch {
      return `auth-${Date.now()}-${Math.floor(Math.random() * 1e9)}`
    }
  }, [])

  // Создаем клиент только на клиенте, не во время SSR/SSG
  // Используем useMemo чтобы не пересоздавать клиент на каждом рендере
  const supabase = useMemo(() => {
    if (typeof window === 'undefined') return null;
    try {
      if (process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) {
        return createClient();
      }
    } catch (error) {
      console.error('Failed to create Supabase client:', error);
    }
    return null;
  }, []);

  useEffect(() => {
    if (!supabase) {
      setLoading(false);
      return;
    }

    let mounted = true;
    const t0 = now()

    reportClientEvent('auth_client_created', { request_id: authRequestId, ...envContext() })
    reportClientEvent('auth_getsession_start', { request_id: authRequestId })

    // Get initial session, but never let it hang the spinner forever.
    withTimeout(supabase.auth.getSession(), SESSION_BOOTSTRAP_TIMEOUT_MS, 'getSession')
      .then(({ data: { session }, error }) => {
        if (!mounted) return;
        if (error) {
          reportClientEvent(
            'auth_getsession_error',
            { request_id: authRequestId, elapsed_ms: elapsedMsSince(t0), error_type: error.name, message: error.message },
            'WARN',
          )
          setLoading(false);
          return;
        }
        reportClientEvent('auth_getsession_resolved', {
          request_id: authRequestId,
          elapsed_ms: elapsedMsSince(t0),
          has_session: !!session,
        })
        setUser(session?.user ?? null);
        setLoading(false);
      })
      .catch((error) => {
        if (!mounted) return;
        // CRITICAL: on timeout/network hang we still drop the spinner so the
        // login form RENDERS instead of getting stuck on "Загрузка…". If the
        // session was merely slow, onAuthStateChange (INITIAL_SESSION) below
        // still updates the user once it eventually arrives.
        reportClientEvent(
          'auth_getsession_timeout',
          { request_id: authRequestId, elapsed_ms: elapsedMsSince(t0), error_type: (error as Error)?.name, message: (error as Error)?.message, ...envContext() },
          'ERROR',
        )
        setLoading(false);
      });

    // Listen for auth changes
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
      setUser(session?.user ?? null);
      setLoading(false);
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, [supabase, authRequestId])

  const signIn = async (email: string, password: string) => {
    if (!supabase) {
      return { data: null, error: new Error('Supabase client is not available') };
    }
    // Mobile keyboards capitalize the first letter / append a trailing space;
    // GoTrue does not trim, so normalize before authenticating.
    const normalizedEmail = email.trim().toLowerCase()
    const t0 = now()
    reportClientEvent('auth_signin_start', { request_id: authRequestId, is_inapp_webview: isInAppWebview() })
    try {
      const { data, error } = await withTimeout(
        supabase.auth.signInWithPassword({ email: normalizedEmail, password }),
        SIGN_IN_TIMEOUT_MS,
        'signIn',
      )
      reportClientEvent(
        error ? 'auth_signin_rejected' : 'auth_signin_resolved',
        {
          request_id: authRequestId,
          elapsed_ms: elapsedMsSince(t0),
          ...(error ? { error_type: error.name, message: error.message, auth_status: (error as { status?: number }).status } : {}),
        },
        error ? 'WARN' : 'INFO',
      )
      return { data, error }
    } catch (timeoutOrNetwork) {
      reportClientEvent(
        'auth_signin_timeout',
        { request_id: authRequestId, elapsed_ms: elapsedMsSince(t0), error_type: (timeoutOrNetwork as Error)?.name, message: (timeoutOrNetwork as Error)?.message, ...envContext() },
        'ERROR',
      )
      return { data: null, error: timeoutOrNetwork as Error }
    }
  }

  const signUp = async (email: string, password: string) => {
    if (!supabase) {
      return { data: null, error: new Error('Supabase client is not available') };
    }
    const normalizedEmail = email.trim().toLowerCase()
    try {
      const { data, error } = await withTimeout(
        supabase.auth.signUp({ email: normalizedEmail, password }),
        SIGN_IN_TIMEOUT_MS,
        'signUp',
      )
      return { data, error }
    } catch (timeoutOrNetwork) {
      return { data: null, error: timeoutOrNetwork as Error }
    }
  }

  const signOut = async () => {
    if (!supabase) {
      return { error: new Error('Supabase client is not available') };
    }
    const { error } = await supabase.auth.signOut()
    return { error }
  }

  const resetPassword = async (email: string) => {
    if (!supabase) {
      return { data: null, error: new Error('Supabase client is not available') };
    }
    const { data, error } = await supabase.auth.resetPasswordForEmail(email.trim().toLowerCase(), {
      redirectTo: `${window.location.origin}/auth/reset-password`,
    })
    return { data, error }
  }

  return {
    user,
    loading,
    signIn,
    signUp,
    signOut,
    resetPassword,
  }
}
