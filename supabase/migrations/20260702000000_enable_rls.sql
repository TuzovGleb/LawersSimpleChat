-- Enable Row Level Security (RLS) on all tenant tables.
--
-- WHY: without RLS the ONLY thing isolating tenants is application code. Any
-- path that reaches PostgREST with a user's anon/authenticated JWT (notably the
-- transparent /api/supabase-proxy) can otherwise read every tenant's rows, e.g.
-- GET /api/supabase-proxy/rest/v1/project_documents?select=* returns the
-- extracted full text of every project's documents. RLS makes the DATABASE
-- enforce ownership so a code mistake can't leak across tenants.
--
-- HOW THE APP STILL WORKS AFTER THIS:
--   * Server API routes use the cookie-authenticated Supabase client, so they
--     run as the logged-in user (auth.uid() = that user) and see only their own
--     rows — which is exactly what their code already intends.
--   * The Python backend uses the SERVICE ROLE, which bypasses RLS, so history
--     reads/writes and session creation are unaffected.
--   * The browser only reaches these tables via /api/supabase-proxy, which
--     forwards the user's Supabase JWT — so those requests are now correctly
--     scoped to the user instead of seeing everything.
--
-- CAVEAT: rows with a NULL user_id (legacy/anonymous projects & sessions) become
-- invisible to the anon/authenticated role (only the service role sees them).
-- The app requires auth today, so this is acceptable; back-fill user_id first if
-- any anonymous rows must stay user-visible.
--
-- Policies use `to authenticated` (anon gets no policy => denied). Ownership for
-- child tables (chat_messages, project_documents) is derived via their parent.

-- ── projects ──────────────────────────────────────────────────────────────
alter table public.projects enable row level security;

drop policy if exists projects_owner on public.projects;
create policy projects_owner on public.projects
  for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ── chat_sessions (owned directly, or via the session's project) ──────────
alter table public.chat_sessions enable row level security;

drop policy if exists chat_sessions_owner on public.chat_sessions;
create policy chat_sessions_owner on public.chat_sessions
  for all to authenticated
  using (
    user_id = auth.uid()
    or exists (
      select 1 from public.projects p
      where p.id = chat_sessions.project_id and p.user_id = auth.uid()
    )
  )
  with check (
    user_id = auth.uid()
    or exists (
      select 1 from public.projects p
      where p.id = chat_sessions.project_id and p.user_id = auth.uid()
    )
  );

-- ── chat_messages (via the parent session's ownership) ────────────────────
alter table public.chat_messages enable row level security;

drop policy if exists chat_messages_owner on public.chat_messages;
create policy chat_messages_owner on public.chat_messages
  for all to authenticated
  using (
    exists (
      select 1 from public.chat_sessions s
      where s.id = chat_messages.session_id
        and (
          s.user_id = auth.uid()
          or exists (
            select 1 from public.projects p
            where p.id = s.project_id and p.user_id = auth.uid()
          )
        )
    )
  )
  with check (
    exists (
      select 1 from public.chat_sessions s
      where s.id = chat_messages.session_id
        and (
          s.user_id = auth.uid()
          or exists (
            select 1 from public.projects p
            where p.id = s.project_id and p.user_id = auth.uid()
          )
        )
    )
  );

-- ── project_documents (via the parent project's ownership) ────────────────
alter table public.project_documents enable row level security;

drop policy if exists project_documents_owner on public.project_documents;
create policy project_documents_owner on public.project_documents
  for all to authenticated
  using (
    exists (
      select 1 from public.projects p
      where p.id = project_documents.project_id and p.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from public.projects p
      where p.id = project_documents.project_id and p.user_id = auth.uid()
    )
  );
