"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { AuthShell } from "@/components/auth-shell";
import { createClient } from "@/lib/supabase/client";
import { normalizeRuPhone, validateRuPhone } from "@/lib/phone";
import { CALENDLY_URL } from "@/lib/contact";
import { Loader2 } from "lucide-react";

// У supabase-вызовов нет собственного таймаута (см. комментарий к withTimeout
// в hooks/use-auth.ts — тот же паттерн): зависший RPC не должен ни на что
// влиять, по истечении срока просто остаёмся на fail-open рендере.
const SIGNUP_GATE_TIMEOUT_MS = 8_000;

function withTimeout<T>(promise: PromiseLike<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("is_signup_enabled timeout")),
      ms,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export function AuthForm() {
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [signupFirstName, setSignupFirstName] = useState("");
  const [signupLastName, setSignupLastName] = useState("");
  const [signupPhone, setSignupPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const { signIn, signUp, user, loading: authLoading } = useAuth();
  const router = useRouter();
  const { toast } = useToast();

  const isSignupEnabled = process.env.NEXT_PUBLIC_ENABLE_SIGNUP === "true";
  const defaultTab = "authorization";

  // Runtime-выключатель регистрации (billing_settings.signup_enabled, тумблер
  // в /admin). Env-флаг лишь прячет форму per-deploy; настоящий запрет — в
  // БД-триггере on_auth_user_signup_gate, поэтому ошибка/таймаут RPC =
  // fail-open (форму показываем). Default true: до ответа RPC рендерим как
  // сейчас — без мигания и без спиннеров.
  const [signupAllowed, setSignupAllowed] = useState(true);

  useEffect(() => {
    if (!isSignupEnabled) return;
    if (
      !process.env.NEXT_PUBLIC_SUPABASE_URL ||
      !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
    ) {
      return;
    }

    let cancelled = false;

    try {
      const supabase = createClient();
      withTimeout(supabase.rpc("is_signup_enabled"), SIGNUP_GATE_TIMEOUT_MS)
        .then(({ data, error }) => {
          if (cancelled || error) return; // fail-open
          if (data === false) {
            setSignupAllowed(false);
          }
        })
        .catch(() => {
          // Таймаут/сеть → fail-open: БД-триггер всё равно отобьёт signup.
        });
    } catch (error) {
      console.error("Failed to create Supabase client:", error);
    }

    return () => {
      cancelled = true;
    };
  }, [isSignupEnabled]);

  useEffect(() => {
    if (!authLoading && user) {
      router.replace("/workspace");
    }
  }, [user, authLoading, router]);

  const isLoginFormValid =
    loginEmail.trim() !== "" &&
    loginPassword.trim() !== "" &&
    loginPassword.length >= 6;

  const isSignupFormValid =
    signupFirstName.trim().length >= 2 &&
    signupLastName.trim().length >= 2 &&
    signupPhone.trim() !== "" &&
    signupEmail.trim() !== "" &&
    signupPassword.trim() !== "" &&
    signupPassword.length >= 6 &&
    confirmPassword.trim() !== "" &&
    signupPassword === confirmPassword;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!loginEmail || !loginPassword) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пожалуйста, заполните все поля",
      });
      return;
    }

    if (loginPassword.length < 6) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пароль должен содержать минимум 6 символов",
      });
      return;
    }

    setLoading(true);

    try {
      const { error } = await signIn(loginEmail, loginPassword);

      if (error) {
        throw error;
      }

      toast({
        title: "Вход выполнен",
        description: "Добро пожаловать!",
      });

      router.push("/workspace");
      router.refresh();
    } catch (error: any) {
      console.error("Auth error:", error);
      toast({
        variant: "destructive",
        title: "Ошибка входа",
        description:
          error.message || "Проверьте введенные данные и попробуйте снова",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCalendlyClick = () => {
    window.open(CALENDLY_URL, "_blank");
  };

  // Нормализуем телефон к канону +7XXXXXXXXXX на blur, чтобы пользователь
  // сразу видел, как номер будет сохранён (валидация — при сабмите).
  const handlePhoneBlur = () => {
    const normalized = normalizeRuPhone(signupPhone);
    if (normalized) {
      setSignupPhone(normalized);
    }
  };

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();

    if (
      !signupFirstName.trim() ||
      !signupLastName.trim() ||
      !signupPhone.trim() ||
      !signupEmail ||
      !signupPassword ||
      !confirmPassword
    ) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пожалуйста, заполните все поля",
      });
      return;
    }

    if (signupFirstName.trim().length < 2) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Имя должно содержать минимум 2 символа",
      });
      return;
    }

    if (signupLastName.trim().length < 2) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Фамилия должна содержать минимум 2 символа",
      });
      return;
    }

    const normalizedPhone = normalizeRuPhone(signupPhone);
    if (!normalizedPhone || !validateRuPhone(normalizedPhone)) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Укажите настоящий номер телефона в формате +7...",
      });
      return;
    }

    if (signupPassword.length < 6) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пароль должен содержать минимум 6 символов",
      });
      return;
    }

    if (signupPassword !== confirmPassword) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пароли не совпадают",
      });
      return;
    }

    setLoading(true);

    try {
      const { data, error } = await signUp(signupEmail, signupPassword, {
        firstName: signupFirstName.trim(),
        lastName: signupLastName.trim(),
        phone: normalizedPhone,
      });

      if (error) {
        throw error;
      }

      if (data?.user) {
        if (!data.session) {
          toast({
            title: "Проверьте почту",
            description:
              "На вашу почту отправлено письмо для подтверждения аккаунта.",
          });
          return;
        }

        const { error: signInError } = await signIn(signupEmail, signupPassword);

        if (signInError) {
          throw signInError;
        }

        toast({
          title: "Регистрация выполнена",
          description: "Добро пожаловать!",
        });

        router.push("/workspace");
        router.refresh();
      }
    } catch (error: any) {
      console.error("Signup error:", error);

      const rawMessage = (error?.message || "").toLowerCase();
      const isUserExists =
        rawMessage.includes("already registered") ||
        rawMessage.includes("already exists") ||
        rawMessage.includes("user already") ||
        error?.code === "user_already_exists";

      if (isUserExists) {
        toast({
          variant: "destructive",
          title: "Пользователь уже существует",
          description:
            "Аккаунт с таким email уже зарегистрирован. Перейдите на вкладку «Авторизация», чтобы войти.",
        });
      } else {
        toast({
          variant: "destructive",
          title: "Ошибка регистрации",
          description:
            error.message || "Проверьте введенные данные и попробуйте снова",
        });
      }
    } finally {
      setLoading(false);
    }
  };

  if (authLoading || user) {
    return (
      <div
        className="flex min-h-dvh items-center justify-center"
        style={{ background: "var(--bg)" }}
      >
        <div className="text-center">
          <div
            className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid"
            style={{
              borderColor: "var(--brand-accent)",
              borderRightColor: "transparent",
            }}
          />
          <p className="mt-4" style={{ color: "var(--text-secondary)" }}>
            Загрузка…
          </p>
        </div>
      </div>
    );
  }

  return (
    <AuthShell
      footer={
        <Link href="/" style={{ color: "var(--text-muted)" }}>
          На главную
        </Link>
      }
    >
      {isSignupEnabled && signupAllowed ? (
        <>
          <h1>Войти в Джейхелпер</h1>
          <p className="lede">Введите email и пароль</p>

          <Tabs defaultValue={defaultTab} className="w-full">
            <TabsList className="grid grid-cols-2 w-full" style={{ background: "var(--bg-soft)", borderRadius: 8, padding: 4, height: "auto" }}>
              <TabsTrigger value="authorization" style={{ borderRadius: 6 }}>
                Авторизация
              </TabsTrigger>
              <TabsTrigger value="registration" style={{ borderRadius: 6 }}>
                Регистрация
              </TabsTrigger>
            </TabsList>

            <TabsContent value="registration" className="mt-6">
              <form onSubmit={handleSignUp} autoComplete="off" className="space-y-4">
                <FormField label="Имя" id="signup-first-name">
                  <Input
                    id="signup-first-name"
                    name="first-name"
                    type="text"
                    autoCorrect="off"
                    spellCheck={false}
                    placeholder="Иван"
                    value={signupFirstName}
                    onChange={(e) => setSignupFirstName(e.target.value)}
                    disabled={loading}
                    required
                    minLength={2}
                    autoComplete="given-name"
                  />
                </FormField>
                <FormField label="Фамилия" id="signup-last-name">
                  <Input
                    id="signup-last-name"
                    name="last-name"
                    type="text"
                    autoCorrect="off"
                    spellCheck={false}
                    placeholder="Иванов"
                    value={signupLastName}
                    onChange={(e) => setSignupLastName(e.target.value)}
                    disabled={loading}
                    required
                    minLength={2}
                    autoComplete="family-name"
                  />
                </FormField>
                <FormField label="Телефон" id="signup-phone">
                  <Input
                    id="signup-phone"
                    name="phone"
                    type="tel"
                    inputMode="tel"
                    autoCorrect="off"
                    spellCheck={false}
                    placeholder="+7 999 123-45-67"
                    value={signupPhone}
                    onChange={(e) => setSignupPhone(e.target.value)}
                    onBlur={handlePhoneBlur}
                    disabled={loading}
                    required
                    autoComplete="tel"
                  />
                </FormField>
                <FormField label="Email" id="signup-email">
                  <Input
                    id="signup-email"
                    name="email"
                    type="email"
                    inputMode="email"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    placeholder="name@example.com"
                    value={signupEmail}
                    onChange={(e) => setSignupEmail(e.target.value)}
                    disabled={loading}
                    required
                    autoComplete="email"
                  />
                </FormField>
                <FormField label="Пароль" id="signup-password">
                  <Input
                    id="signup-password"
                    name="new-password"
                    type="password"
                    placeholder="Минимум 6 символов"
                    value={signupPassword}
                    onChange={(e) => setSignupPassword(e.target.value)}
                    disabled={loading}
                    required
                    minLength={6}
                    autoComplete="new-password"
                  />
                </FormField>
                <FormField label="Подтвердите пароль" id="signup-confirm-password">
                  <Input
                    id="signup-confirm-password"
                    name="confirm-password"
                    type="password"
                    placeholder="••••••••"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    disabled={loading}
                    required
                    minLength={6}
                    autoComplete="new-password"
                  />
                </FormField>
                <Button
                  type="submit"
                  variant="brand"
                  size="cta"
                  className="w-full"
                  disabled={loading || !isSignupFormValid}
                >
                  {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Зарегистрироваться
                </Button>
              </form>
            </TabsContent>

            <TabsContent value="authorization" className="mt-6">
              <LoginForm
                email={loginEmail}
                password={loginPassword}
                setEmail={setLoginEmail}
                setPassword={setLoginPassword}
                loading={loading}
                isValid={isLoginFormValid}
                onSubmit={handleSubmit}
              />
            </TabsContent>
          </Tabs>
        </>
      ) : (
        <>
          <h1>Войти в Джейхелпер</h1>
          <p className="lede">Введите email и пароль</p>

          <LoginForm
            email={loginEmail}
            password={loginPassword}
            setEmail={setLoginEmail}
            setPassword={setLoginPassword}
            loading={loading}
            isValid={isLoginFormValid}
            onSubmit={handleSubmit}
          />

          <div
            className="mt-6 rounded-lg p-5"
            style={{
              background: "var(--bg-soft)",
              border: "1px solid var(--border-soft)",
            }}
          >
            <p
              className="text-sm text-center mb-3"
              style={{ color: "var(--text-secondary)" }}
            >
              Регистрация доступна только после звонка
            </p>
            <Button
              type="button"
              variant="brandOutline"
              size="ctaSm"
              onClick={handleCalendlyClick}
              className="w-full"
            >
              Записаться на звонок
            </Button>
          </div>
        </>
      )}
    </AuthShell>
  );
}

