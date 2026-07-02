import type { createClient } from '@/lib/supabase/server';

type ServerSupabaseClient = Awaited<ReturnType<typeof createClient>>;

export type ChatSessionRow = {
  id: string;
  user_id: string | null;
  project_id: string | null;
  initial_message: string | null;
  created_at: string;
};

/**
 * Возвращает сессию чата, только если она принадлежит пользователю —
 * напрямую (chat_sessions.user_id) или через проект (projects.user_id).
 */
export async function getAuthorizedChatSession(
  supabase: ServerSupabaseClient,
  sessionId: string,
  userId: string,
): Promise<ChatSessionRow | null> {
  const { data: session, error } = await supabase
    .from('chat_sessions')
    .select('id, user_id, project_id, initial_message, created_at')
    .eq('id', sessionId)
    .maybeSingle();

  if (error) {
    console.error('[chat-access] Session lookup error:', error);
    return null;
  }
  if (!session) {
    return null;
  }

  if (session.user_id === userId) {
    return session as ChatSessionRow;
  }

  if (session.project_id) {
    const { data: project, error: projectError } = await supabase
      .from('projects')
      .select('id')
      .eq('id', session.project_id)
      .eq('user_id', userId)
      .maybeSingle();

    if (projectError) {
      console.error('[chat-access] Project lookup error:', projectError);
      return null;
    }
    if (project) {
      return session as ChatSessionRow;
    }
  }

  return null;
}

/**
 * true, только если проект принадлежит пользователю (projects.user_id).
 * Используется API-роутами документов/загрузки для авторизации доступа к проекту.
 */
export async function isProjectOwnedBy(
  supabase: ServerSupabaseClient,
  projectId: string,
  userId: string,
): Promise<boolean> {
  const { data, error } = await supabase
    .from('projects')
    .select('id')
    .eq('id', projectId)
    .eq('user_id', userId)
    .maybeSingle();

  if (error) {
    console.error('[chat-access] Project ownership lookup error:', error);
    return false;
  }
  return !!data;
}
