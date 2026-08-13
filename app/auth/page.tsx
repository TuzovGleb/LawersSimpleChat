import { AuthForm } from "@/components/auth-form";

export const metadata = {
  title: "Авторизация | Джейхелпер",
  description: "Войдите в свой аккаунт или создайте новый",
};

export default async function AuthPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab } = await searchParams;
  return <AuthForm defaultTab={tab === "registration" ? "registration" : "authorization"} />;
}