function FormField({
  label,
  id,
  children,
}: {
  label: string;
  id: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id} style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}>
        {label}
      </Label>
      {children}
    </div>
  );
}

function LoginForm({
  email,
  password,
  setEmail,
  setPassword,
  loading,
  isValid,
  onSubmit,
}: {
  email: string;
  password: string;
  setEmail: (v: string) => void;
  setPassword: (v: string) => void;
  loading: boolean;
  isValid: boolean;
  onSubmit: (e: React.FormEvent) => void;
}) {
  return (
    <form onSubmit={onSubmit} autoComplete="off" className="space-y-4">
      <FormField label="Email" id="login-email">
        <Input
          id="login-email"
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
      </FormField>
      <FormField label="Пароль" id="login-password">
        <Input
          id="login-password"
          name="password"
          type="password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={loading}
          required
          minLength={6}
          autoComplete="current-password"
        />
      </FormField>
      <div style={{ marginTop: 8, textAlign: "right" }}>
        <Link
          href="/auth/forgot-password"
          style={{ fontSize: 13.5, color: "var(--text-secondary)" }}
        >
          Забыли пароль?
        </Link>
      </div>
      <Button
        type="submit"
        variant="brand"
        size="cta"
        className="w-full"
        disabled={loading || !isValid}
      >
        {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        Войти
      </Button>
    </form>
  );
}
