import { type NextRequest } from 'next/server'
import { updateSession } from '@/lib/supabase/middleware'

export async function middleware(request: NextRequest) {
  return await updateSession(request)
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api/supabase-proxy (transparent Supabase reverse proxy — must not trigger
     *   its own getUser()/redirect; it just forwards bytes)
     * - email/ (шаблоны писем из public/email: GoTrue тянет их ПО HTTP в момент
     *   отправки, и getUser() здесь означал бы запрос в GoTrue из обработки
     *   письма, которое GoTrue же и отправляет — бессмысленная петля)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * Feel free to modify this pattern to include more paths.
     */
    '/((?!api/supabase-proxy|email/|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
}

