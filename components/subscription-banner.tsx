"use client";

import { useState } from "react";
import { format } from "date-fns";
import { Button } from "@/components/ui/button";
import { InfoBox } from "@/components/ui/info-box";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { cn, resolveApiUrl } from "@/lib/utils";
import { CALENDLY_URL } from "@/lib/contact";
import { AlertCircle, Clock, Loader2 } from "lucide-react";
import type { Entitlement } from "@/lib/entitlement";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function daysLeft(expiresAt: string | null): number | null {
  if (!expiresAt) return null;
  const timestamp = new Date(expiresAt).getTime();
  if (Number.isNaN(timestamp)) return null;
  return Math.ceil((timestamp - Date.now()) / MS_PER_DAY);
}

function formatExpiresAt(expiresAt: string | null): string | null {
  if (!expiresAt) return null;
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) return null;
  return format(date, "dd.MM.yyyy");
}

// Минимальная клиентская проверка формы Entitlement из ответа BFF: сервер уже
// нормализует JSONB через mapEntitlementJson, здесь только защищаемся от
// неожиданных тел (lib/entitlement.ts импортируем строго type-only — он тянет
// next/server и не должен попадать в клиентский бандл). Экспортируется для
// chat-page-client (разбор entitlement из GET /api/projects и тел 402).
export function parseEntitlement(raw: unknown): Entitlement | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as { status?: unknown; kind?: unknown; expiresAt?: unknown };
  if (value.status !== "active" && value.status !== "expired" && value.status !== "none") {
    return null;
  }
  const kind =
    value.kind === "trial" ||
    value.kind === "promo" ||
    value.kind === "manual" ||
    value.kind === "payment" ||
    value.kind === "admin"
      ? value.kind
      : null;
  return {
    status: value.status,
    kind,
    expiresAt: typeof value.expiresAt === "string" ? value.expiresAt : null,
  };
}

interface SubscriptionBannerProps {
  entitlement: Entitlement | null;
  onRedeemed: (entitlement: Entitlement) => void;
  className?: string;
}

