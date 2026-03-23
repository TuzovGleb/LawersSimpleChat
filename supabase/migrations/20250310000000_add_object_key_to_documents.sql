-- Add object_key column to store the S3 key of the original uploaded file
alter table public.project_documents
  add column if not exists object_key text;
