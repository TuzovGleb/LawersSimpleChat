// 401 «Unauthorized» от auth-эндпоинтов — это не «неверный пароль» (он приходит
// как 400 Invalid login credentials от GoTrue), а отказ шлюза по anon-ключу:
// браузер держит закешированный старый бандл со старыми реквизитами Supabase
// (инцидент после катовера на self-hosted, 2026-08-10). Единственное лечение
// на стороне пользователя — полное обновление страницы.
export const STALE_APP_MESSAGE =
  "Приложение обновилось. Полностью обновите страницу (Ctrl+Shift+R, на Mac ⌘+Shift+R) и попробуйте снова.";

export function isStaleClientAuthError(error: unknown): boolean {
  return (error as { status?: number } | null)?.status === 401;
}
