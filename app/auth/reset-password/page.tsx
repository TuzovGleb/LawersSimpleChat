"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { AuthShell } from "@/components/auth-shell";
import { createClient } from "@/lib/supabase/client";
import { reportClientEvent } from "@/lib/client-error-logger";
import { Loader2, AlertTriangle } from "lucide-react";

type Phase = "verifying" | "ready" | "invalid" | "updating" | "done";

// Safety net independent of the SDK's own timeouts (lock cap 5s + getSession
// bootstrap 8s in use-auth). If the recovery session never materializes within
// this window the link is invalid/expired/cross-device — show an error, never
// hang on the spinner.
const RESET_READY_TIMEOUT_MS = 15_000;
const MIN_PASSWORD_LENGTH = 6;

export default function ResetPasswordPage() {
  const router = useRouter();
  const { toast } = useToast();
  const { updatePassword } = useAuth();

  const [phase, setPhase] = useState<Phase>("verifying");
  // Distinguishes the two "invalid" copies: a missing code_verifier means the
  // link was opened on a different device/browser than the one that requested it.
  const [wrongDevice, setWrongDevice] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  const supabase = useMemo(() => {
    if (typeof window === "undefined") return null;
    try {
      if (
        process.env.NEXT_PUBLIC_SUPABASE_URL &&
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
      ) {
        return createClient();
      }
    } catch (e) {
      console.error("Failed to create Supabase client:", e);
    }
    return null;
  }, []);

  // --- Detect the recovery session established by the PKCE ?code= callback. ---
  // The supabase client is a browser singleton, so the SDK's initialize() has
  // already auto-exchanged the ?code= (and stripped it from the URL). We MUST
  // NOT call exchangeCodeForSession ourselves — the verifier is already consumed.
  // For PKCE the callback emits SIGNED_IN (and replays INITIAL_SESSION to a late
  // subscriber), NEVER PASSWORD_RECOVERY — so we key readiness off session.user.
  useEffect(() => {
    if (!supabase) {
      setPhase("invalid");
      return;
    }

    const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
    const elapsed = () =>
      Math.round((typeof performance !== "undefined" ? performance.now() : Date.now()) - t0);

    // GoTrue redirects with ?error / #error params when the link is expired or
    // already used. _getSessionFromURL throws on these and emits no useful event,
    // so we read them off the URL ourselves and bail immediately. Never surface
    // the raw error_description (it can leak internals).
    const search = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const errorParam = search.get("error") || hash.get("error") || undefined;
    const errorCode =
      search.get("error_code") || hash.get("error_code") || undefined;
    const hasCode = !!search.get("code");

    reportClientEvent("auth_recovery_link_opened", {
      has_error: !!(errorParam || errorCode),
      has_code: hasCode,
    });

    if (errorParam || errorCode) {
      reportClientEvent(
        "auth_recovery_invalid",
        { reason: "url_error", error_code: errorCode },
        "WARN",
      );
      setWrongDevice(false);
      setPhase("invalid");
      return;
    }

    let resolved = false;

    const markReady = () => {
      if (resolved) return;
      resolved = true;
      reportClientEvent("auth_recovery_session_ready", { elapsed_ms: elapsed() });
      setPhase("ready");
    };

    const markInvalid = (reason: string) => {
      if (resolved) return;
      resolved = true;
      reportClientEvent(
        "auth_recovery_invalid",
        { reason, elapsed_ms: elapsed() },
        "WARN",
      );
      // A code is present but no session arrived => the exchange found no stored
      // verifier => the link was opened on a different device/browser.
      setWrongDevice(hasCode);
      setPhase("invalid");
    };

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if (session?.user) {
        markReady();
      } else if (event === "INITIAL_SESSION") {
        // initialize() finished without a recovery session.
        markInvalid("no_session");
      }
    });

    // Redundant probe: the recovery session is usually already saved by mount, so
    // even if we attach the listener after SIGNED_IN fired, this resolves us.
    supabase.auth
      .getSession()
      .then(({ data: { session } }) => {
        if (session?.user) markReady();
      })
      .catch(() => {
        /* ignore — the timeout/INITIAL_SESSION paths cover failure */
      });

    const timer = setTimeout(() => markInvalid("timeout"), RESET_READY_TIMEOUT_MS);

    return () => {
      subscription.unsubscribe();
      clearTimeout(timer);
    };
  }, [supabase]);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && confirm !== password;
  const isValid =
    password.length >= MIN_PASSWORD_LENGTH && confirm === password;

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid || phase === "updating") return;

    setPhase("updating");
    const { error } = await updatePassword(password);

    if (!error) {
      setPhase("done");
      toast({ title: "Пароль изменён", description: "Перенаправляем…" });
      router.replace("/workspace");
      router.refresh();
      return;
    }

    const code = (error as { code?: string }).code || "";
    const status = (error as { status?: number }).status;
    const name = (error as { name?: string }).name;

    if (name === "AuthTimeoutError") {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description:
          "Не удалось связаться с сервером авторизации. Проверьте подключение к интернету и попробуйте снова.",
      });
      setPhase("ready");
      return;
    }

    if (code === "same_password") {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Новый пароль не должен совпадать со старым.",
      });
      setPhase("ready");
      return;
    }

    if (code === "weak_password") {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пароль слишком простой. Используйте более надёжный.",
      });
      setPhase("ready");
      return;
    }

    if (status === 401 || name === "AuthSessionMissingError" || code.includes("session")) {
      // Recovery session expired mid-flow — send them back to request a new link.
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Сессия восстановления истекла. Запросите новую ссылку.",
      });
      setWrongDevice(false);
      setPhase("invalid");
      return;
    }

    toast({
      variant: "destructive",
      title: "Ошибка",
      description:
        (error as { message?: string }).message ||
        "Не удалось изменить пароль. Попробуйте снова.",
    });
    setPhase("ready");
  };

  return (
    <AuthShell
      footer={
        <Link href="/auth" style={{ color: "var(--text-muted)" }}>
          Вернуться на вход
        </Link>
      }
    >
      {phase === "verifying" && (
        <div style={{ textAlign: "center", padding: "24px 0" }}>
          <Loader2
            className="mx-auto h-7 w-7 animate-spin"
            style={{ color: "var(--brand-accent)" }}
          />
          <p style={{ marginTop: 16, color: "var(--text-secondary)" }}>
            Проверяем ссылку…
          </p>
        </div>
      )}

      {phase === "invalid" && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "22px 1fr",
              gap: 12,
              alignItems: "start",
              marginBottom: 8,
            }}
          >
            <AlertTriangle size={20} style={{ color: "var(--brand-accent)", marginTop: 4 }} />
            <h1 style={{ margin: 0 }}>Ссылка недействительна</h1>
          </div>
          <p className="lede">
            {wrongDevice
              ? "Ссылка недействительна для этого устройства. Откройте письмо в том же браузере, где запрашивали сброс, или запросите новую ссылку."
              : "Ссылка восстановления истекла или недействительна. Запросите новую."}
          </p>
          <Link
            href="/auth/forgot-password"
            className={cn(buttonVariants({ variant: "brand", size: "cta" }), "w-full")}
          >
            Запросить новую ссылку
          </Link>
        </>
      )}

      {(phase === "ready" || phase === "updating" || phase === "done") && (
        <>
          <h1>Новый пароль</h1>
          <p className="lede">
            Придумайте новый пароль для входа в аккаунт.
          </p>

          <form onSubmit={handleUpdate} autoComplete="off" className="space-y-4">
            <div className="space-y-2">
              <Label
                htmlFor="new-password"
                style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}
              >
                Новый пароль
              </Label>
              <Input
                id="new-password"
                name="new-password"
                type="password"
                placeholder="Минимум 6 символов"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={phase !== "ready"}
                required
                minLength={MIN_PASSWORD_LENGTH}
                autoComplete="new-password"
              />
              {tooShort && (
                <p style={{ margin: 0, fontSize: 13, color: "var(--brand-accent)" }}>
                  Пароль должен содержать минимум 6 символов
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label
                htmlFor="confirm-password"
                style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}
              >
                Повторите пароль
              </Label>
              <Input
                id="confirm-password"
                name="confirm-password"
                type="password"
                placeholder="Повторите новый пароль"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                disabled={phase !== "ready"}
                required
                minLength={MIN_PASSWORD_LENGTH}
                autoComplete="new-password"
              />
              {mismatch && (
                <p style={{ margin: 0, fontSize: 13, color: "var(--brand-accent)" }}>
                  Пароли не совпадают
                </p>
              )}
            </div>

            <Button
              type="submit"
              variant="brand"
              size="cta"
              className="w-full"
              disabled={!isValid || phase !== "ready"}
            >
              {(phase === "updating" || phase === "done") && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {phase === "updating" || phase === "done"
                ? "Сохраняем…"
                : "Сохранить пароль"}
            </Button>
          </form>
        </>
      )}
    </AuthShell>
  );
}
