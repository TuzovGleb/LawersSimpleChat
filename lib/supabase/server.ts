import { createServerClient, type CookieOptions } from '@supabase/ssr'
import { cookies } from 'next/headers'

export async function createClient() {
  // SUPABASE_URL allows server-side override (e.g. Docker internal network)
  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

  if (!supabaseUrl || !supabaseAnonKey) {
    const missingVars = []
    if (!supabaseUrl) missingVars.push('NEXT_PUBLIC_SUPABASE_URL')
    if (!supabaseAnonKey) missingVars.push('NEXT_PUBLIC_SUPABASE_ANON_KEY')
    
    console.error('[supabase/server] Missing required environment variables:', missingVars.join(', '))
    throw new Error(
      `Supabase configuration error: Missing required environment variables: ${missingVars.join(', ')}`
    )
  }

  let cookieStore
  try {
    cookieStore = await cookies()
  } catch (error) {
    console.error('[supabase/server] Failed to access cookies:', error)
    throw new Error('Failed to access cookies. This may occur in certain Next.js contexts.')
  }

  try {
    return createServerClient(
      supabaseUrl,
      supabaseAnonKey,
      {
        auth: { storageKey: 'sb-local-auth-token' },
        cookies: {
          get(name: string) {
            return cookieStore.get(name)?.value
          },
          set(name: string, value: string, options: CookieOptions) {
            try {
              cookieStore.set({ name, value, ...options })
            } catch (error) {
              // The `set` method was called from a Server Component.
              // This can be ignored if you have middleware refreshing
              // user sessions.
            }
          },
          remove(name: string, options: CookieOptions) {
            try {
              cookieStore.set({ name, value: '', ...options })
            } catch (error) {
              // The `delete` method was called from a Server Component.
              // This can be ignored if you have middleware refreshing
              // user sessions.
            }
          },
        },
      }
    )
  } catch (error) {
    console.error('[supabase/server] Failed to create Supabase client:', error)
    throw new Error('Failed to create Supabase client. Check your configuration.')
  }
}

