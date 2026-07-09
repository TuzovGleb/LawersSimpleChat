# Billing / gating: администрирование доступа

Как применить миграцию gating'а, назначить админа и управлять доступом
(триалы, промокоды, ручная выдача). Модель данных и контракт `entitlement`
описаны в конце — они не изменятся при подключении платёжного провайдера.

## 1. Как применить миграцию

Миграция: `supabase/migrations/20260709000000_billing_and_profiles.sql`.
Применяется вручную, как остальные (см. `supabase/migrations/README.md`):
открыть Supabase Studio → SQL Editor → вставить содержимое файла → Run.
Сначала на staging-инстансе, после проверки — на prod.

Миграция идемпотентна: повторный запуск безопасен (create table if not
exists, create or replace function, guarded backfill — второй 7-дневный
грант существующим пользователям выдан не будет).

**ВАЖНО — порядок деплоя.** Код фронта работает fail-closed: гейт в API
вызывает RPC `get_my_entitlement`, и если функции в базе ещё нет, отправка
сообщений/загрузка документов вернёт 503 для ВСЕХ пользователей. Поэтому
строго: **сначала миграция, потом деплой фронта**. Обратный порядок = отказ
сервиса до применения миграции.

## 2. Роли, пермишены и назначение админов

Права построены на RBAC-таблицах `roles` / `permissions` /
`role_permissions` / `user_roles` (колонки роли в `profiles` нет).
Принцип:

- **Пермишены** — фиксированный каталог, зашитый в миграцию (каждый slug
  соответствует реальной проверке в коде). Из админки не создаются.
- **Роли** — произвольные наборы пермишенов, создаются и редактируются на
  вкладке «Роли» страницы `/admin`. У пользователя может быть несколько
  ролей — права объединяются.
- Доступ к действию = наличие конкретного пермишена хотя бы через одну роль.
  Доступ к самой странице `/admin` — хотя бы один `admin.*` пермишен
  (RPC `is_admin()`).

Миграция создаёт две роли:

| Роль | Что даёт |
| --- | --- |
| `admin` | все пермишены; **системная** — её нельзя редактировать или удалить, при добавлении новых пермишенов они доназначаются ей автоматически |
| `admin_readonly` | только просмотр (пользователи, промокоды, настройки); обычная редактируемая роль-пример |

Все четыре таблицы закрыты RLS deny-all — назначить себе роль с клиента
невозможно, записи идут только через SECURITY DEFINER-RPC (сервисный ключ в
Next-слое не используется и добавлять его туда нельзя).

**Первый админ** назначается вручную SQL-ом (Studio → SQL Editor):

```sql
insert into public.user_roles (user_id, role_id, granted_by)
select u.id, r.id, 'bootstrap'
from auth.users u, public.roles r
where u.email = 'ivan.razvensky@gmail.com' and r.slug = 'admin'
on conflict do nothing;
```

**Дальше — только через `/admin`**: роли создаются/редактируются на вкладке
«Роли», назначаются и снимаются в диалоге «Роли…» на вкладке «Пользователи»
(RPC `admin_assign_role` / `admin_revoke_role`, нужен пермишен
`admin.roles.manage`).

Два защитных инварианта:

- **Свои роли менять нельзя** — сервер отвечает `cannot change own roles`;
  любое изменение админских прав требует второго админа (самоблокировка и
  самоэскалация исключены).
- **Последнего админа снять нельзя** — если операция (снятие роли, удаление
  роли, изменение её пермишенов) оставила бы систему без единого носителя
  `admin.roles.manage`, сервер отвечает `cannot remove last admin` и
  откатывает её. Проверка сериализована advisory-lock'ом, так что и два
  админа, одновременно разжалующие друг друга, систему не осиротят.

## 3. Что происходит с существующими пользователями

При применении миграции (backfill внутри неё же):

