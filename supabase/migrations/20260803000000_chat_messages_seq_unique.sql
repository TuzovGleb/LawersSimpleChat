-- Безопасный повтор записи хода: ключ хода + уникальный (session_id, seq).
--
-- Ход сохраняется одним insert'ом (user + assistant + tool-строки). Транспортный
-- сбой на пути к Supabase (httpx.ReadError на переиспользованном keep-alive
-- соединении) рвал этот insert, а postgrest ретраит только GET/HEAD — POST не
-- защищён ничем. Ход терялся целиком: и вопрос юриста, и ответ, и блоки
-- подготовленного документа. Скачивание такого документа потом вечно отдавало
-- 404 «Документ не найден».
--
-- Повторять insert вслепую нельзя: транспортная ошибка приходит при чтении
-- ОТВЕТА, то есть запрос до базы уже долетел и мог закоммититься. Поэтому у хода
-- появляется собственный идентификатор turn_id: перед повтором приложение
-- спрашивает базу, есть ли уже строки с этим turn_id, и повторяет только когда
-- их нет.
--
-- Одного turn_id мало: seq назначается в приложении (read-then-write), а лок
-- внутрипроцессный, тогда как прод масштабируется несколькими инстансами. Без
-- уникального индекса два хода молча садятся в один диапазон seq и переписка
-- перемешивается. С индексом такая гонка становится видимой ошибкой, и
-- приложение перечитывает seq и повторяет вставку.
alter table public.chat_messages
  add column if not exists turn_id uuid;  -- ключ идемпотентности одного хода

create index if not exists chat_messages_session_turn_idx
  on public.chat_messages (session_id, turn_id);

-- Схлопнувшиеся seq могли появиться до этой миграции: _next_seq читает максимум
-- и прибавляет единицу, а при сбое чтения раньше отдавал 0 — целый поздний ход
-- садился на диапазон ранних. Поэтому seq здесь НЕ опора: порядок восстанавливаем
-- по времени, ровно как это делал бэкфилл в 20260613000000_add_tool_messages.sql.
-- created_at проставляется построчно при сборке хода, так что ход остаётся
-- непрерывным блоком, а seq работает лишь тайбрейком на совпадении меток.
-- Трогаем только задетые сессии.
with dup_sessions as (
  select session_id
  from public.chat_messages
  group by session_id, seq
  having count(*) > 1
),
ranked as (
  select
    m.id,
    row_number() over (
      partition by m.session_id
      order by m.created_at asc, m.seq asc, m.id asc
    ) - 1 as rn
  from public.chat_messages m
  where m.session_id in (select session_id from dup_sessions)
)
update public.chat_messages m
set seq = ranked.rn
from ranked
where ranked.id = m.id
  and m.seq is distinct from ranked.rn;

create unique index if not exists chat_messages_session_seq_uniq
  on public.chat_messages (session_id, seq);

-- Прежний неуникальный индекс по той же паре стал избыточным: уникальный
-- обслуживает те же запросы на сортировку.
drop index if exists public.chat_messages_session_seq_idx;

comment on column public.chat_messages.turn_id is
  'Ключ хода: позволяет проверить, записался ли ход, прежде чем повторять insert';
comment on index public.chat_messages_session_seq_uniq is
  'Уникальность порядка в сессии: гонка за seq становится видимой ошибкой, а не тихой перемешанной историей';
