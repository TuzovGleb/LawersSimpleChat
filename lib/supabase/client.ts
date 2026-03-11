import { createBrowserClient } from '@supabase/ssr'

export function createClient() {
  // Проверяем наличие переменных окружения (могут отсутствовать во время сборки)
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '';
  
  // Если переменные отсутствуют, используем пустые строки
  // createBrowserClient все равно выбросит ошибку, но это произойдет во время выполнения,
  // а не во время сборки, если обернуть это в проверку на клиенте
  return createBrowserClient(supabaseUrl, supabaseAnonKey, {
    auth: { storageKey: 'sb-local-auth-token' },
  });
}

