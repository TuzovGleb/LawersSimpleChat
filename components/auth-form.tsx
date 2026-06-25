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
import { Loader2 } from "lucide-react";

const CALENDLY_URL =
  "https://calendly.com/glebtuzov/30-minute-call-with-tuzov-gleb-opencv?month=2025-12";

export function AuthForm() {
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const { signIn, signUp, user, loading: authLoading } = useAuth();
  const router = useRouter();
  const { toast } = useToast();

  const isSignupEnabled = process.env.NEXT_PUBLIC_ENABLE_SIGNUP === "true";
  const defaultTab = "authorization";

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

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!signupEmail || !signupPassword || !confirmPassword) {
      toast({
        variant: "destructive",
        title: "Ошибка",
        description: "Пожалуйста, заполните все поля",
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
      const { data, error } = await signUp(signupEmail, signupPassword);

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
        className="flex min-h-screen items-center justify-center"
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
      {isSignupEnabled ? (
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
                  className="btn btn-primary w-full"
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
            <button
              type="button"
              onClick={handleCalendlyClick}
              className="btn btn-outline-accent w-full btn-sm"
            >
              Записаться на звонок
            </button>
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
        className="btn btn-primary w-full"
        disabled={loading || !isValid}
      >
        {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        Войти
      </Button>
    </form>
  );
}