- каждому существующему пользователю создаётся пустая строка в `profiles`;
- каждому выдаётся переходный грант `trial` на **7 дней с момента применения
  миграции** (`granted_by = 'system:backfill'`);
- при первом заходе в приложение middleware отправит их на
  `/onboarding/profile` дозаполнить имя/фамилию/телефон (у существующих в
  metadata нет `profile_completed`), после чего они работают как обычно.

Когда 7 дней истекут — режим read-only: история чатов и скачивание готовых
.docx работают, отправка сообщений и загрузка документов закрыты, показан
баннер «свяжитесь с нами». Дальше доступ продлевается админкой или
промокодом.

Новые пользователи получают автотриал при регистрации; длительность — в
`billing_settings.signup_trial_days` (по умолчанию 7; `0` отключает
автотриал). Значение редактируется на вкладке «Настройки» страницы `/admin`.

## 4. Закрытие открытой регистрации

Три независимых слоя, от «спрятать» до «запретить»:

1. **Env-флаг `NEXT_PUBLIC_ENABLE_SIGNUP`** — прячет таб регистрации в UI,
   настраивается per-среда при деплое (на staging просто не включён). Это
   только косметика: прямой запрос к Supabase Auth API флаг не остановит.
2. **Тумблер «Открытая регистрация» в `/admin` → Настройки**
   (`billing_settings.signup_enabled`) — честный запрет на уровне БД: триггер
   `on_auth_user_signup_gate` (BEFORE INSERT ON auth.users) отбивает любую
   вставку с ошибкой `signups_disabled`, в том числе регистрацию напрямую
   через API мимо нашего фронта. Меняется на лету, без деплоя.
3. **Supabase Dashboard → Authentication → «Allow new sign ups»** — нативный
   вариант: блокирует публичную регистрацию, но НЕ приглашения (Invite user).
   Уместен, если staging и prod живут в разных Supabase-проектах и хочется
   закрыть регистрацию на уровне проекта целиком.

**Caveat про наш триггер (слой 2):** он блокирует ЛЮБУЮ вставку в
`auth.users` — включая приглашения и ручное создание пользователей из
Supabase Studio. Процесс ручного онбординга при закрытой регистрации:
временно включить тумблер в `/admin`, зарегистрировать/пригласить
пользователя, выключить обратно. Запасной вариант из Studio:

```sql
update public.billing_settings set signup_enabled = true;  -- открыть
update public.billing_settings set signup_enabled = false; -- закрыть
```

Фронт дополнительно спрашивает `is_signup_enabled()` (RPC доступен anon) и
прячет таб регистрации; при ошибке RPC форма показывается (fail-open) —
настоящий запрет всё равно обеспечивает триггер.

## 5. Сниппеты для Supabase Studio

Обычный путь — страница `/admin`. Сниппеты ниже — запасной вариант напрямую
из Studio (SQL Editor работает под `postgres`, поэтому admin-RPC там
недоступны — `auth.uid()` пуст; пишем в таблицы напрямую).

Выдать/продлить доступ (продление встык к текущему доступу):

```sql
insert into public.access_grants (user_id, kind, ends_at, granted_by, note)
select u.id,
       'manual',
       greatest(now(), coalesce((select max(g.ends_at)
                                 from public.access_grants g
                                 where g.user_id = u.id
                                   and g.revoked_at is null
                                   and g.ends_at > now()), now()))
         + interval '30 days',
       'studio:admin',
       'оплата по счёту №14'
from auth.users u
where lower(u.email) = lower('user@example.com');
```

Отозвать весь активный доступ (журнал append-only — ничего не удаляем,
только ставим `revoked_at`):

```sql
update public.access_grants
set revoked_at = now(), revoked_reason = 'причина отзыва'
where user_id = (select id from auth.users where lower(email) = lower('user@example.com'))
  and revoked_at is null
  and ends_at > now();
```

