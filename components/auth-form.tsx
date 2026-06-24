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
import { ToasterClient } from "@/components/toaster-client";
import { Loader2, ShieldCheck, Lock, Server } from "lucide-react";

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
    <div className="auth-shell">
      <aside className="auth-aside">
        <Link href="/" className="logo" style={{ color: "#fff" }}>
          Джейхелпер<span className="dot" style={{ color: "var(--brand-accent-bg)" }}>.</span>
        </Link>
        <div className="aside-body">
          <h2
            style={{
              color: "#fff",
              fontFamily: "var(--font-serif-family)",
              fontWeight: 500,
              fontSize: 30,
              lineHeight: 1.2,
              margin: "0 0 24px",
              maxWidth: 420,
              letterSpacing: "-.012em",
            }}
          >
            AI-помощник для российских юристов
          </h2>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 14 }}>
            <li style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 12, fontSize: 15.5, color: "rgba(250,250,247,.85)", lineHeight: 1.45 }}>
              <Server size={20} style={{ color: "var(--secondary-accent)", marginTop: 1 }} />
              <span>Данные хранятся в России. Соответствие 152-ФЗ.</span>
            </li>
            <li style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 12, fontSize: 15.5, color: "rgba(250,250,247,.85)", lineHeight: 1.45 }}>
              <ShieldCheck size={20} style={{ color: "var(--secondary-accent)", marginTop: 1 }} />
              <span>Содержимое дел не используется для обучения моделей.</span>
            </li>
            <li style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 12, fontSize: 15.5, color: "rgba(250,250,247,.85)", lineHeight: 1.45 }}>
              <Lock size={20} style={{ color: "var(--secondary-accent)", marginTop: 1 }} />
              <span>Конфиденциальность — технически и юридически.</span>
            </li>
          </ul>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-form-wrap">
          {isSignupEnabled ? (
            <>
              <h1>{defaultTab === "registration" ? "Создайте аккаунт" : "Войти в Джейхелпер"}</h1>
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
        </div>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 18,
            fontSize: 13,
            color: "var(--text-muted)",
            marginTop: 32,
            paddingTop: 24,
            borderTop: "1px solid var(--border-strong)",
          }}
        >
          <Link href="/" style={{ color: "var(--text-muted)" }}>На главную</Link>
        </div>
      </main>

      <ToasterClient />

      <style jsx>{`
        .auth-shell {
          min-height: 100vh;
          display: grid;
          grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
          background: var(--bg);
        }
        .auth-aside {
          background: var(--bg-dark);
          color: var(--text-on-dark);
          padding: 48px 56px;
          display: flex;
          flex-direction: column;
          position: relative;
          overflow: hidden;
        }
        .auth-aside::after {
          content: "";
          position: absolute;
          inset: 0;
          background:
            radial-gradient(800px 360px at 80% 10%, rgba(168, 118, 62, 0.1), transparent 60%),
            radial-gradient(700px 300px at 10% 90%, rgba(122, 46, 46, 0.18), transparent 65%);
          pointer-events: none;
        }
        .auth-aside > * {
          position: relative;
          z-index: 1;
        }
        .auth-aside .logo {
          font-family: var(--font-serif-family);
          font-weight: 600;
          font-size: 24px;
          color: #fff;
          letter-spacing: -0.01em;
          display: inline-flex;
          align-items: baseline;
          margin-bottom: auto;
        }
        .aside-body {
          margin-top: auto;
        }
        .auth-main {
          display: flex;
          flex-direction: column;
          padding: 28px 56px 40px;
          background: var(--bg);
          min-height: 100vh;
        }
        .auth-form-wrap {
          width: 100%;
          max-width: 420px;
          margin: auto;
          padding: 32px 0;
        }
        .auth-form-wrap h1 {
          font-family: var(--font-serif-family);
          font-size: 32px;
          font-weight: 500;
          letter-spacing: -0.012em;
          margin: 0 0 8px;
          color: var(--text-primary);
        }
        .auth-form-wrap .lede {
          color: var(--text-secondary);
          font-size: 15.5px;
          margin: 0 0 28px;
          line-height: 1.5;
        }
        @media (max-width: 900px) {
          .auth-shell {
            grid-template-columns: minmax(0, 1fr);
          }
          .auth-aside {
            padding: 28px 22px 26px;
            min-height: auto;
          }
          .auth-aside .logo {
            margin-bottom: 20px;
            font-size: 22px;
          }
          .auth-main {
            padding: 20px 18px 28px;
            min-height: auto;
          }
          .auth-form-wrap {
            padding: 8px 0;
            max-width: none;
          }
          .auth-form-wrap h1 {
            font-size: 26px;
          }
        }
      `}</style>
    </div>
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
