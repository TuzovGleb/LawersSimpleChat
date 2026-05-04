alter table public.chat_messages
  add column if not exists attached_document_ids uuid[] not null default '{}';

