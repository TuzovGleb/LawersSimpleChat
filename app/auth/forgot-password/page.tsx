"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { AuthShell } from "@/components/auth-shell";
import { Loader2, MailCheck } from "lucide-react";

// Anti rate-limit / anti-enumeration: keep resend interval consistent so timing
// can't be used to probe whether an address exists.
const RESEND_COOLDOWN_SECONDS = 60;

export default function ForgotPasswordPage() {
  const { resetPassword } = useAuth();
  const { toast } = useToast();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setInterval(() => {
      setCooldown((s) => (s > 0 ? s - 1 : 0));
    }, 1000);
    return () => clearInterval(id);
  }, [cooldown]);

  const isValid = email.trim() !== "";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid || loading || cooldown > 0) return;

    setLoading(true);
    try {
      const { error } = await resetPassword(email);

      if (error) {
        // resetPasswordForEmail does NOT reveal whether the address exists, so any
        // error here is a real transport/rate-limit failure — surface it so the
        // user can retry (do NOT flip to the success state).
        const code = (error as { code?: string }).code || "";
        const status = (error as { status?: number }).status;
        const isRateLimited = status === 429 || code.includes("rate");
        toast({
          variant: "destructive",
          title: "Ошибка",
          description: isRateLimited
            ? "Слишком много попыток. Попробуйте позже."
            : "Не удалось связаться с сервером авторизации. Проверьте подключение к интернету и попробуйте снова.",
        });
        return;
      }

      // Generic success regardless of whether the email exists (no enumeration).
      setSent(true);
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      footer={
        <Link href="/auth" style={{ color: "var(--text-muted)" }}>
          Вернуться на вход
        </Link>
      }
    >
      <h1>Восстановление пароля</h1>
      <p className="lede">
        Укажите email, на который зарегистрирован аккаунт — мы отправим ссылку
        для сброса пароля.
      </p>

      {sent && (
        <div
          className="mb-5 rounded-lg p-4"
          style={{
            background: "var(--bg-soft)",
            border: "1px solid var(--border-soft)",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "20px 1fr",
              gap: 10,
              alignItems: "start",
            }}
          >
            <MailCheck size={18} style={{ color: "var(--brand-accent)", marginTop: 2 }} />
            <p style={{ margin: 0, fontSize: 14.5, color: "var(--text-secondary)", lineHeight: 1.5 }}>
              Если аккаунт существует, мы отправили на эту почту письмо с
              инструкциями по сбросу пароля.
            </p>
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} autoComplete="off" className="space-y-4">
        <div className="space-y-2">
          <Label
            htmlFor="forgot-email"
            style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}
          >
            Email
          </Label>
          <Input
            id="forgot-email"
            name="email"
            type="email"
            inputMode="email"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="name@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={loading}
            required
            autoComplete="email"
          />
        </div>

        <Button
          type="submit"
          className="btn btn-primary w-full"
          disabled={loading || !isValid || cooldown > 0}
        >
          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {loading
            ? "Отправляем…"
            : sent
              ? "Отправить повторно"
              : "Отправить ссылку"}
        </Button>

        {cooldown > 0 && (
          <p
            style={{
              margin: 0,
              fontSize: 13,
              textAlign: "center",
              color: "var(--text-muted)",
            }}
          >
            Повторить можно через {cooldown}&nbsp;с.
          </p>
        )}
      </form>
    </AuthShell>
  );
}
