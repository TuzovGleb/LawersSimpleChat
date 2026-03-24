"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { ToasterClient } from "@/components/toaster-client";
import { Loader2 } from "lucide-react";

// Ретро стиль клавиатур (винтажный)
const retroStyles = `
  @keyframes blink {
    0%, 50% { border-color: #000; }
    51%, 100% { border-color: transparent; }
  }
  
  .retro-bg {
    background: #f5f5f0;
    background-image: 
      repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0, 0, 0, 0.02) 2px,
        rgba(0, 0, 0, 0.02) 4px
      );
    position: relative;
  }
  
  .retro-bg::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: 
      radial-gradient(circle at 20% 50%, rgba(0, 0, 0, 0.01) 0%, transparent 50%),
      radial-gradient(circle at 80% 80%, rgba(0, 0, 0, 0.01) 0%, transparent 50%);
    pointer-events: none;
  }
  
  .retro-card {
    background: #fafaf5;
    border: 3px solid #2a2a2a;
    box-shadow: 
      0 4px 8px rgba(0, 0, 0, 0.15),
      0 8px 16px rgba(0, 0, 0, 0.1),
      inset 0 1px 0 rgba(255, 255, 255, 0.5);
    position: relative;
    font-family: 'Courier New', 'Monaco', monospace;
  }
  
  .retro-card::after {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4px;
    background: linear-gradient(90deg, 
      #2a2a2a 0%, 
      #4a4a4a 25%, 
      #2a2a2a 50%, 
      #4a4a4a 75%, 
      #2a2a2a 100%
    );
  }
  
  .retro-title {
    color: #000;
    font-family: 'Courier New', 'Monaco', monospace;
    font-weight: bold;
    letter-spacing: 1px;
    text-transform: uppercase;
    position: relative;
    display: inline-block;
  }
  
  .retro-title::after {
    content: '_';
    animation: blink 1s infinite;
    margin-left: 2px;
  }
  
  .retro-label {
    color: #000;
    font-family: 'Courier New', 'Monaco', monospace;
    font-weight: bold;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    font-size: 0.85rem;
  }
  
  .retro-input {
    background: #ffffff;
    border: 2px solid #4a4a4a;
    border-top-color: #2a2a2a;
    border-left-color: #2a2a2a;
    border-right-color: #6a6a6a;
    border-bottom-color: #6a6a6a;
    color: #000;
    font-family: 'Courier New', 'Monaco', monospace;
    box-shadow: 
      inset 2px 2px 4px rgba(0, 0, 0, 0.1),
      0 1px 0 rgba(255, 255, 255, 0.8);
    transition: all 0.2s;
  }
  
  .retro-input:focus {
    outline: none;
    border-color: #2a2a2a;
    background: #fffef5;
    box-shadow: 
      inset 2px 2px 4px rgba(0, 0, 0, 0.15),
      0 0 0 2px rgba(0, 0, 0, 0.1);
  }
  
  .retro-input::placeholder {
    color: #888;
    font-style: italic;
  }
  
  .retro-button {
    background: #982525 !important;
    border: 3px solid #2a2a2a !important;
    border-top-color: #4a4a4a !important;
    border-left-color: #4a4a4a !important;
    border-right-color: #1a1a1a !important;
    border-bottom-color: #1a1a1a !important;
    color: #fff !important;
    font-family: 'Courier New', 'Monaco', monospace !important;
    font-weight: bold !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
    box-shadow: 
      0 3px 6px rgba(0, 0, 0, 0.2),
      inset 0 1px 0 rgba(255, 255, 255, 0.2),
      inset 0 -1px 0 rgba(0, 0, 0, 0.3) !important;
    transition: all 0.15s !important;
    position: relative !important;
    text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5) !important;
    padding: 0.75rem 1.5rem !important;
    font-size: 0.875rem !important;
    width: 100% !important;
    height: auto !important;
    min-height: 44px !important;
    line-height: 1.5 !important;
  }
  
  .retro-button:hover:not(:disabled) {
    background: #b03030;
    transform: translateY(-1px);
    box-shadow: 
      0 4px 8px rgba(0, 0, 0, 0.25),
      inset 0 1px 0 rgba(255, 255, 255, 0.3),
      inset 0 -1px 0 rgba(0, 0, 0, 0.4);
  }
  
  .retro-button:active:not(:disabled) {
    transform: translateY(0);
    box-shadow: 
      inset 0 2px 4px rgba(0, 0, 0, 0.3),
      inset 0 -1px 0 rgba(255, 255, 255, 0.1);
  }
  
  .retro-button:disabled {
    background: #6a6a6a;
    color: #ccc;
    cursor: not-allowed;
    opacity: 0.7;
  }
  
  .retro-info-box {
    background: #f0f0eb;
    border: 2px solid #4a4a4a;
    border-top-color: #6a6a6a;
    border-left-color: #6a6a6a;
    border-right-color: #2a2a2a;
    border-bottom-color: #2a2a2a;
    box-shadow: 
      inset 2px 2px 4px rgba(0, 0, 0, 0.1),
      0 2px 4px rgba(0, 0, 0, 0.1);
    font-family: 'Courier New', 'Monaco', monospace;
  }
  
  .retro-info-text {
    color: #000;
    font-weight: 500;
  }
  
  .retro-calendly-button {
    background: #982525 !important;
    border: 3px solid #2a2a2a !important;
    border-top-color: #4a4a4a !important;
    border-left-color: #4a4a4a !important;
    border-right-color: #1a1a1a !important;
    border-bottom-color: #1a1a1a !important;
    color: #fff !important;
    font-family: 'Courier New', 'Monaco', monospace !important;
    font-weight: bold !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
    box-shadow: 
      0 3px 6px rgba(0, 0, 0, 0.2),
      inset 0 1px 0 rgba(255, 255, 255, 0.2),
      inset 0 -1px 0 rgba(0, 0, 0, 0.3) !important;
    transition: all 0.15s !important;
    position: relative !important;
    text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5) !important;
    padding: 0.75rem 1.5rem !important;
    font-size: 0.875rem !important;
    width: 100% !important;
    height: auto !important;
    min-height: 44px !important;
    line-height: 1.5 !important;
  }
  
  .retro-calendly-button:hover:not(:disabled) {
    background: #b03030;
    transform: translateY(-1px);
    box-shadow: 
      0 4px 8px rgba(0, 0, 0, 0.25),
      inset 0 1px 0 rgba(255, 255, 255, 0.3),
      inset 0 -1px 0 rgba(0, 0, 0, 0.4);
  }
  
  .retro-calendly-button:active:not(:disabled) {
    transform: translateY(0);
    box-shadow: 
      inset 0 2px 4px rgba(0, 0, 0, 0.3),
      inset 0 -1px 0 rgba(255, 255, 255, 0.1);
  }
  
  .retro-loading {
    color: #000;
    font-family: 'Courier New', 'Monaco', monospace;
    font-weight: bold;
    letter-spacing: 2px;
  }
  
  .retro-spinner {
    border-color: #4a4a4a;
    border-top-color: #000;
  }
  
  .retro-description {
    color: #333;
    font-family: 'Courier New', 'Monaco', monospace;
    font-size: 0.9rem;
  }
  
  .retro-tabs-list {
    background: #fafaf5 !important;
    border: 2px solid #2a2a2a !important;
    padding: 0 !important;
    gap: 0 !important;
    font-family: 'Courier New', 'Monaco', monospace !important;
    display: flex !important;
    align-items: stretch !important;
    height: auto !important;
    min-height: 44px !important;
    border-radius: 0 !important;
    box-shadow: none !important;
  }
  
  .retro-tabs-trigger {
    background: transparent !important;
    border: none !important;
    color: #666 !important;
    font-family: 'Courier New', 'Monaco', monospace !important;
    font-weight: bold !important;
    text-transform: uppercase !important;
    letter-spacing: 0.5px !important;
    padding: 0.875rem 1rem !important;
    transition: color 0.15s !important;
    position: relative !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    flex: 1 !important;
    box-shadow: none !important;
    border-radius: 0 !important;
    height: auto !important;
    min-height: 44px !important;
    margin: 0 !important;
  }
  
  .retro-tabs-trigger:hover {
    color: #333 !important;
    background: transparent !important;
  }
  
  .retro-tabs-trigger[data-state=active] {
    background: transparent !important;
    border: none !important;
    color: #982525 !important;
    box-shadow: none !important;
  }
  
  .retro-tabs-trigger:focus-visible {
    outline: none !important;
    ring: none !important;
  }
  
  .retro-tabs-content {
    margin-top: 1.5rem;
  }
`;

