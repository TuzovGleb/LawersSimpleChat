"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { AuthShell } from "@/components/auth-shell";
import { createClient } from "@/lib/supabase/client";
import { normalizeRuPhone, validateRuPhone } from "@/lib/phone";
import { Loader2 } from "lucide-react";

const NAME_ERROR = "Минимум 2 символа";
const PHONE_ERROR = "Укажите настоящий номер телефона в формате +7...";
const GENERIC_ERROR = "Не удалось сохранить, попробуйте ещё раз";

export default function OnboardingProfilePage() {
  const router = useRouter();
  const { toast } = useToast();

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);

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

  const firstNameValid = firstName.trim().length >= 2;
  const lastNameValid = lastName.trim().length >= 2;
  const normalizedPhone = normalizeRuPhone(phone);
  const phoneValid = normalizedPhone !== null && validateRuPhone(normalizedPhone);
  const isValid = firstNameValid && lastNameValid && phoneValid;

  // На blur приводим телефон к канону +7XXXXXXXXXX, чтобы юрист видел,
  // что именно сохранится.
  const handlePhoneBlur = () => {
    const normalized = normalizeRuPhone(phone);
    if (normalized) setPhone(normalized);
  };

  const showError = (description: string) => {
    toast({ variant: "destructive", title: "Ошибка", description });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;

    const first = firstName.trim();
    const last = lastName.trim();
    if (first.length < 2 || last.length < 2) {
      showError("Имя и фамилия должны содержать минимум 2 символа");
      return;
    }
    const normalized = normalizeRuPhone(phone);
    if (!normalized || !validateRuPhone(normalized)) {
      showError(PHONE_ERROR);
      return;
    }
    if (!supabase) {
      showError(GENERIC_ERROR);
      return;
    }

    setSubmitting(true);
    try {
      // 1) Профиль в БД — источник истины (SECURITY DEFINER RPC).
      const { data, error } = await supabase.rpc("complete_my_profile", {
        p_first_name: first,
        p_last_name: last,
        p_phone: normalized,
      });

      if (error) {
        console.error("complete_my_profile error:", error);
        showError(GENERIC_ERROR);
        return;
      }

      const result = data as { ok?: boolean; error?: string } | null;
      if (!result?.ok) {
        if (result?.error === "invalid_phone") {
          showError(PHONE_ERROR);
        } else if (result?.error === "invalid_name") {
          showError("Имя и фамилия должны содержать минимум 2 символа");
        } else {
          showError(GENERIC_ERROR);
        }
        return;
      }

      // 2) Метаданные в auth — их читает middleware-гейт. При ошибке НЕ
      // редиректим: без profile_completed=true middleware вернёт сюда же (петля).
      const { error: updateError } = await supabase.auth.updateUser({
        data: {
          first_name: first,
          last_name: last,
          phone: normalized,
          profile_completed: true,
        },
      });

      if (updateError) {
        console.error("auth.updateUser error:", updateError);
        showError(GENERIC_ERROR);
        return;
      }

      // 3) Готово — в приложение.
      router.replace("/workspace");
      router.refresh();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthShell>
      <h1>Представьтесь, пожалуйста</h1>
      <p className="lede">
        Эти данные нужны, чтобы мы могли связаться с вами
      </p>

      <form onSubmit={handleSubmit} autoComplete="off" className="space-y-4">
        <div className="space-y-2">
          <Label
            htmlFor="first-name"
            style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}
          >
            Имя
          </Label>
          <Input
            id="first-name"
            name="first-name"
            type="text"
            placeholder="Иван"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            disabled={submitting}
            required
            autoComplete="given-name"
          />
          {firstName.length > 0 && !firstNameValid && (
            <p style={{ margin: 0, fontSize: 13, color: "var(--brand-accent)" }}>
              {NAME_ERROR}
            </p>
          )}
        </div>

        <div className="space-y-2">
          <Label
            htmlFor="last-name"
            style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}
          >
            Фамилия
          </Label>
          <Input
            id="last-name"
            name="last-name"
            type="text"
            placeholder="Иванов"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            disabled={submitting}
            required
            autoComplete="family-name"
          />
          {lastName.length > 0 && !lastNameValid && (
            <p style={{ margin: 0, fontSize: 13, color: "var(--brand-accent)" }}>
              {NAME_ERROR}
            </p>
          )}
        </div>

        <div className="space-y-2">
          <Label
            htmlFor="phone"
            style={{ fontSize: 13.5, fontWeight: 600, color: "#2A313D" }}
          >
            Телефон
          </Label>
          <Input
            id="phone"
            name="phone"
            type="tel"
            inputMode="tel"
            placeholder="+7 999 123-45-67"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            onBlur={handlePhoneBlur}
            disabled={submitting}
            required
            autoComplete="tel"
          />
          {phone.length > 0 && !phoneValid && (
            <p style={{ margin: 0, fontSize: 13, color: "var(--brand-accent)" }}>
              {PHONE_ERROR}
            </p>
          )}
        </div>

        <Button
          type="submit"
          variant="brand"
          size="cta"
          className="w-full"
          disabled={submitting || !isValid}
        >
          {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {submitting ? "Сохраняем…" : "Продолжить"}
        </Button>
      </form>
    </AuthShell>
  );
}
