"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { ToasterClient } from "@/components/toaster-client";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { Button } from "@/components/ui/button";
// resolveApiUrl: все запросы к /api/* должны уважать NEXT_PUBLIC_PROXY_URL
// (RU-доступ через прокси), как и остальные клиентские fetch'и приложения.
import { cn, resolveApiUrl } from "@/lib/utils";

// ── Пермишены (каталог зашит в seed миграции; здесь — только слаги) ──

const PERM = {
  usersView: "admin.users.view",
  accessManage: "admin.access.manage",
  promosView: "admin.promos.view",
  promosManage: "admin.promos.manage",
  settingsView: "admin.settings.view",
  settingsManage: "admin.settings.manage",
  rolesManage: "admin.roles.manage",
} as const;

// ── Типы ответов /api/admin/* ──
// users/promos — сырые формы admin_list_users / admin_list_promos (snake_case);
// roles/permissions — контракт /api/admin/roles (camelCase, маппинг в роуте).

interface AdminUserRole {
  slug: string;
  name: string;
}

interface AdminUser {
  email: string;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  registered_at: string | null;
  last_sign_in_at: string | null;
  status: "active" | "expired" | "none";
  // 'admin' — вычисляемый kind (не хранится в access_grants): пользователь с
  // хотя бы одним admin.*-пермишеном всегда active с постоянным доступом
  // (access_until/days_left = null), см. 20260709120000_admin_permanent_access.sql.
  kind: "trial" | "promo" | "manual" | "payment" | "admin" | null;
  access_until: string | null;
  days_left: number | null;
  roles: AdminUserRole[];
}

interface AdminSettings {
  signupEnabled: boolean;
  signupTrialDays: number;
}

interface AdminPromo {
  code: string;
  grant_days: number;
  redeemed_count: number;
  max_redemptions: number;
  expires_at: string | null;
  disabled_at: string | null;
  note: string | null;
  created_at: string;
}

interface AdminRole {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  isSystem: boolean;
  permissions: string[];
  usersCount: number;
}

interface AdminPermission {
  slug: string;
  description: string;
}

// ── Хелперы ──

// Генератор кода LAW-XXXX-XXXX. Алфавит без 0/O/1/I (неразличимы на слух и
// в печати). 32 символа → 256 % 32 === 0, равномерное распределение.
const PROMO_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function generatePromoCode(): string {
  const segment = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(4));
    return Array.from(bytes)
      .map((b) => PROMO_ALPHABET[b % PROMO_ALPHABET.length])
      .join("");
  };
  return `LAW-${segment()}-${segment()}`;
}

// Автогенерация slug роли из русского названия (транслит → [a-z0-9_]).
// Ограничения зеркалят SQL CHECK: ^[a-z0-9_]{2,40}$.
const ROLE_SLUG_RE = /^[a-z0-9_]{2,40}$/;

const TRANSLIT_MAP: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh",
  з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o",
  п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c",
  ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu",
  я: "ya",
};

function slugifyRoleName(name: string): string {
  return name
    .toLowerCase()
    .split("")
    .map((ch) => TRANSLIT_MAP[ch] ?? ch)
    .join("")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

// Иммутабельный toggle для Set-стейтов чекбоксов.
function toggleInSet(prev: Set<string>, value: string, checked: boolean): Set<string> {
  const next = new Set(prev);
  if (checked) {
    next.add(value);
  } else {
    next.delete(value);
  }
  return next;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("ru-RU");
}

async function readErrorText(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.error === "string" && data.error) return data.error;
  } catch {
    // тело не JSON — используем fallback
  }
  return fallback;
}

function statusBadge(user: AdminUser): { label: string; className: string } {
  if (user.status === "active") {
    if (user.kind === "admin") {
      // Постоянный доступ администратора: «Доступ до»/«Осталось дней» у такой
      // строки null и рендерятся как «—» (formatDate / typeof-проверка ниже).
      return {
        label: "Админ · постоянный",
        className: "bg-violet-100 text-violet-800 border-violet-200",
      };
    }
    if (user.kind === "trial") {
      return {
        label: "Активен · триал",
        className: "bg-amber-100 text-amber-800 border-amber-200",
      };
    }
    if (user.kind === "promo") {
      return {
        label: "Активен · промокод",
        className: "bg-sky-100 text-sky-800 border-sky-200",
      };
    }
    return {
      label: "Активен · оплачен",
      className: "bg-emerald-100 text-emerald-800 border-emerald-200",
    };
  }
  if (user.status === "expired") {
    return {
      label: "Истёк",
      className: "bg-red-100 text-red-800 border-red-200",
    };
  }
  return {
    label: "Нет",
    className: "bg-muted text-muted-foreground border-border",
  };
}

const thClass =
  "px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground whitespace-nowrap";
const tdClass = "px-3 py-2 align-middle whitespace-nowrap";
const selectClass =
  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-base md:text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const roleBadgeClass =
  "ml-2 rounded-full border border-violet-200 bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-800";

// Список чекбоксов пермишенов (русские description из каталога seed'а).
// В components/ui нет checkbox-примитива — нативный input на токенах
// проекта, без новых зависимостей (как кнопка-тумблер в «Настройках»).
function PermissionCheckboxList({
  catalog,
  selected,
  onToggle,
  disabled,
}: {
  catalog: AdminPermission[];
  selected: Set<string>;
  onToggle: (slug: string, checked: boolean) => void;
  disabled: boolean;
}) {
  if (catalog.length === 0) {
    return <p className="text-sm text-muted-foreground">Загрузка…</p>;
  }
  return (
    <div className="space-y-2">
      {catalog.map((perm) => (
        <label key={perm.slug} className="flex cursor-pointer items-start gap-2">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
            checked={selected.has(perm.slug)}
            onChange={(e) => onToggle(perm.slug, e.target.checked)}
            disabled={disabled}
          />
          <span className="min-w-0">
            <span className="block text-sm leading-5">{perm.description}</span>
            <span className="block truncate font-mono text-xs text-muted-foreground">
              {perm.slug}
            </span>
          </span>
        </label>
      ))}
    </div>
  );
}