export function SubscriptionBanner({ entitlement, onRedeemed, className }: SubscriptionBannerProps) {
  const { toast } = useToast();
  const [isPromoOpen, setIsPromoOpen] = useState(false);
  const [promoCode, setPromoCode] = useState("");
  const [isRedeeming, setIsRedeeming] = useState(false);

  const handleContactClick = () => {
    window.open(CALENDLY_URL, "_blank");
  };

  const handlePromoOpenChange = (open: boolean) => {
    if (isRedeeming) return;
    setIsPromoOpen(open);
    if (!open) {
      setPromoCode("");
    }
  };

  const handleRedeem = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const code = promoCode.trim();
    if (!code || isRedeeming) return;
    setIsRedeeming(true);
    try {
      // resolveApiUrl: как и остальные клиентские запросы, редим должен идти
      // через NEXT_PUBLIC_PROXY_URL, если он настроен (RU-доступ через прокси).
      const response = await fetch(resolveApiUrl("/api/promo/redeem"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const payload = await response.json().catch(() => ({}) as Record<string, unknown>);
      const nextEntitlement = parseEntitlement(
        (payload as { entitlement?: unknown })?.entitlement,
      );

      if (response.ok && (payload as { ok?: unknown })?.ok === true) {
        const expiresText = formatExpiresAt(nextEntitlement?.expiresAt ?? null);
        setIsPromoOpen(false);
        setPromoCode("");
        toast({
          title: "Промокод применён",
          description: expiresText
            ? `Доступ открыт до ${expiresText}.`
            : "Доступ открыт.",
        });
        if (nextEntitlement) {
          onRedeemed(nextEntitlement);
        } else {
          // Редим прошёл, но entitlement из ответа не распарсился (BFF легально
          // отдаёт {ok:true, entitlement:null} при дрейфе контракта) — иначе UI
          // остался бы в read-only до перезагрузки. Fallback: перечитываем
          // статус из bootstrap-эндпоинта (refreshEntitlement из спеки).
          try {
            const refreshResponse = await fetch(resolveApiUrl("/api/projects"));
            const refreshPayload: unknown = await refreshResponse.json().catch(() => null);
            const refreshed = parseEntitlement(
              (refreshPayload as { entitlement?: unknown } | null)?.entitlement,
            );
            if (refreshed) {
              onRedeemed(refreshed);
            }
          } catch (refreshError) {
            console.error("Не удалось обновить статус доступа после промокода:", refreshError);
          }
        }
        return;
      }

      const errorText =
        typeof (payload as { error?: unknown })?.error === "string" &&
        ((payload as { error: string }).error).trim()
          ? (payload as { error: string }).error
          : "Промокод недействителен или истёк";
      toast({
        variant: "destructive",
        title: "Не удалось применить промокод",
        description: errorText,
      });
    } catch (error) {
      console.error("Не удалось применить промокод:", error);
      toast({
        variant: "destructive",
        title: "Не удалось применить промокод",
        description: "Проверьте соединение и попробуйте снова.",
      });
    } finally {
      setIsRedeeming(false);
    }
  };

  if (!entitlement) {
    return null;
  }

  // kind='admin' — постоянный доступ (expiresAt всегда null): админ не должен
  // видеть ни одну полосу. Активные ветки ниже и так перечисляют kind'ы явно,
  // но явный guard защищает от будущих правок веток.
  if (entitlement.status === "active" && entitlement.kind === "admin") {
    return null;
  }

  const remainingDays = daysLeft(entitlement.expiresAt);
  const expiresText = formatExpiresAt(entitlement.expiresAt);

  let tone: "trial" | "soft" | "blocked" | null = null;
  let message = "";
  let showPromoButton = false;

  if (entitlement.status === "active") {
    if (
      (entitlement.kind === "trial" || entitlement.kind === "promo") &&
      remainingDays !== null &&
      remainingDays <= 3
    ) {
      tone = "trial";
      message = expiresText
        ? `Пробный доступ до ${expiresText}. Чтобы продолжить после — свяжитесь с нами`
        : "Пробный доступ скоро закончится. Чтобы продолжить — свяжитесь с нами";
      showPromoButton = true;
    } else if (
      (entitlement.kind === "manual" || entitlement.kind === "payment") &&
      remainingDays !== null &&
      remainingDays <= 7
    ) {
      tone = "soft";
      message = expiresText
        ? `Доступ до ${expiresText}. Для продления свяжитесь с нами`
        : "Доступ скоро закончится. Для продления свяжитесь с нами";
    }
  } else {
    tone = "blocked";
    showPromoButton = true;
    // Текст зависит от того, ПОЧЕМУ доступа нет: кончился бесплатный период,
    // кончился оплаченный доступ или доступа ещё не было вовсе.
    if (entitlement.status === "expired" && (entitlement.kind === "trial" || entitlement.kind === "promo")) {
      message = "У вас закончился бесплатный период. Свяжитесь с нами для получения доступа";
    } else if (entitlement.status === "expired") {
      message = "Срок вашего доступа истёк. Свяжитесь с нами, чтобы продлить его";
    } else {
      message = "Чтобы начать работу, свяжитесь с нами — или активируйте промокод";
    }
  }

  if (!tone) {
    return null;
  }

  const toneClasses: Record<NonNullable<typeof tone>, string> = {
    trial: "border-amber-300 bg-amber-50",
    soft: "border-border bg-[var(--bg-soft)]",
    blocked: "border-[hsl(var(--destructive)/0.45)] bg-[hsl(var(--destructive)/0.06)]",
  };

  const icon =
    tone === "blocked" ? (
      <AlertCircle className="h-4 w-4 shrink-0" style={{ color: "hsl(var(--destructive))" }} />
    ) : (
      <Clock
        className="h-4 w-4 shrink-0"
        style={{ color: tone === "trial" ? "#B45309" : "var(--text-secondary)" }}
      />
    );

  return (
    <>
      <InfoBox
        role="status"
        className={cn(
          "flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3",
          toneClasses[tone],
          className,
        )}
        style={{ boxShadow: "none" }}
      >
        <div className="flex min-w-[200px] flex-1 items-center gap-2">
          {icon}
          <p className="m-0 text-sm" style={{ color: "var(--text-primary)" }}>
            {message}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="brandOutline" size="ctaSm" onClick={handleContactClick}>
            Связаться с нами
          </Button>
          {showPromoButton && (
            <Button
              type="button"
              variant="outlineMuted"
              size="ctaSm"
              onClick={() => setIsPromoOpen(true)}
            >
              У меня есть промокод
            </Button>
          )}
        </div>
      </InfoBox>

      <Dialog open={isPromoOpen} onOpenChange={handlePromoOpenChange}>
        <DialogContent className="sm:max-w-[440px]">
          <DialogHeader>
            <DialogTitle>Активация промокода</DialogTitle>
            <DialogDescription>
              Введите промокод, чтобы открыть или продлить доступ.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleRedeem}>
            <div className="grid gap-2 py-2">
              <Label htmlFor="promo-code">Промокод</Label>
              <Input
                id="promo-code"
                placeholder="LAW-XXXX-XXXX"
                value={promoCode}
                onChange={(event) => setPromoCode(event.target.value)}
                autoFocus
                autoComplete="off"
                disabled={isRedeeming}
              />
            </div>
            <DialogFooter className="mt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => handlePromoOpenChange(false)}
                disabled={isRedeeming}
              >
                Отмена
              </Button>
              <Button type="submit" variant="secondary" disabled={!promoCode.trim() || isRedeeming}>
                {isRedeeming && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Применить
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
