import { NextResponse } from 'next/server'
import type { User } from '@supabase/supabase-js'
import { requireAuth } from '@/lib/auth-helpers'
import { createClient } from '@/lib/supabase/server'

type ServerSupabaseClient = Awaited<ReturnType<typeof createClient>>

interface AdminGateResult {
  user: User | null
  supabase: ServerSupabaseClient | null
  response: NextResponse | null
}

const forbidden = (): AdminGateResult => ({
  user: null,
  supabase: null,
  response: NextResponse.json({ error: 'Forbidden' }, { status: 403 }),
})

/**
 * Гейт «панельного доступа» для /api/admin/*: requireAuth → rpc('is_admin').
 * is_admin = наличие хотя бы одного admin.* пермишена (RBAC).
 * Использовать только там, где достаточно «любой админ» (например,
 * чтение справочников для админки); мутации и точечные чтения —
 * через requirePermission.
 *
 * FAIL-CLOSED: не админ ИЛИ любая ошибка RPC (функция не задеплоена,
 * транзиентная ошибка БД) → 403. Админ-доступ никогда не выдаётся «по ошибке».
 * Возвращает { user, supabase, response } по образцу requireAuth —
 * supabase переиспользуется роутом, чтобы не создавать клиент дважды.
 */
export async function requireAdmin(): Promise<AdminGateResult> {
  const { user, response: authResponse } = await requireAuth()
  if (authResponse) {
    return { user: null, supabase: null, response: authResponse }
  }

  try {
    const supabase = await createClient()
    const { data, error } = await supabase.rpc('is_admin')

    if (error || data !== true) {
      return forbidden()
    }

    return { user, supabase, response: null }
  } catch {
    return forbidden()
  }
}

/**
 * Гейт КОНКРЕТНОГО пермишена: requireAuth → rpc('has_permission', p_perm).
 * Пермишен считается выданным, если он есть хотя бы у одной роли пользователя.
 *
 * FAIL-CLOSED: нет пермишена ИЛИ любая ошибка RPC → 403,
 * форма результата — как у requireAdmin.
 */
export async function requirePermission(perm: string): Promise<AdminGateResult> {
  const { user, response: authResponse } = await requireAuth()
  if (authResponse) {
    return { user: null, supabase: null, response: authResponse }
  }

  try {
    const supabase = await createClient()
    const { data, error } = await supabase.rpc('has_permission', { p_perm: perm })

    if (error || data !== true) {
      return forbidden()
    }

    return { user, supabase, response: null }
  } catch {
    return forbidden()
  }
}
