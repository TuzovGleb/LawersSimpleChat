import { createServerClient, type CookieOptions } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'

export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({
    request: {
      headers: request.headers,
    },
  })

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        get(name: string) {
          return request.cookies.get(name)?.value
        },
        set(name: string, value: string, options: CookieOptions) {
          request.cookies.set({
            name,
            value,
            ...options,
          })
          response = NextResponse.next({
            request: {
              headers: request.headers,
            },
          })
          response.cookies.set({
            name,
            value,
            ...options,
          })
        },
        remove(name: string, options: CookieOptions) {
          request.cookies.set({
            name,
            value: '',
            ...options,
          })
          response = NextResponse.next({
            request: {
              headers: request.headers,
            },
          })
          response.cookies.set({
            name,
            value: '',
            ...options,
          })
        },
      },
    }
  )

  const {
    data: { user },
  } = await supabase.auth.getUser()

  const pathname = request.nextUrl.pathname

  // Redirect authenticated users from home page or auth page to workspace
  if (user && (pathname === '/' || pathname === '/auth')) {
    const url = request.nextUrl.clone()
    url.pathname = '/workspace'
    return NextResponse.redirect(url)
  }

  // Redirect unauthenticated users from protected pages to home
  const requiresAuth =
    pathname === '/workspace' ||
    pathname === '/chat' ||
    pathname.startsWith('/chat/') ||
    pathname === '/admin' ||
    pathname.startsWith('/admin/') ||
    pathname === '/onboarding' ||
    pathname.startsWith('/onboarding/')
  if (!user && requiresAuth) {
    const url = request.nextUrl.clone()
    url.pathname = '/'
    return NextResponse.redirect(url)
  }

  // Onboarding gate: authenticated users without a completed profile must fill
  // it in before using the app. Applies ONLY to page paths (/workspace, /chat*,
  // /admin*) — never to /api/** (the matcher lets API routes through here; a
  // redirect would break fetches), /auth/* (would break the PKCE reset-password
  // flow), '/' or /onboarding itself (redirect loop).
  if (user) {
    const profileCompleted = user.user_metadata?.profile_completed === true
    const isGatedPage =
      pathname === '/workspace' ||
      pathname === '/chat' ||
      pathname.startsWith('/chat/') ||
      pathname === '/admin' ||
      pathname.startsWith('/admin/')

    if (!profileCompleted && isGatedPage) {
      const url = request.nextUrl.clone()
      url.pathname = '/onboarding/profile'
      return NextResponse.redirect(url)
    }

    // A completed user has nothing to do on the onboarding page.
    if (
      profileCompleted &&
      (pathname === '/onboarding' || pathname.startsWith('/onboarding/'))
    ) {
      const url = request.nextUrl.clone()
      url.pathname = '/workspace'
      return NextResponse.redirect(url)
    }
  }

  return response
}