export function AuthForm() {
  // Separate state for login and signup forms
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const { signIn, signUp, user, loading: authLoading } = useAuth();
  const router = useRouter();
  const { toast } = useToast();

  // Check if signup is enabled via environment variable
  const isSignupEnabled = process.env.NEXT_PUBLIC_ENABLE_SIGNUP === "true";
  
  // Default tab: registration when enabled, authorization when disabled
  const defaultTab = isSignupEnabled ? "registration" : "authorization";

  // Redirect if already authenticated
  useEffect(() => {
    if (!authLoading && user) {
      router.replace('/workspace');
    }
  }, [user, authLoading, router]);

  // Ссылка на Calendly для записи на консультацию
  const CALENDLY_URL = "https://calendly.com/glebtuzov/30-minute-call-with-tuzov-gleb-opencv?month=2025-12";

  // Check if login form is valid for submission
  const isLoginFormValid = loginEmail.trim() !== "" && loginPassword.trim() !== "" && loginPassword.length >= 6;

  // Check if signup form is valid for submission
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
        description: error.message || "Проверьте введенные данные и попробуйте снова",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCalendlyClick = () => {
    window.open(CALENDLY_URL, '_blank');
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
        // If session is null, email confirmation is required
        if (!data.session) {
          toast({
            title: "Проверьте почту",
            description: "На вашу почту отправлено письмо для подтверждения аккаунта.",
          });
          return;
        }

        // Email confirmation disabled — sign in immediately
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
      toast({
        variant: "destructive",
        title: "Ошибка регистрации",
        description: error.message || "Проверьте введенные данные и попробуйте снова",
      });
    } finally {
      setLoading(false);
    }
  };

  // Show loading while checking auth
  if (authLoading || user) {
    return (
      <>
        <style>{retroStyles}</style>
        <div className="retro-bg flex min-h-screen items-center justify-center">
          <div className="text-center">
            <div className="retro-spinner inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-r-transparent" />
            <p className="retro-loading mt-4">ЗАГРУЗКА...</p>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <style>{retroStyles}</style>
      <div className="retro-bg flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-md space-y-4">
          <Card className="retro-card">
            <CardHeader className="space-y-1 pt-6">
              <CardTitle className="retro-title text-2xl font-bold">
                {isSignupEnabled ? "АВТОРИЗАЦИЯ И РЕГИСТРАЦИЯ" : "ВХОД В СИСТЕМУ"}
              </CardTitle>
              <CardDescription className="retro-description text-sm">
                {isSignupEnabled ? "Выберите действие" : "Введите email и пароль для входа"}
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-4">
              {isSignupEnabled ? (
                <Tabs defaultValue={defaultTab} className="w-full">
                  <TabsList className="retro-tabs-list w-full">
                    <TabsTrigger value="authorization" className="retro-tabs-trigger">
                      АВТОРИЗАЦИЯ
                    </TabsTrigger>
                    <TabsTrigger value="registration" className="retro-tabs-trigger">
                      РЕГИСТРАЦИЯ
                    </TabsTrigger>
                  </TabsList>
                  
                  <TabsContent value="registration" className="retro-tabs-content">
                    <form onSubmit={handleSignUp} autoComplete="off">
                      <div className="space-y-4">
                        <div className="space-y-2">
                          <Label htmlFor="signup-email" className="retro-label">
                            EMAIL:
                          </Label>
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
                            className="retro-input"
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="signup-password" className="retro-label">
                            ПАРОЛЬ:
                          </Label>
                          <Input
                            id="signup-password"
                            name="new-password"
                            type="password"
                            placeholder="••••••••"
                            value={signupPassword}
                            onChange={(e) => setSignupPassword(e.target.value)}
                            disabled={loading}
                            required
                            minLength={6}
                            autoComplete="new-password"
                            className="retro-input"
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="signup-confirm-password" className="retro-label">
                            ПОДТВЕРДИТЕ ПАРОЛЬ:
                          </Label>
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
                            className="retro-input"
                          />
                        </div>
                        <Button
                          type="submit"
                          className="retro-button w-full"
                          disabled={loading || !isSignupFormValid}
                        >
                          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                          ЗАРЕГИСТРИРОВАТЬСЯ
                        </Button>
                      </div>
                    </form>
                  </TabsContent>
                  
                  <TabsContent value="authorization" className="retro-tabs-content">
                    <form onSubmit={handleSubmit} autoComplete="off">
                      <div className="space-y-4">
                        <div className="space-y-2">
                          <Label htmlFor="login-email" className="retro-label">
                            EMAIL:
                          </Label>
                          <Input
                            id="login-email"
                            name="email"
                            type="email"
                            placeholder="name@example.com"
                            value={loginEmail}
                            onChange={(e) => setLoginEmail(e.target.value)}
                            disabled={loading}
                            required
                            autoComplete="email"
                            className="retro-input"
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="login-password" className="retro-label">
                            ПАРОЛЬ:
                          </Label>
                          <Input
                            id="login-password"
                            name="password"
                            type="password"
                            placeholder="••••••••"
                            value={loginPassword}
                            onChange={(e) => setLoginPassword(e.target.value)}
                            disabled={loading}
                            required
                            minLength={6}
                            autoComplete="current-password"
                            className="retro-input"
                          />
                        </div>
                        <Button
                          type="submit"
                          className="retro-button w-full"
                          disabled={loading || !isLoginFormValid}
                        >
                          {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                          ВОЙТИ
                        </Button>
                      </div>
                    </form>
                  </TabsContent>
                </Tabs>
              ) : (
                <form onSubmit={handleSubmit} autoComplete="off">
                  <div className="space-y-4">
                <div className="space-y-2">
                      <Label htmlFor="login-email" className="retro-label">
                    EMAIL:
                  </Label>
                  <Input
                        id="login-email"
                        name="email"
                    type="email"
                    placeholder="name@example.com"
                        value={loginEmail}
                        onChange={(e) => setLoginEmail(e.target.value)}
                    disabled={loading}
                    required
                        autoComplete="email"
                    className="retro-input"
                  />
                </div>
                <div className="space-y-2">
                      <Label htmlFor="login-password" className="retro-label">
                    ПАРОЛЬ:
                  </Label>
                  <Input
                        id="login-password"
                        name="password"
                    type="password"
                    placeholder="••••••••"
                        value={loginPassword}
                        onChange={(e) => setLoginPassword(e.target.value)}
                    disabled={loading}
                    required
                    minLength={6}
                        autoComplete="current-password"
                    className="retro-input"
                  />
                </div>
                <Button
                  type="submit"
                  className="retro-button w-full"
                  disabled={loading || !isLoginFormValid}
                >
                  {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  ВОЙТИ
                </Button>
                  </div>
            </form>
              )}
            </CardContent>
          </Card>
          
          {/* Calendly section - shown only when signup is disabled */}
          {!isSignupEnabled && (
            <div className="retro-info-box p-6 w-full">
              <div className="space-y-3">
                <p className="retro-info-text text-sm text-center">
                  Регистрация доступна только после звонка
                </p>
                <button
                  type="button"
                  onClick={handleCalendlyClick}
                  className="retro-calendly-button w-full"
                  style={{
                    borderRadius: '0.5rem',
                    cursor: 'pointer',
                  }}
                >
                  ЗАПИСАТЬСЯ НА ЗВОНОК
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
      <ToasterClient />
    </>
  );
}

