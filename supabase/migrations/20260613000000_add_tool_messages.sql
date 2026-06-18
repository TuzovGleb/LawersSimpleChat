-- Tool-call support for the agentic chat loop.
--
-- chat_messages becomes the full, faithful message log: assistant rows may
-- carry tool_calls, and tool results are stored as separate role='tool' rows.
-- Heavy payloads (e.g. full court-decision text) are NOT stored here — tool
-- rows keep only minimal state and are rehydrated from their source at read
-- time. `seq` gives a stable per-session ordering within a turn.

-- Allow the 'tool' role.
alter table public.chat_messages drop constraint if exists chat_messages_role_check;
alter table public.chat_messages
  add constraint chat_messages_role_check
  check (role in ('user', 'assistant', 'system', 'tool'));

-- Tool-call metadata.
alter table public.chat_messages
  add column if not exists tool_calls   jsonb,   -- on assistant rows: [{id,name,args}]
  add column if not exists tool_call_id text,    -- on tool rows: links to the call
  add column if not exists tool_name    text,    -- on tool rows: handler dispatch key
  add column if not exists tool_state   jsonb,   -- on tool rows: minimal persisted state
  add column if not exists seq          bigint;  -- per-session ordering within a turn

-- Backfill seq from existing created_at ordering so old conversations sort
-- correctly alongside new rows.
with ordered as (
  select
    id,
    row_number() over (partition by session_id order by created_at asc, id asc) - 1 as rn
  from public.chat_messages
)
update public.chat_messages m
set seq = ordered.rn
from ordered
where ordered.id = m.id
  and m.seq is null;

-- Every row now has a seq; forbid NULLs so future inserts can't sort to the end
-- (the application always assigns seq). No default: writers must set it.
alter table public.chat_messages alter column seq set not null;

create index if not exists chat_messages_session_seq_idx
  on public.chat_messages (session_id, seq asc);

comment on column public.chat_messages.tool_calls is 'Assistant tool calls: [{id,name,args}]';
comment on column public.chat_messages.tool_call_id is 'Tool row: id of the originating tool call';
comment on column public.chat_messages.tool_name is 'Tool row: tool name (handler dispatch key)';
comment on column public.chat_messages.tool_state is 'Tool row: minimal state for rehydration (no heavy payloads)';
comment on column public.chat_messages.seq is 'Per-session message ordering (stable within a turn)';
