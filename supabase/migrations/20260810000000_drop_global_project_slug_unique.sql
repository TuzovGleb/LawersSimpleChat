-- Убираем ГЛОБАЛЬНЫЙ unique на projects.slug (projects_slug_key): он действовал
-- поверх всех пользователей, а pre-check уникальности в /api/projects ходит через
-- RLS-клиент (policy projects_owner: user_id = auth.uid()) и чужие slug'и не
-- видит — поэтому авто-создание проекта «Мои дела» падало с 409 у каждого
-- пользователя после первого. Уникальность В РАМКАХ пользователя по-прежнему
-- обеспечивает projects_user_slug_idx (user_id, slug) из
-- 20241111100000_add_projects_and_documents.sql.
alter table public.projects drop constraint if exists projects_slug_key;