// ── Компонент ──

export function AdminDashboard({ permissions }: { permissions: string[] }) {
  const { toast } = useToast();
  // Email текущего админа — чтобы задизейблить «Роли…» на собственной
  // строке (серверный guard 'cannot change own roles' всё равно отобьёт).
  const { user: currentUser, loading: authLoading } = useAuth();
  const currentEmail = (currentUser?.email ?? "").toLowerCase();

  // Права текущего админа: view-пермишен показывает вкладку, manage —
  // кнопки-мутации. Это только UX: серверная проверка — на каждом роуте.
  const canUsersView = permissions.includes(PERM.usersView);
  const canAccessManage = permissions.includes(PERM.accessManage);
  const canPromosView = permissions.includes(PERM.promosView);
  const canPromosManage = permissions.includes(PERM.promosManage);
  const canSettingsView = permissions.includes(PERM.settingsView);
  const canSettingsManage = permissions.includes(PERM.settingsManage);
  const canRolesManage = permissions.includes(PERM.rolesManage);

  const tabs: { value: string; label: string }[] = [];
  if (canUsersView) tabs.push({ value: "users", label: "Пользователи" });
  if (canPromosView) tabs.push({ value: "promos", label: "Промокоды" });
  if (canSettingsView) tabs.push({ value: "settings", label: "Настройки" });
  if (canRolesManage) tabs.push({ value: "roles", label: "Роли" });
  // Активная вкладка по умолчанию — первая доступная.
  const defaultTab = tabs[0]?.value ?? null;

  // Пользователи
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [search, setSearch] = useState("");
  // Фильтр, по которому реально построен текущий список (выставляется только
  // при submit поиска): рефетчи после мутаций не должны подхватывать
  // недопечатанный текст из инпута.
  const [appliedSearch, setAppliedSearch] = useState("");

  // Промокоды
  const [promos, setPromos] = useState<AdminPromo[]>([]);
  const [promosLoading, setPromosLoading] = useState(false);

  // Роли и каталог пермишенов (GET /api/admin/roles)
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [permissionCatalog, setPermissionCatalog] = useState<AdminPermission[]>([]);
  const [rolesLoading, setRolesLoading] = useState(false);

  // Диалог «Выдать/продлить»
  const [grantTarget, setGrantTarget] = useState<AdminUser | null>(null);
  const [grantDays, setGrantDays] = useState("30");
  const [grantKind, setGrantKind] = useState<"trial" | "manual">("manual");
  const [grantNote, setGrantNote] = useState("");
  const [grantSubmitting, setGrantSubmitting] = useState(false);

  // Диалог «Отозвать»
  const [revokeTarget, setRevokeTarget] = useState<AdminUser | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [revokeSubmitting, setRevokeSubmitting] = useState(false);

  // Диалог «Роли…» пользователя (чекбоксы всех ролей, сохранение по diff)
  const [userRolesTarget, setUserRolesTarget] = useState<AdminUser | null>(null);
  const [userRolesSelected, setUserRolesSelected] = useState<Set<string>>(new Set());
  const [userRolesSubmitting, setUserRolesSubmitting] = useState(false);

  // Настройки (вкладка «Настройки»)
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  // Снапшот загруженных из БД значений: null = ещё не загружены (или загрузка
  // упала) — форму без него не рендерим, чтобы «Сохранить» не перезаписал
  // реальные настройки зашитыми дефолтами. При сабмите отправляем только
  // поля, отличающиеся от снапшота (RPC: NULL = не менять) — иначе сохранение
  // одного поля молча перетирало бы конкурентное изменение второго.
  const [loadedSettings, setLoadedSettings] = useState<AdminSettings | null>(null);
  const [signupEnabled, setSignupEnabled] = useState(true);
  const [trialDays, setTrialDays] = useState("7");

  // Форма создания промокода
  const [promoCode, setPromoCode] = useState("");
  const [promoDays, setPromoDays] = useState("30");
  const [promoMax, setPromoMax] = useState("1");
  const [promoExpiresAt, setPromoExpiresAt] = useState("");
  const [promoNote, setPromoNote] = useState("");
  const [promoSubmitting, setPromoSubmitting] = useState(false);

  // Форма создания роли. Slug автогенерируется транслитом из названия, пока
  // админ не начал править его вручную (roleSlugTouched).
  const [roleName, setRoleName] = useState("");
  const [roleSlug, setRoleSlug] = useState("");
  const [roleSlugTouched, setRoleSlugTouched] = useState(false);
  const [roleDescription, setRoleDescription] = useState("");
  const [roleCreatePerms, setRoleCreatePerms] = useState<Set<string>>(new Set());
  const [roleCreateSubmitting, setRoleCreateSubmitting] = useState(false);

  // Диалог редактирования несистемной роли
  const [editRole, setEditRole] = useState<AdminRole | null>(null);
  const [editRoleName, setEditRoleName] = useState("");
  const [editRoleDescription, setEditRoleDescription] = useState("");
  const [editRolePerms, setEditRolePerms] = useState<Set<string>>(new Set());
  const [editRoleSubmitting, setEditRoleSubmitting] = useState(false);

  // Диалог удаления несистемной роли (с подтверждением)
  const [deleteRole, setDeleteRole] = useState<AdminRole | null>(null);
  const [deleteRoleSubmitting, setDeleteRoleSubmitting] = useState(false);

  const showError = useCallback(
    (description: string) => {
      toast({ variant: "destructive", title: "Ошибка", description });
    },
    [toast],
  );

  const fetchUsers = useCallback(
    async (searchQuery: string) => {
      setUsersLoading(true);
      try {
        const params = new URLSearchParams();
        if (searchQuery.trim()) params.set("search", searchQuery.trim());
        const query = params.toString();
        const res = await fetch(resolveApiUrl(`/api/admin/users${query ? `?${query}` : ""}`));
        if (!res.ok) {
          showError(
            await readErrorText(res, "Не удалось загрузить список пользователей."),
          );
          return;
        }
        const data = await res.json();
        const list: AdminUser[] = Array.isArray(data?.users) ? data.users : [];
        // roles приходит jsonb-агрегатом — нормализуем в [] один раз здесь,
        // чтобы рендер и диалог «Роли…» не падали на null.
        setUsers(
          list.map((u) => ({ ...u, roles: Array.isArray(u.roles) ? u.roles : [] })),
        );
      } catch {
        showError("Не удалось загрузить список пользователей.");
      } finally {
        setUsersLoading(false);
      }
    },
    [showError],
  );

  const fetchPromos = useCallback(async () => {
    setPromosLoading(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/promos"));
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось загрузить список промокодов."));
        return;
      }
      const data = await res.json();
      setPromos(Array.isArray(data?.promos) ? data.promos : []);
    } catch {
      showError("Не удалось загрузить список промокодов.");
    } finally {
      setPromosLoading(false);
    }
  }, [showError]);

  const fetchRoles = useCallback(async () => {
    setRolesLoading(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/roles"));
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось загрузить список ролей."));
        return;
      }
      const data = await res.json();
      setRoles(Array.isArray(data?.roles) ? data.roles : []);
      setPermissionCatalog(Array.isArray(data?.permissions) ? data.permissions : []);
    } catch {
      showError("Не удалось загрузить список ролей.");
    } finally {
      setRolesLoading(false);
    }
  }, [showError]);

  const applySettings = useCallback((settings: unknown) => {
    const s = settings as Partial<AdminSettings> | null | undefined;
    const snapshot: AdminSettings = {
      signupEnabled: s?.signupEnabled === true,
      signupTrialDays: typeof s?.signupTrialDays === "number" ? s.signupTrialDays : 0,
    };
    setLoadedSettings(snapshot);
    setSignupEnabled(snapshot.signupEnabled);
    setTrialDays(String(snapshot.signupTrialDays));
  }, []);

  const fetchSettings = useCallback(async () => {
    setSettingsLoading(true);
    // Сбрасываем снапшот: если загрузка упадёт, вместо формы покажем
    // ошибку с «Повторить» — старые/дефолтные значения сохранять нельзя.
    setLoadedSettings(null);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/settings"));
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось загрузить настройки."));
        return;
      }
      const data = await res.json();
      applySettings(data?.settings);
    } catch {
      showError("Не удалось загрузить настройки.");
    } finally {
      setSettingsLoading(false);
    }
  }, [showError, applySettings]);

  useEffect(() => {
    // Грузим только данные доступных вкладок — на закрытые разделы сервер
    // всё равно ответит 403, а тосты об этом только шумели бы.
    if (canUsersView) void fetchUsers("");
    if (canPromosView) void fetchPromos();
    if (canRolesManage) void fetchRoles();
    // «Настройки» могут оказаться первой доступной вкладкой (например, роль
    // только с settings.view) — onValueChange при первом рендере не вызывается.
    if (defaultTab === "settings") void fetchSettings();
  }, [
    canUsersView,
    canPromosView,
    canRolesManage,
    defaultTab,
    fetchUsers,
    fetchPromos,
    fetchRoles,
    fetchSettings,
  ]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setAppliedSearch(search);
    void fetchUsers(search);
  };

  const openGrantDialog = (user: AdminUser) => {
    setGrantDays("30");
    setGrantKind("manual");
    setGrantNote("");
    setGrantTarget(user);
  };

  const openRevokeDialog = (user: AdminUser) => {
    setRevokeReason("");
    setRevokeTarget(user);
  };

  const openUserRolesDialog = (user: AdminUser) => {
    setUserRolesSelected(new Set(user.roles.map((r) => r.slug)));
    setUserRolesTarget(user);
  };

  const handleGrantSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!grantTarget) return;

    const days = Number.parseInt(grantDays, 10);
    if (!Number.isInteger(days) || days <= 0) {
      showError("Количество дней должно быть целым положительным числом.");
      return;
    }

    setGrantSubmitting(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/grants"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: grantTarget.email,
          days,
          kind: grantKind,
          note: grantNote.trim() || undefined,
        }),
      });
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось выдать доступ."));
        return;
      }
      const data = await res.json();
      toast({
        title: "Доступ выдан",
        description: data?.expiresAt
          ? `${grantTarget.email}: доступ до ${formatDate(data.expiresAt)}`
          : `${grantTarget.email}: доступ продлён на ${days} дн.`,
      });
      setGrantTarget(null);
      void fetchUsers(appliedSearch);
    } catch {
      showError("Не удалось выдать доступ.");
    } finally {
      setGrantSubmitting(false);
    }
  };

  const handleRevokeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!revokeTarget) return;

    setRevokeSubmitting(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/revoke"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: revokeTarget.email,
          reason: revokeReason.trim() || undefined,
        }),
      });
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось отозвать доступ."));
        return;
      }
      const data = await res.json();
      toast({
        title: "Доступ отозван",
        description: `${revokeTarget.email}: отозвано грантов — ${data?.revokedCount ?? 0}`,
      });
      setRevokeTarget(null);
      void fetchUsers(appliedSearch);
    } catch {
      showError("Не удалось отозвать доступ.");
    } finally {
      setRevokeSubmitting(false);
    }
  };

  // Сохранение диалога «Роли…»: diff между исходным и отмеченным наборами →
  // последовательные POST /api/admin/user-roles (assign/revoke). Assign
  // раньше revoke: при «пересадке» админ-роли промежуточное состояние не
  // остаётся без носителей admin.roles.manage (иначе серверный guard
  // «последнего администратора» отбил бы revoke).
  const handleUserRolesSubmit = async () => {
    if (!userRolesTarget) return;
    const original = new Set(userRolesTarget.roles.map((r) => r.slug));
    const ops: { roleSlug: string; action: "assign" | "revoke" }[] = [
      ...[...userRolesSelected]
        .filter((slug) => !original.has(slug))
        .map((slug) => ({ roleSlug: slug, action: "assign" as const })),
      ...[...original]
        .filter((slug) => !userRolesSelected.has(slug))
        .map((slug) => ({ roleSlug: slug, action: "revoke" as const })),
    ];
    if (ops.length === 0) {
      setUserRolesTarget(null);
      toast({ title: "Изменений нет", description: "Набор ролей не изменился." });
      return;
    }

    setUserRolesSubmitting(true);
    let failed = false;
    try {
      for (const op of ops) {
        const res = await fetch(resolveApiUrl("/api/admin/user-roles"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: userRolesTarget.email,
            roleSlug: op.roleSlug,
            action: op.action,
          }),
        });
        if (!res.ok) {
          showError(await readErrorText(res, "Не удалось изменить роли пользователя."));
          failed = true;
          break;
        }
      }
      if (!failed) {
        toast({ title: "Роли обновлены", description: userRolesTarget.email });
      }
    } catch {
      showError("Не удалось изменить роли пользователя.");
    } finally {
      setUserRolesSubmitting(false);
      // Закрываем и рефетчим в любом исходе: часть операций могла успеть
      // примениться — источник истины после сохранения только сервер.
      setUserRolesTarget(null);
      void fetchUsers(appliedSearch);
      void fetchRoles(); // usersCount ролей изменился
    }
  };

  const handleSettingsSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Рид-онли просмотр (settings.view без settings.manage): Enter в поле дней
    // триала вызывает implicit submission — не дёргаем POST, сервер отбил бы 403.
    if (!canSettingsManage) return;
    // Без снапшота (загрузка упала) форма не рендерится, но на всякий случай.
    if (!loadedSettings) return;

    const days = Number.parseInt(trialDays, 10);
    if (!Number.isInteger(days) || String(days) !== trialDays.trim()) {
      showError("Число дней триала должно быть целым числом.");
      return;
    }
    if (days < 0) {
      showError("Число дней триала не может быть отрицательным");
      return;
    }

    // Отправляем только изменённые поля: RPC трактует отсутствующее поле как
    // NULL = «не менять», так конкурентное изменение второго поля другим
    // админом не будет молча перезаписано.
    const body: { signupEnabled?: boolean; signupTrialDays?: number } = {};
    if (signupEnabled !== loadedSettings.signupEnabled) body.signupEnabled = signupEnabled;
    if (days !== loadedSettings.signupTrialDays) body.signupTrialDays = days;
    if (Object.keys(body).length === 0) {
      toast({ title: "Изменений нет", description: "Настройки уже сохранены." });
      return;
    }

    setSettingsSaving(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/settings"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось сохранить настройки."));
        return;
      }
      const data = await res.json();
      // Перезагружаем значения из ответа — сервер возвращает актуальное
      // состояние billing_settings после UPDATE.
      applySettings(data?.settings);
      toast({ title: "Настройки сохранены" });
    } catch {
      showError("Не удалось сохранить настройки.");
    } finally {
      setSettingsSaving(false);
    }
  };

  const handlePromoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const code = promoCode.trim();
    if (!code) {
      showError("Укажите код промокода.");
      return;
    }
    const days = Number.parseInt(promoDays, 10);
    if (!Number.isInteger(days) || days <= 0) {
      showError("Количество дней должно быть целым положительным числом.");
      return;
    }
    const max = Number.parseInt(promoMax, 10);
    if (!Number.isInteger(max) || max <= 0) {
      showError("Максимум активаций должен быть целым положительным числом.");
      return;
    }

    setPromoSubmitting(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/promos"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          days,
          max,
          // date input отдаёт YYYY-MM-DD; конец дня, чтобы код работал весь день
          expiresAt: promoExpiresAt ? `${promoExpiresAt}T23:59:59+03:00` : undefined,
          note: promoNote.trim() || undefined,
        }),
      });
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось создать промокод."));
        return;
      }
      const data = await res.json();
      toast({
        title: "Промокод создан",
        description: `${data?.code ?? code.toUpperCase()} — ${days} дн.`,
      });
      setPromoCode("");
      setPromoDays("30");
      setPromoMax("1");
      setPromoExpiresAt("");
      setPromoNote("");
      void fetchPromos();
    } catch {
      showError("Не удалось создать промокод.");
    } finally {
      setPromoSubmitting(false);
    }
  };

  const handleRoleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const name = roleName.trim();
    if (name.length < 2) {
      showError("Название роли должно быть не короче 2 символов.");
      return;
    }
    const slug = roleSlug.trim();
    if (!ROLE_SLUG_RE.test(slug)) {
      showError(
        "Идентификатор роли — от 2 до 40 символов: строчные латинские буквы, цифры и «_».",
      );
      return;
    }

    setRoleCreateSubmitting(true);
    try {
      const res = await fetch(resolveApiUrl("/api/admin/roles"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug,
          name,
          description: roleDescription.trim() || undefined,
          permissions: [...roleCreatePerms],
        }),
      });
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось создать роль."));
        return;
      }
      toast({ title: "Роль создана", description: `${name} (${slug})` });
      setRoleName("");
      setRoleSlug("");
      setRoleSlugTouched(false);
      setRoleDescription("");
      setRoleCreatePerms(new Set());
      void fetchRoles();
    } catch {
      showError("Не удалось создать роль.");
    } finally {
      setRoleCreateSubmitting(false);
    }
  };

  const openEditRoleDialog = (role: AdminRole) => {
    setEditRoleName(role.name);
    setEditRoleDescription(role.description ?? "");
    setEditRolePerms(new Set(role.permissions));
    setEditRole(role);
  };

  const handleRoleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editRole) return;

    const name = editRoleName.trim();
    if (name.length < 2) {
      showError("Название роли должно быть не короче 2 символов.");
      return;
    }

    setEditRoleSubmitting(true);
    try {
      const res = await fetch(
        resolveApiUrl(`/api/admin/roles/${encodeURIComponent(editRole.slug)}`),
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            // Пустая строка — валидная «очистка» описания (NULL для RPC
            // значит «не менять», поэтому undefined не подходит).
            description: editRoleDescription.trim(),
            // Полная замена набора пермишенов (контракт admin_update_role).
            permissions: [...editRolePerms],
          }),
        },
      );
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось сохранить роль."));
        return;
      }
      toast({ title: "Роль обновлена", description: name });
      setEditRole(null);
      void fetchRoles();
      // Имя роли показывается в бейджах на вкладке «Пользователи».
      if (canUsersView) void fetchUsers(appliedSearch);
    } catch {
      showError("Не удалось сохранить роль.");
    } finally {
      setEditRoleSubmitting(false);
    }
  };

  const handleRoleDeleteSubmit = async () => {
    if (!deleteRole) return;

    setDeleteRoleSubmitting(true);
    try {
      const res = await fetch(
        resolveApiUrl(`/api/admin/roles/${encodeURIComponent(deleteRole.slug)}`),
        { method: "DELETE" },
      );
      if (!res.ok) {
        showError(await readErrorText(res, "Не удалось удалить роль."));
        return;
      }
      toast({ title: "Роль удалена", description: deleteRole.name });
      setDeleteRole(null);
      void fetchRoles();
      if (canUsersView) void fetchUsers(appliedSearch);
    } catch {
      showError("Не удалось удалить роль.");
    } finally {
      setDeleteRoleSubmitting(false);
    }
  };

  // Колонка «Действия» в таблице пользователей нужна только при наличии
  // хотя бы одного manage-пермишена — рид-онли админ видит таблицу без неё.
  const showUserActions = canAccessManage || canRolesManage;
  const userTableCols = showUserActions ? 7 : 6;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Админка</h1>
          <p className="text-sm text-muted-foreground">
            Пользователи, доступы, промокоды и роли
          </p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <a href="/workspace">В воркспейс</a>
        </Button>
      </div>

      {defaultTab === null ? (
        // is_admin пропустил (какой-то admin.* пермишен есть), но ни одного
        // view/manage для существующих вкладок не нашлось — честно говорим.
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            У вас нет доступа ни к одному разделу админки.
          </CardContent>
        </Card>
      ) : (
        <Tabs
          defaultValue={defaultTab}
          onValueChange={(value) => {
            // Значения настроек грузятся при каждом открытии вкладки —
            // тумблер регистрации могли переключить в другой сессии.
            if (value === "settings") void fetchSettings();
          }}
        >
          <TabsList>
            {tabs.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {/* ── Пользователи ── */}
          {canUsersView && (
            <TabsContent value="users">
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">Пользователи</CardTitle>
                  <CardDescription>
                    Сортировка: сначала те, у кого доступ заканчивается раньше
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <form onSubmit={handleSearchSubmit} className="mb-4 flex gap-2">
                    <Input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Поиск: email, имя, фамилия, телефон"
                      className="max-w-sm"
                    />
                    <Button type="submit" variant="secondary" disabled={usersLoading}>
                      Найти
                    </Button>
                  </form>

                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b">
                          <th className={thClass}>Email</th>
                          <th className={thClass}>ФИО</th>
                          <th className={thClass}>Телефон</th>
                          <th className={thClass}>Статус</th>
                          <th className={thClass}>Доступ до</th>
                          <th className={thClass}>Осталось дней</th>
                          {showUserActions && <th className={thClass}>Действия</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {usersLoading ? (
                          <tr>
                            <td
                              colSpan={userTableCols}
                              className="px-3 py-8 text-center text-muted-foreground"
                            >
                              Загрузка…
                            </td>
                          </tr>
                        ) : users.length === 0 ? (
                          <tr>
                            <td
                              colSpan={userTableCols}
                              className="px-3 py-8 text-center text-muted-foreground"
                            >
                              Пользователи не найдены
                            </td>
                          </tr>
                        ) : (
                          users.map((user) => {
                            const badge = statusBadge(user);
                            const fio =
                              [user.last_name, user.first_name].filter(Boolean).join(" ") || "—";
                            const isSelf =
                              currentEmail !== "" &&
                              user.email.toLowerCase() === currentEmail;
                            return (
                              <tr key={user.email} className="border-b last:border-b-0">
                                <td className={tdClass}>
                                  <span className="font-medium">{user.email}</span>
                                  {user.roles.map((role) => (
                                    <span key={role.slug} className={roleBadgeClass}>
                                      {role.name}
                                    </span>
                                  ))}
                                </td>
                                <td className={tdClass}>{fio}</td>
                                <td className={tdClass}>{user.phone || "—"}</td>
                                <td className={tdClass}>
                                  <span
                                    className={cn(
                                      "inline-flex rounded-full border px-2 py-0.5 text-xs font-medium",
                                      badge.className,
                                    )}
                                  >
                                    {badge.label}
                                  </span>
                                </td>
                                <td className={tdClass}>{formatDate(user.access_until)}</td>
                                <td
                                  className={cn(
                                    tdClass,
                                    typeof user.days_left === "number" &&
                                      user.days_left <= 3 &&
                                      "text-red-600 font-medium",
                                  )}
                                >
                                  {typeof user.days_left === "number" ? user.days_left : "—"}
                                </td>
                                {showUserActions && (
                                  <td className={tdClass}>
                                    <div className="flex gap-2">
                                      {canAccessManage && (
                                        <>
                                          {/* Гранты не влияют на доступ админа
                                              (kind='admin' — постоянный доступ,
                                              вычисляется по admin.*-пермишенам):
                                              выдача/отзыв на такой строке —
                                              вводящий в заблуждение no-op,
                                              поэтому кнопки дизейблим и
                                              отправляем в «Роли…». */}
                                          <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => openGrantDialog(user)}
                                            disabled={user.kind === "admin"}
                                            title={
                                              user.kind === "admin"
                                                ? "Доступ администратора постоянный — управляется ролями (диалог «Роли…»)"
                                                : undefined
                                            }
                                          >
                                            Выдать/продлить
                                          </Button>
                                          <Button
                                            variant="ghost"
                                            size="sm"
                                            className="text-destructive hover:text-destructive"
                                            onClick={() => openRevokeDialog(user)}
                                            disabled={
                                              user.kind === "admin" ||
                                              user.status !== "active"
                                            }
                                            title={
                                              user.kind === "admin"
                                                ? "Доступ администратора постоянный — управляется ролями (диалог «Роли…»)"
                                                : undefined
                                            }
                                          >
                                            Отозвать
                                          </Button>
                                        </>
                                      )}
                                      {canRolesManage && (
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          onClick={() => openUserRolesDialog(user)}
                                          // Собственные роли менять нельзя (иначе
                                          // можно остаться без админов); пока auth
                                          // грузится, «свою» строку ещё не
                                          // отличить — дизейблим все.
                                          disabled={isSelf || authLoading}
                                          title={
                                            isSelf
                                              ? "Нельзя изменять собственные роли"
                                              : undefined
                                          }
                                        >
                                          Роли…
                                        </Button>
                                      )}
                                    </div>
                                  </td>
                                )}
                              </tr>
                            );
                          })
                        )}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          )}

          {/* ── Промокоды ── */}
          {canPromosView && (
            <TabsContent value="promos">
              <div
                className={cn(
                  "grid gap-4",
                  // Без promos.manage формы создания нет — таблица на всю ширину.
                  canPromosManage && "lg:grid-cols-[1fr_360px]",
                )}
              >
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">Промокоды</CardTitle>
                    <CardDescription>Созданные коды и их активации</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className={thClass}>Код</th>
                            <th className={thClass}>Дней</th>
                            <th className={thClass}>Активаций</th>
                            <th className={thClass}>Годен до</th>
                            <th className={thClass}>Заметка</th>
                          </tr>
                        </thead>
                        <tbody>
                          {promosLoading ? (
                            <tr>
                              <td
                                colSpan={5}
                                className="px-3 py-8 text-center text-muted-foreground"
                              >
                                Загрузка…
                              </td>
                            </tr>
                          ) : promos.length === 0 ? (
                            <tr>
                              <td
                                colSpan={5}
                                className="px-3 py-8 text-center text-muted-foreground"
                              >
                                Промокодов пока нет
                              </td>
                            </tr>
                          ) : (
                            promos.map((promo) => (
                              <tr key={promo.code} className="border-b last:border-b-0">
                                <td className={cn(tdClass, "font-mono font-medium")}>
                                  {promo.code}
                                  {promo.disabled_at && (
                                    <span className="ml-2 rounded-full border border-red-200 bg-red-100 px-2 py-0.5 text-xs font-sans font-medium text-red-800">
                                      отключён
                                    </span>
                                  )}
                                </td>
                                <td className={tdClass}>{promo.grant_days}</td>
                                <td className={tdClass}>
                                  {promo.redeemed_count}/{promo.max_redemptions}
                                </td>
                                <td className={tdClass}>{formatDate(promo.expires_at)}</td>
                                <td
                                  className={cn(
                                    tdClass,
                                    "max-w-[240px] truncate whitespace-normal",
                                  )}
                                >
                                  {promo.note || "—"}
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>

                {canPromosManage && (
                  <Card className="h-fit">
                    <CardHeader>
                      <CardTitle className="text-lg">Новый промокод</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <form onSubmit={handlePromoSubmit} className="space-y-4">
                        <div className="space-y-2">
                          <Label htmlFor="promo-code">Код</Label>
                          <div className="flex gap-2">
                            <Input
                              id="promo-code"
                              value={promoCode}
                              onChange={(e) => setPromoCode(e.target.value.toUpperCase())}
                              placeholder="LAW-XXXX-XXXX"
                              className="font-mono"
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              onClick={() => setPromoCode(generatePromoCode())}
                            >
                              Сгенерировать
                            </Button>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-2">
                            <Label htmlFor="promo-days">Дней доступа</Label>
                            <Input
                              id="promo-days"
                              type="number"
                              min={1}
                              value={promoDays}
                              onChange={(e) => setPromoDays(e.target.value)}
                            />
                          </div>
                          <div className="space-y-2">
                            <Label htmlFor="promo-max">Макс. активаций</Label>
                            <Input
                              id="promo-max"
                              type="number"
                              min={1}
                              value={promoMax}
                              onChange={(e) => setPromoMax(e.target.value)}
                            />
                          </div>
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="promo-expires">Годен до (необязательно)</Label>
                          <Input
                            id="promo-expires"
                            type="date"
                            value={promoExpiresAt}
                            onChange={(e) => setPromoExpiresAt(e.target.value)}
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="promo-note">Заметка</Label>
                          <Input
                            id="promo-note"
                            value={promoNote}
                            onChange={(e) => setPromoNote(e.target.value)}
                            placeholder="Например: для вебинара 15.07"
                          />
                        </div>
                        <Button type="submit" className="w-full" disabled={promoSubmitting}>
                          {promoSubmitting ? "Создание…" : "Создать промокод"}
                        </Button>
                      </form>
                    </CardContent>
                  </Card>
                )}
              </div>
            </TabsContent>
          )}

          {/* ── Настройки ── */}
          {canSettingsView && (
            <TabsContent value="settings">
              <Card className="max-w-xl">
                <CardHeader>
                  <CardTitle className="text-lg">Настройки</CardTitle>
                  <CardDescription>
                    Регистрация и триал для новых пользователей
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {settingsLoading ? (
                    <p className="py-8 text-center text-sm text-muted-foreground">
                      Загрузка…
                    </p>
                  ) : loadedSettings === null ? (
                    // Загрузка упала — форму с дефолтами не показываем, иначе
                    // «Сохранить» перезаписал бы реальные значения в БД.
                    <div className="space-y-4 py-8 text-center">
                      <p className="text-sm text-muted-foreground">
                        Не удалось загрузить настройки.
                      </p>
                      <Button variant="secondary" onClick={() => void fetchSettings()}>
                        Повторить
                      </Button>
                    </div>
                  ) : (
                    <form onSubmit={handleSettingsSubmit} className="space-y-6">
                      <div className="space-y-2">
                        <div className="flex items-center gap-3">
                          {/* В components/ui нет switch-примитива — кнопка-тумблер
                              на токенах проекта, без новых зависимостей. */}
                          <button
                            type="button"
                            role="switch"
                            aria-checked={signupEnabled}
                            aria-labelledby="signup-enabled-label"
                            onClick={() => setSignupEnabled((v) => !v)}
                            disabled={!canSettingsManage || settingsSaving}
                            className={cn(
                              "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                              signupEnabled ? "bg-primary" : "bg-input",
                            )}
                          >
                            <span
                              className={cn(
                                "pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg transition-transform",
                                signupEnabled ? "translate-x-5" : "translate-x-0",
                              )}
                            />
                          </button>
                          <Label
                            id="signup-enabled-label"
                            className={canSettingsManage ? "cursor-pointer" : undefined}
                            onClick={() => {
                              if (canSettingsManage && !settingsSaving) {
                                setSignupEnabled((v) => !v);
                              }
                            }}
                          >
                            Открытая регистрация
                          </Label>
                        </div>
                        <p className="text-sm text-muted-foreground">
                          Выключено — новые пользователи не смогут зарегистрироваться
                          сами (в т.ч. напрямую через API). Чтобы завести пользователя
                          вручную, временно включите.
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="settings-trial-days">
                          Дней триала новым при регистрации
                        </Label>
                        <Input
                          id="settings-trial-days"
                          type="number"
                          min={0}
                          value={trialDays}
                          onChange={(e) => setTrialDays(e.target.value)}
                          disabled={!canSettingsManage}
                          className="max-w-[160px]"
                        />
                        <p className="text-sm text-muted-foreground">
                          0 — новые пользователи регистрируются без триала.
                        </p>
                      </div>

                      {/* Без settings.manage кнопки нет (рид-онли просмотр);
                          сервер всё равно отбил бы POST 403-м. */}
                      {canSettingsManage && (
                        <Button type="submit" disabled={settingsSaving}>
                          {settingsSaving ? "Сохранение…" : "Сохранить"}
                        </Button>
                      )}
                    </form>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          )}

          {/* ── Роли ── */}
          {canRolesManage && (
            <TabsContent value="roles">
              <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">Роли</CardTitle>
                    <CardDescription>
                      Наборы возможностей; одну роль можно назначить нескольким
                      пользователям, у пользователя может быть несколько ролей
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className={thClass}>Название</th>
                            <th className={thClass}>Slug</th>
                            <th className={thClass}>Возможности</th>
                            <th className={thClass}>Пользователей</th>
                            <th className={thClass}>Действия</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rolesLoading ? (
                            <tr>
                              <td
                                colSpan={5}
                                className="px-3 py-8 text-center text-muted-foreground"
                              >
                                Загрузка…
                              </td>
                            </tr>
                          ) : roles.length === 0 ? (
                            <tr>
                              <td
                                colSpan={5}
                                className="px-3 py-8 text-center text-muted-foreground"
                              >
                                Ролей пока нет
                              </td>
                            </tr>
                          ) : (
                            roles.map((role) => (
                              <tr key={role.id} className="border-b last:border-b-0">
                                <td className={tdClass}>
                                  <span className="font-medium">{role.name}</span>
                                  {role.isSystem && (
                                    <span className="ml-2 rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                                      Системная
                                    </span>
                                  )}
                                </td>
                                <td className={cn(tdClass, "font-mono")}>{role.slug}</td>
                                <td
                                  className={tdClass}
                                  // Полный список слагов — по наведению.
                                  title={role.permissions.join(", ") || undefined}
                                >
                                  {role.permissions.length}
                                </td>
                                <td className={tdClass}>{role.usersCount}</td>
                                <td className={tdClass}>
                                  {role.isSystem ? (
                                    // Системную роль нельзя изменить или удалить
                                    // (серверный guard 'system role').
                                    <span className="text-muted-foreground">—</span>
                                  ) : (
                                    <div className="flex gap-2">
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => openEditRoleDialog(role)}
                                      >
                                        Изменить
                                      </Button>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="text-destructive hover:text-destructive"
                                        onClick={() => setDeleteRole(role)}
                                      >
                                        Удалить
                                      </Button>
                                    </div>
                                  )}
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>

                <Card className="h-fit">
                  <CardHeader>
                    <CardTitle className="text-lg">Новая роль</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <form onSubmit={handleRoleCreateSubmit} className="space-y-4">
                      <div className="space-y-2">
                        <Label htmlFor="role-name">Название</Label>
                        <Input
                          id="role-name"
                          value={roleName}
                          onChange={(e) => {
                            setRoleName(e.target.value);
                            if (!roleSlugTouched) {
                              setRoleSlug(slugifyRoleName(e.target.value));
                            }
                          }}
                          placeholder="Например: Поддержка"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="role-slug">Идентификатор (slug)</Label>
                        <Input
                          id="role-slug"
                          value={roleSlug}
                          onChange={(e) => {
                            const value = e.target.value.toLowerCase();
                            setRoleSlug(value);
                            // Очистили поле — снова автогенерируем из названия.
                            setRoleSlugTouched(value !== "");
                          }}
                          placeholder="podderzhka"
                          className="font-mono"
                        />
                        <p className="text-sm text-muted-foreground">
                          Строчные латинские буквы, цифры и «_», от 2 до 40 символов.
                        </p>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="role-description">Описание</Label>
                        <Input
                          id="role-description"
                          value={roleDescription}
                          onChange={(e) => setRoleDescription(e.target.value)}
                          placeholder="Например: только просмотр админки"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Возможности</Label>
                        <PermissionCheckboxList
                          catalog={permissionCatalog}
                          selected={roleCreatePerms}
                          onToggle={(slug, checked) =>
                            setRoleCreatePerms((prev) => toggleInSet(prev, slug, checked))
                          }
                          disabled={roleCreateSubmitting}
                        />
                      </div>
                      <Button
                        type="submit"
                        className="w-full"
                        disabled={roleCreateSubmitting}
                      >
                        {roleCreateSubmitting ? "Создание…" : "Создать роль"}
                      </Button>
                    </form>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
          )}
        </Tabs>
      )}

      {/* ── Диалог «Выдать/продлить» ── */}
      <Dialog
        open={grantTarget !== null}
        onOpenChange={(open) => {
          if (!open) setGrantTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Выдать или продлить доступ</DialogTitle>
            <DialogDescription>
              {grantTarget?.email} — доступ продлевается встык к текущему.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleGrantSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="grant-days">Дней</Label>
              <Input
                id="grant-days"
                type="number"
                min={1}
                value={grantDays}
                onChange={(e) => setGrantDays(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="grant-kind">Тип</Label>
              <select
                id="grant-kind"
                className={selectClass}
                value={grantKind}
                onChange={(e) => setGrantKind(e.target.value as "trial" | "manual")}
              >
                <option value="manual">Оплачено вручную</option>
                <option value="trial">Триал</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="grant-note">Заметка</Label>
              <Input
                id="grant-note"
                value={grantNote}
                onChange={(e) => setGrantNote(e.target.value)}
                placeholder="Например: оплата по счёту №14"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setGrantTarget(null)}
                disabled={grantSubmitting}
              >
                Отмена
              </Button>
              <Button type="submit" disabled={grantSubmitting}>
                {grantSubmitting ? "Сохранение…" : "Выдать доступ"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* ── Диалог «Отозвать» ── */}
      <Dialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Отозвать доступ</DialogTitle>
            <DialogDescription>
              Все активные гранты пользователя {revokeTarget?.email} будут отозваны.
              Действие нельзя отменить — доступ можно будет выдать заново.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleRevokeSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="revoke-reason">Причина</Label>
              <Input
                id="revoke-reason"
                value={revokeReason}
                onChange={(e) => setRevokeReason(e.target.value)}
                placeholder="Например: возврат оплаты"
                autoFocus
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setRevokeTarget(null)}
                disabled={revokeSubmitting}
              >
                Отмена
              </Button>
              <Button type="submit" variant="destructive" disabled={revokeSubmitting}>
                {revokeSubmitting ? "Отзыв…" : "Отозвать доступ"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* ── Диалог «Роли…» пользователя ── */}
      <Dialog
        open={userRolesTarget !== null}
        onOpenChange={(open) => {
          if (!open) setUserRolesTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Роли пользователя</DialogTitle>
            <DialogDescription>
              {userRolesTarget?.email} — отметьте роли, которые должны быть
              назначены. Изменения применятся после сохранения.
            </DialogDescription>
          </DialogHeader>
          {rolesLoading ? (
            <p className="py-4 text-center text-sm text-muted-foreground">Загрузка…</p>
          ) : roles.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              Ролей пока нет — создайте их на вкладке «Роли».
            </p>
          ) : (
            <div className="space-y-2">
              {roles.map((role) => (
                <label key={role.id} className="flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
                    checked={userRolesSelected.has(role.slug)}
                    onChange={(e) =>
                      setUserRolesSelected((prev) =>
                        toggleInSet(prev, role.slug, e.target.checked),
                      )
                    }
                    disabled={userRolesSubmitting}
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium leading-5">
                      {role.name}
                    </span>
                    {role.description && (
                      <span className="block text-sm text-muted-foreground">
                        {role.description}
                      </span>
                    )}
                    <span className="block truncate font-mono text-xs text-muted-foreground">
                      {role.slug}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setUserRolesTarget(null)}
              disabled={userRolesSubmitting}
            >
              Отмена
            </Button>
            <Button
              type="button"
              onClick={() => void handleUserRolesSubmit()}
              disabled={userRolesSubmitting || rolesLoading || roles.length === 0}
            >
              {userRolesSubmitting ? "Сохранение…" : "Сохранить"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Диалог редактирования роли ── */}
      <Dialog
        open={editRole !== null}
        onOpenChange={(open) => {
          if (!open) setEditRole(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Изменить роль</DialogTitle>
            <DialogDescription>
              {editRole?.slug} — набор возможностей заменяется целиком.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleRoleEditSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-role-name">Название</Label>
              <Input
                id="edit-role-name"
                value={editRoleName}
                onChange={(e) => setEditRoleName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-role-description">Описание</Label>
              <Input
                id="edit-role-description"
                value={editRoleDescription}
                onChange={(e) => setEditRoleDescription(e.target.value)}
                placeholder="Например: только просмотр админки"
              />
            </div>
            <div className="space-y-2">
              <Label>Возможности</Label>
              <PermissionCheckboxList
                catalog={permissionCatalog}
                selected={editRolePerms}
                onToggle={(slug, checked) =>
                  setEditRolePerms((prev) => toggleInSet(prev, slug, checked))
                }
                disabled={editRoleSubmitting}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setEditRole(null)}
                disabled={editRoleSubmitting}
              >
                Отмена
              </Button>
              <Button type="submit" disabled={editRoleSubmitting}>
                {editRoleSubmitting ? "Сохранение…" : "Сохранить"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* ── Диалог удаления роли ── */}
      <Dialog
        open={deleteRole !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteRole(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить роль?</DialogTitle>
            <DialogDescription>
              Роль «{deleteRole?.name ?? ""}» будет удалена и снята со всех
              пользователей
              {typeof deleteRole?.usersCount === "number" && deleteRole.usersCount > 0
                ? ` (сейчас назначена: ${deleteRole.usersCount})`
                : ""}
              . Действие нельзя отменить.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteRole(null)}
              disabled={deleteRoleSubmitting}
            >
              Отмена
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleRoleDeleteSubmit()}
              disabled={deleteRoleSubmitting}
            >
              {deleteRoleSubmitting ? "Удаление…" : "Удалить роль"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ToasterClient />
    </div>
  );
}
