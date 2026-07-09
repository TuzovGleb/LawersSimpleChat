import { redirect } from "next/navigation";
import { getAuthUser } from "@/lib/auth-helpers";
import { createClient } from "@/lib/supabase/server";
import { AdminDashboard } from "@/components/admin-dashboard";

// Страница читает cookies (auth) — статический prerender невозможен.
export const dynamic = "force-dynamic";

// Fail-closed: не залогинен → на лендинг; залогинен, но не админ (или RPC
// упал) → в воркспейс. Второй рубеж — requirePermission/requireAdmin в
// каждом /api/admin/*.
export default async function AdminPage() {
  const { user } = await getAuthUser();
  if (!user) {
    redirect("/");
  }

  const supabase = await createClient();
  const { data: isAdmin, error } = await supabase.rpc("is_admin");
  if (error || isAdmin !== true) {
    redirect("/workspace");
  }

  // Пермишены текущего админа определяют видимые вкладки и кнопки-мутации.
  // Ошибка RPC → пустой список (UI без разделов); это только UX — реальная
  // проверка прав всё равно на каждом /api/admin/*-роуте.
  const { data: perms } = await supabase.rpc("my_permissions");
  const permissions = Array.isArray(perms)
    ? perms.filter((p): p is string => typeof p === "string")
    : [];

  return (
    <main className="min-h-dvh">
      <AdminDashboard permissions={permissions} />
    </main>
  );
}