Создать промокод (код всегда в верхнем регистре; `expires_at` — до какого
числа код можно активировать, НЕ длительность доступа):

```sql
insert into public.promo_codes (code, grant_days, max_redemptions, expires_at, created_by, note)
values (upper('LAW-XXXX-XXXX'), 30, 10, null, 'studio:admin', 'для вебинара');
```

Отключить промокод:

```sql
update public.promo_codes set disabled_at = now() where code = 'LAW-XXXX-XXXX';
```

Обзорные view (только для Studio/service role; от `anon`/`authenticated`
доступ отозван, т.к. view выполняются правами владельца и обходят RLS):

```sql
select * from public.admin_user_overview;   -- все пользователи + статус доступа
select * from public.admin_expiring_soon;   -- активные, у кого доступ кончается в ближайшие 7 дней
select * from public.admin_promo_stats;     -- промокоды и их активации
```

Аудит всех операций — в `public.billing_events` (append-only):

```sql
select * from public.billing_events order by created_at desc limit 100;
```

## 6. Модель данных и контракт entitlement (кратко)

Единый источник истины — append-only журнал `access_grants`. Статус НИКОГДА
не хранится флагом, всегда вычисляется:

> active ⟺ существует грант с `revoked_at IS NULL AND starts_at <= now() < ends_at`.

Таблицы (все под RLS, записи только через SECURITY DEFINER-функции /
service role):

| Таблица | Что хранит |
| --- | --- |
| `profiles` | имя/фамилия/телефон; SELECT только своей строки |
| `access_grants` | журнал доступа: kind (`trial`/`promo`/`manual`/`payment`), starts_at/ends_at, revoked_at; SELECT своих строк |
| `promo_codes` | промокоды; deny-all (не читаются и не перебираются с клиента) |
| `promo_redemptions` | кто какой код погасил (UNIQUE code+user); deny-all |
| `billing_settings` | одна строка настроек (`signup_trial_days`, `signup_enabled`); deny-all |
| `billing_events` | append-only аудит; deny-all |
| `roles` | RBAC-роли (slug, название, `is_system`); deny-all |
| `permissions` | каталог пермишенов (seed-only, из админки не создаются); deny-all |
| `role_permissions` | какие пермишены даёт роль; deny-all |
| `user_roles` | назначения ролей пользователям (несколько ролей = объединение прав); deny-all |

Контракт entitlement (JSONB из `get_my_entitlement()` /
`get_entitlement(uuid)`, TS-тип в `lib/entitlement.ts`):

```json
{ "status": "active" | "expired" | "none",
  "kind": "trial" | "promo" | "manual" | "payment" | null,
  "expires_at": "<ISO>" | null }
```

Правило гейта везде одно: допуск ⟺ `status === "active"`. `kind` и
`expires_at` используются только для текстов баннера. Проверка на сервере
fail-closed: ошибка проверки = 503, нет доступа = 402 с кодом
`SUBSCRIPTION_REQUIRED`.

Промокод при активном доступе продлевает встык:
`greatest(now(), max активный ends_at) + grant_days`.

## 7. Как потом встанет платёжный провайдер

Модель уже готова, менять её не придётся:

- в `access_grants.kind` зарезервировано значение `'payment'`;
- `access_grants.payment_ref` — id транзакции провайдера (UNIQUE-индекс по
  `payment_ref WHERE kind='payment'` даёт идемпотентность вебхука: повторная
  доставка того же события не создаст второй грант);
- webhook провайдера (фаза 2, Python-бэкенд под service role) валидирует
  подпись и просто вставляет строку
  `access_grants(kind='payment', payment_ref=..., ends_at=...)` + событие в
  `billing_events`;
- статус по-прежнему вычисляется из журнала — баннеры, гейты, админка и
  контракт `entitlement` продолжают работать без изменений (для серверной
  проверки произвольного пользователя есть `get_entitlement(uuid)`,
  доступный только service role).
