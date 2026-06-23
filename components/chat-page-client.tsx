"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { v4 as uuidv4 } from "uuid";
import { CaseSelectionScreen } from "@/components/case-selection-screen";
import { CaseWorkspace } from "@/components/case-workspace";
import type { ChatMessage, ChatMessageDocument, Project, SessionDocument, SelectedModel } from "@/lib/types";
import { useToast } from "@/hooks/use-toast";
import { useExportMessage } from "@/hooks/use-export-message";
import { useAuth } from "@/hooks/use-auth";
import { ToasterClient } from "@/components/toaster-client";
import { fetchWithRetry, safeJsonResponse, resolveApiUrl } from "@/lib/utils";
import { setCurrentChatId } from "@/lib/client-error-logger";

type LocalChatSession = {
  id: string;
  title: string;
  messages: ChatMessage[];
  backendSessionId?: string;
  createdAt: string;
  documents: SessionDocument[];
  projectId: string;
};

type ProjectState = Project & {
  documents: SessionDocument[];
};

const LOCAL_STORAGE_KEY = "legal-assistant-chat-sessions-v2";
const LEGACY_LOCAL_STORAGE_KEY = "legal-assistant-chat-sessions";
const ACTIVE_PROJECT_STORAGE_KEY = "legal-assistant-active-project-id";
const DEFAULT_PROJECT_NAME = "Мои дела";

function createEmptySession(projectId: string): LocalChatSession {
  const now = new Date().toISOString();
  const chatId = uuidv4();
  return {
    id: chatId,
    title: "Новый чат",
    messages: [],
    backendSessionId: chatId,
    documents: [],
    createdAt: now,
    projectId,
  };
}

function generateTitle(message: string) {
  if (!message) return "Новый чат";
  const trimmed = message.trim().replace(/\s+/g, " ");
  if (trimmed.length <= 40) {
    return trimmed;
  }
  return `${trimmed.slice(0, 40)}…`;
}

function normalizeDocument(raw: unknown): SessionDocument | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const value = raw as Partial<SessionDocument> & Record<string, unknown>;
  const text = typeof value.text === "string" ? value.text : "";
  if (!text) {
    return null;
  }
  const strategy = isValidDocumentStrategy(value.strategy) ? value.strategy : "text";
  return {
    id: typeof value.id === "string" && value.id.trim() ? value.id : uuidv4(),
    name: typeof value.name === "string" && value.name.trim() ? value.name : "Документ",
    mimeType: typeof value.mimeType === "string" && value.mimeType.trim() ? value.mimeType : "application/octet-stream",
    size: typeof value.size === "number" && value.size >= 0 ? value.size : text.length,
    text,
    truncated: Boolean(value.truncated),
    rawTextLength: typeof value.rawTextLength === "number" && value.rawTextLength > 0 ? value.rawTextLength : text.length,
    strategy,
    uploadedAt:
      typeof value.uploadedAt === "string" && value.uploadedAt.trim()
        ? value.uploadedAt
        : new Date().toISOString(),
  };
}

function isValidDocumentStrategy(value: unknown): value is SessionDocument["strategy"] {
  return value === "text" || value === "pdf" || value === "docx" || value === "doc" || value === "vision" || value === "llm-file";
}

function toMessageDocument(document: SessionDocument): ChatMessageDocument {
  return {
    id: document.id,
    name: document.name,
    mimeType: document.mimeType,
    size: document.size,
  };
}

function normalizeDbMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((msg: any) => ({
    role: msg.role,
    content: msg.content,
    attachedDocumentIds: Array.isArray(msg.attachedDocumentIds)
      ? msg.attachedDocumentIds
      : Array.isArray(msg.attached_document_ids)
        ? msg.attached_document_ids
        : undefined,
    attachedDocuments: Array.isArray(msg.attachedDocuments)
      ? msg.attachedDocuments
      : Array.isArray(msg.attached_documents)
        ? msg.attached_documents
        : undefined,
  }));
}

export function ChatPageClient({ initialChatId }: { initialChatId?: string } = {}) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, loading: authLoading, signOut } = useAuth();
  
  const utmQuery = useMemo(() => {
    const params = new URLSearchParams();
    searchParams.forEach((value, key) => {
      if (key.startsWith("utm_")) {
        params.set(key, value);
      }
    });
    const query = params.toString();
    return query ? `?${query}` : "";
  }, [searchParams]);
  const [projects, setProjects] = useState<ProjectState[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [isInWorkspace, setIsInWorkspace] = useState<boolean>(false);
  const [isProjectsLoading, setIsProjectsLoading] = useState<boolean>(true);
  const [sessions, setSessions] = useState<LocalChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  // Per-session in-flight streaming so concurrent chats don't clobber each
  // other: switching/creating a chat never aborts another chat's request, and
  // every chat keeps its own live draft + tool status. isLoading/isThinking
  // below are derived for the *active* session only.
  const [streamStates, setStreamStates] = useState<
    Record<
      string,
      { phase: "thinking" | "streaming"; draft: string; toolLabel: string | null; startedAt: number }
    >
  >({});
  const inflightSessionsRef = useRef<Set<string>>(new Set());
  const activeStream = activeSessionId ? streamStates[activeSessionId] : undefined;
  const isLoading = Boolean(activeStream);
  const isThinking = activeStream?.phase === "thinking";
  const [hasInitialized, setHasInitialized] = useState(false);
  const [isUploadingDocument, setIsUploadingDocument] = useState(false);
  const [pendingMessageDocuments, setPendingMessageDocuments] = useState<SessionDocument[]>([]);
  const [isLoadingChatsFromDB, setIsLoadingChatsFromDB] = useState(false);
  // Чат, открытый по прямой ссылке /chat/[chatId]: ждём, пока он появится в списке сессий
  const pendingInitialChatIdRef = useRef<string | null>(initialChatId ?? null);
  const [isResolvingInitialChat, setIsResolvingInitialChat] = useState<boolean>(Boolean(initialChatId));
  // Сессии, для которых сообщения уже загружены из БД в этом сеансе работы
  const loadedMessageSessionsRef = useRef<Set<string>>(new Set());
  const [loadingMessagesSessionId, setLoadingMessagesSessionId] = useState<string | null>(null);
  // Проект, для которого список чатов уже загружен из БД (нужно, чтобы не сбрасывать deep-link раньше времени)
  const dbChatsLoadedProjectRef = useRef<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<SelectedModel>('openai'); // Выбранная модель (по умолчанию openai)
  const [isPageVisible, setIsPageVisible] = useState(true);
  const [pendingRequest, setPendingRequest] = useState<{
    sessionLocalId: string;
    messagesForRequest: ChatMessage[];
    backendSessionId?: string;
    isFirstUserMessage: boolean;
    trimmedMessage: string;
  } | null>(null);
  const { toast } = useToast();
  const { exportMessage } = useExportMessage();

  // Отслеживание видимости страницы для восстановления соединений
  useEffect(() => {
    const handleVisibilityChange = () => {
      const visible = !document.hidden;
      setIsPageVisible(visible);
      
      if (visible && pendingRequest) {
        console.log('[Background Recovery] Страница стала видимой, запрос продолжается в фоне');
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [pendingRequest]);

  // Redirect to auth if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/auth");
    }
  }, [authLoading, user, router]);

  // Resolve deep link /chat/[chatId]: find the chat's project and open the workspace on it
  useEffect(() => {
    if (!initialChatId || !user?.id) return;
    let isCancelled = false;

    const resolveChat = async () => {
      try {
        const response = await fetchWithRetry(`/api/chat/${encodeURIComponent(initialChatId)}`);
        if (isCancelled) return;
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        const projectId = typeof data?.chat?.project_id === "string" ? data.chat.project_id : null;
        if (!projectId) {
          throw new Error("Chat has no project");
        }
        if (isCancelled || pendingInitialChatIdRef.current !== initialChatId) return;
        setSelectedProjectId(projectId);
        setIsInWorkspace(true);
        setActiveSessionId(initialChatId);
      } catch (error) {
        console.error("Не удалось открыть чат по ссылке:", error);
        if (isCancelled) return;
        pendingInitialChatIdRef.current = null;
        window.history.replaceState(null, "", "/workspace");
        toast({
          variant: "destructive",
          title: "Чат не найден",
          description: "Чат по этой ссылке не существует или у вас нет к нему доступа.",
        });
      } finally {
        if (!isCancelled) {
          setIsResolvingInitialChat(false);
        }
      }
    };

    void resolveChat();

    return () => {
      isCancelled = true;
    };
  }, [initialChatId, toast, user?.id]);

  useEffect(() => {
    if (!user?.id) return;
    let isCancelled = false;

    const loadProjects = async () => {
      setIsProjectsLoading(true);
      try {
        const response = await fetchWithRetry(`/api/projects?userId=${encodeURIComponent(user.id)}`);
        let projectsPayload: Project[] = [];

        if (response.ok) {
          try {
            const data = await safeJsonResponse<{ projects?: Project[] }>(response);
            projectsPayload = Array.isArray(data?.projects) ? data.projects : [];
          } catch (error) {
            console.warn("Ошибка при чтении ответа проектов, пробуем повторить:", error);
            // Повторяем запрос один раз при ошибке чтения
            const retryResponse = await fetchWithRetry(`/api/projects?userId=${encodeURIComponent(user.id)}`);
            if (retryResponse.ok) {
              const data = await safeJsonResponse<{ projects?: Project[] }>(retryResponse);
              projectsPayload = Array.isArray(data?.projects) ? data.projects : [];
            }
          }
        }

        if (!projectsPayload.length) {
          const createResponse = await fetchWithRetry(`/api/projects`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: DEFAULT_PROJECT_NAME, userId: user.id }),
          });

          if (createResponse.ok) {
            const created = await createResponse.json();
            if (created?.project) {
              projectsPayload = [created.project as Project];
            }
          }
        }

        if (!isCancelled) {
          const enriched = projectsPayload
            .map<ProjectState>((project) => ({
              ...project,
              documents: [],
            }))
            .sort(
              (a, b) =>
                new Date(b.updated_at ?? b.created_at).getTime() -
                new Date(a.updated_at ?? a.created_at).getTime(),
            );
          setProjects(enriched);
        }
      } catch (error) {
        console.error("Не удалось загрузить проекты:", error);
        if (!isCancelled) {
          toast({
            variant: "destructive",
            title: "Ошибка загрузки проектов",
            description: "Попробуйте обновить страницу или повторить попытку позже.",
          });
        }
      } finally {
        if (!isCancelled) {
          setIsProjectsLoading(false);
        }
      }
    };

    void loadProjects();

    return () => {
      isCancelled = true;
    };
  }, [toast, user?.id]);

  // Initial load from localStorage (as cache)
  // Note: Database will be the source of truth when project is selected
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      let storedSessions = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (!storedSessions) {
        storedSessions = localStorage.getItem(LEGACY_LOCAL_STORAGE_KEY);
      }
      if (storedSessions) {
        const parsedRaw: LocalChatSession[] = JSON.parse(storedSessions);
        const parsed = parsedRaw.map<LocalChatSession>((session) => {
          const storedDocuments = Array.isArray((session as any).documents)
            ? ((session as any).documents as unknown[])
            : [];

          const normalizedDocuments = storedDocuments
            .map((document: any) => normalizeDocument(document))
            .filter((document: any): document is SessionDocument => Boolean(document));

          return {
            ...session,
            title: session.title?.trim() ? session.title : "Новый чат",
            messages: Array.isArray(session.messages) ? session.messages : [],
            documents: normalizedDocuments,
            createdAt: session.createdAt ?? new Date().toISOString(),
            projectId: session.projectId ?? "",
          };
        });
        // Load from localStorage as initial cache
        // Database will override when project is selected
        setSessions(parsed);
        setHasInitialized(true);
        console.log('[Cache] Loaded', parsed.length, 'sessions from localStorage');
        return;
      }
    } catch (error) {
      console.error("Не удалось загрузить чаты из хранилища:", error);
    }

    setHasInitialized(true);
  }, []);

  // Save sessions to localStorage as cache
  // Database is the source of truth, localStorage is for performance
  useEffect(() => {
    if (!hasInitialized || typeof window === "undefined") return;
    try {
      // Strip failed (uncommitted) messages: they are ephemeral retry artifacts
      // that the backend never persisted, so they must not survive a refresh.
      const sanitized = sessions.map((session) => ({
        ...session,
        messages: session.messages.filter((message) => message.status !== "failed"),
      }));
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(sanitized));
      console.log('[Cache] Saved', sessions.length, 'sessions to localStorage');
    } catch (error) {
      console.error("Не удалось сохранить чаты в localStorage:", error);
    }
  }, [sessions, hasInitialized]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (selectedProjectId) {
      try {
        localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, selectedProjectId);
      } catch (error) {
        console.error("Не удалось сохранить активный проект:", error);
      }
    }
  }, [selectedProjectId]);

  useEffect(() => {
    if (!hasInitialized) return;
    if (!projects.length) return;

    const fallbackProjectId = selectedProjectId ?? projects[0]?.id;
    if (!fallbackProjectId) return;

    setSessions((prev) => {
      let hasChanges = false;
      const next = prev.map((session) => {
        if (!session.projectId) {
          hasChanges = true;
          return { ...session, projectId: fallbackProjectId };
        }
        return session;
      });
      return hasChanges ? next : prev;
    });
  }, [hasInitialized, projects, selectedProjectId]);

  // Load chats from database when project is selected
  useEffect(() => {
    if (!selectedProjectId || !user?.id) return;
    let isCancelled = false;

    const loadChatsFromDatabase = async () => {
      setIsLoadingChatsFromDB(true);
      try {
        // Fetch chat sessions from database
        const response = await fetchWithRetry(`/api/projects/${selectedProjectId}/chats?userId=${user.id}`);
        if (!response.ok) {
          throw new Error('Failed to load chats from database');
        }

        const data = await response.json();
        const dbChats = Array.isArray(data?.chats) ? data.chats : [];

        if (isCancelled) return;

        // Messages are loaded lazily for the active chat only (see effect below)
        const validSessions: LocalChatSession[] = dbChats.map(
          (chat: any): LocalChatSession => ({
            id: chat.id,
            title: generateTitle(chat.initial_message || 'Новый чат'),
            messages: [],
            backendSessionId: chat.id,
            createdAt: chat.created_at,
            documents: [],
            projectId: chat.project_id,
          }),
        );

        // Merge database sessions with localStorage sessions
        setSessions((prev) => {
          // Create a map of existing sessions by backend session ID
          const existingByBackendId = new Map(
            prev
              .filter((s) => s.backendSessionId)
              .map((s) => [s.backendSessionId!, s])
          );

          // Update or add database sessions
          const merged = [...prev];
          validSessions.forEach((dbSession) => {
            const existingIndex = merged.findIndex(
              (s) => s.backendSessionId === dbSession.id || s.id === dbSession.id
            );

            if (existingIndex >= 0) {
              // Update existing session with database data (database is source of truth)
              merged[existingIndex] = {
                ...merged[existingIndex],
                ...dbSession,
                // Keep cached messages: dbSession arrives without them (lazy loading)
                messages: merged[existingIndex].messages,
                // Keep local documents if they exist
                documents: merged[existingIndex].documents.length > 0
                  ? merged[existingIndex].documents
                  : dbSession.documents,
              };
            } else {
              // Add new session from database
              merged.push(dbSession);
            }
          });

          // Filter to only include sessions for this project
          return merged.filter((s) => s.projectId === selectedProjectId);
        });

        // If no sessions exist, create an empty one
        setSessions((prev) => {
          const projectSessions = prev.filter((s) => s.projectId === selectedProjectId);
          if (projectSessions.length === 0) {
            const session = createEmptySession(selectedProjectId);
            setActiveSessionId(session.id);
            return [session, ...prev];
          }
          return prev;
        });

      } catch (error) {
        console.error('Error loading chats from database:', error);
        
        // Fallback: create empty session if none exist
    setSessions((prev) => {
      if (prev.some((session) => session.projectId === selectedProjectId)) {
        return prev;
      }
      const session = createEmptySession(selectedProjectId);
          setActiveSessionId(session.id);
      return [session, ...prev];
    });
      } finally {
        if (!isCancelled) {
          dbChatsLoadedProjectRef.current = selectedProjectId;
          setIsLoadingChatsFromDB(false);
        }
      }
    };

    void loadChatsFromDatabase();

    return () => {
      isCancelled = true;
    };
  }, [selectedProjectId, user?.id]);

  useEffect(() => {
    if (!selectedProjectId) return;

    const sessionsForProject = sessions.filter((session) => session.projectId === selectedProjectId);

    const pendingChatId = pendingInitialChatIdRef.current;
    if (pendingChatId) {
      const pendingSession = sessionsForProject.find(
        (session) => session.id === pendingChatId || session.backendSessionId === pendingChatId,
      );
      if (pendingSession) {
        pendingInitialChatIdRef.current = null;
        if (activeSessionId !== pendingSession.id) {
          setActiveSessionId(pendingSession.id);
        }
        return;
      }
      // Чат из ссылки ещё не появился в списке — ждём окончания загрузки чатов из БД
      if (dbChatsLoadedProjectRef.current !== selectedProjectId) {
        return;
      }
      pendingInitialChatIdRef.current = null;
    }

    if (!sessionsForProject.length) {
      setActiveSessionId(null);
      return;
    }

    if (!activeSessionId || !sessionsForProject.some((session) => session.id === activeSessionId)) {
      setActiveSessionId(sessionsForProject[0].id);
    }
  }, [activeSessionId, isLoadingChatsFromDB, selectedProjectId, sessions]);

  // Lazy-load messages for the active chat only
  useEffect(() => {
    if (!activeSessionId || !user?.id) return;
    if (loadedMessageSessionsRef.current.has(activeSessionId)) return;
    if (!sessions.some((session) => session.id === activeSessionId)) return;

    const sessionId = activeSessionId;
    let isCancelled = false;

    const loadMessages = async () => {
      setLoadingMessagesSessionId(sessionId);
      try {
        const response = await fetchWithRetry(`/api/chat/${encodeURIComponent(sessionId)}/messages`);
        if (isCancelled) return;
        if (!response.ok) {
          if (response.status === 404) {
            // Новый чат, которого ещё нет на сервере — загружать нечего
            loadedMessageSessionsRef.current.add(sessionId);
          } else {
            console.warn(`Failed to load messages for session ${sessionId} (HTTP ${response.status})`);
          }
          return;
        }

        const data = await response.json();
        const fetchedMessages = normalizeDbMessages(data?.messages);
        if (isCancelled) return;

        loadedMessageSessionsRef.current.add(sessionId);
        setSessions((prev) =>
          prev.map((session) => {
            if (session.id !== sessionId && session.backendSessionId !== sessionId) return session;
            // Не затираем локальные сообщения (например, оптимистично добавленные перед ответом)
            if (fetchedMessages.length <= session.messages.length) return session;
            return { ...session, messages: fetchedMessages };
          }),
        );
      } catch (error) {
        console.error(`Не удалось загрузить сообщения чата ${sessionId}:`, error);
      } finally {
        if (!isCancelled) {
          setLoadingMessagesSessionId((current) => (current === sessionId ? null : current));
        }
      }
    };

    void loadMessages();

    return () => {
      isCancelled = true;
    };
  }, [activeSessionId, sessions, user?.id]);

  // Keep the URL in sync with the active chat so links can be shared and survive reloads
  useEffect(() => {
    if (typeof window === "undefined" || authLoading || !user) return;
    if (isResolvingInitialChat || pendingInitialChatIdRef.current) return;

    const targetPath = isInWorkspace && activeSessionId ? `/chat/${activeSessionId}` : "/workspace";
    if (window.location.pathname !== targetPath) {
      window.history.replaceState(null, "", `${targetPath}${window.location.search}`);
    }
  }, [activeSessionId, authLoading, isInWorkspace, isResolvingInitialChat, user]);

  const activeProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
  const sessionsForActiveProject = useMemo(
    () =>
      sessions
        .filter((session) => !selectedProjectId || session.projectId === selectedProjectId)
        .slice()
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()),
    [selectedProjectId, sessions],
  );
  const activeSession = useMemo(
    () =>
      sessions.find(
        (session) =>
          session.id === activeSessionId && (!selectedProjectId || session.projectId === selectedProjectId),
      ) ?? null,
    [activeSessionId, selectedProjectId, sessions],
  );

  // Tag uncaught browser errors with the active chat id so client-side errors
  // correlate with backend/BFF logs (see lib/client-error-logger.ts).
  useEffect(() => {
    setCurrentChatId(activeSession?.backendSessionId ?? activeSessionId);
  }, [activeSession, activeSessionId]);


  const isLoadingMessages =
    Boolean(activeSessionId) && loadingMessagesSessionId === activeSessionId;

  const handleNewChat = useCallback(() => {
    if (!selectedProjectId) return;
    const newSession = createEmptySession(selectedProjectId);
    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newSession.id);
    setInput("");
    setPendingMessageDocuments([]);
  }, [selectedProjectId]);

  const handleSelectSession = useCallback((sessionId: string) => {
    pendingInitialChatIdRef.current = null;
    setActiveSessionId(sessionId);
    setInput("");
    setPendingMessageDocuments([]);
  }, []);

  const handleSelectProject = useCallback(
    (projectId: string) => {
      pendingInitialChatIdRef.current = null;
      setSelectedProjectId(projectId);
      setIsInWorkspace(true);
    },
    [],
  );

  const handleBackToSelection = useCallback(() => {
    pendingInitialChatIdRef.current = null;
    setIsInWorkspace(false);
    setSelectedProjectId(null);
    setPendingMessageDocuments([]);
  }, []);

  const handleCreateProject = useCallback(async (name: string) => {
    if (!user?.id) {
      toast({
        variant: "destructive",
        title: "Не удалось определить пользователя",
        description: "Обновите страницу и попробуйте снова.",
      });
      return;
    }

    const trimmedName = name.trim();
    if (!trimmedName) {
      return;
    }

    try {
      const response = await fetchWithRetry("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmedName, userId: user.id }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const message =
          typeof payload?.error === "string" && payload.error.trim()
            ? payload.error
            : "Не удалось создать проект. Попробуйте снова.";
        throw new Error(message);
      }

      const data = await response.json();
      if (data?.project) {
        const project: ProjectState = {
          ...(data.project as Project),
          documents: [],
        };
        setProjects((prev) =>
          [project, ...prev].sort(
            (a, b) =>
              new Date(b.updated_at ?? b.created_at).getTime() -
              new Date(a.updated_at ?? a.created_at).getTime(),
          ),
        );
        setSelectedProjectId(project.id);
        setIsInWorkspace(true);
        toast({
          title: "Проект создан",
          description: `Папка «${project.name}» готова. Создавайте чаты и прикрепляйте документы к сообщениям.`,
        });
      }
    } catch (error) {
      console.error("Не удалось создать проект:", error);
      toast({
        variant: "destructive",
        title: "Не удалось создать проект",
        description: error instanceof Error ? error.message : "Попробуйте снова чуть позже.",
      });
    }
  }, [setProjects, toast, user?.id]);

  const handleRenameProject = useCallback(async (projectId: string, newName: string) => {
    if (!user?.id) {
      toast({
        variant: "destructive",
        title: "Не удалось определить пользователя",
        description: "Обновите страницу и попробуйте снова.",
      });
      return;
    }

    const trimmedName = newName.trim();
    if (!trimmedName) {
      return;
    }

    try {
      const response = await fetchWithRetry(`/api/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmedName, userId: user.id }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const message =
          typeof payload?.error === "string" && payload.error.trim()
            ? payload.error
            : "Не удалось переименовать проект. Попробуйте снова.";
        throw new Error(message);
      }

      const data = await response.json();
      if (data?.project) {
        setProjects((prev) =>
          prev.map((p) =>
            p.id === projectId
              ? { ...p, name: data.project.name, updated_at: data.project.updated_at }
              : p
          ).sort(
            (a, b) =>
              new Date(b.updated_at ?? b.created_at).getTime() -
              new Date(a.updated_at ?? a.created_at).getTime(),
          )
        );
        toast({
          title: "Проект переименован",
          description: `Название изменено на «${data.project.name}»`,
        });
      }
    } catch (error) {
      console.error("Не удалось переименовать проект:", error);
      toast({
        variant: "destructive",
        title: "Не удалось переименовать проект",
        description: error instanceof Error ? error.message : "Попробуйте снова чуть позже.",
      });
    }
  }, [setProjects, toast, user?.id]);

  const handleDeleteProject = useCallback(async (projectId: string) => {
    if (!user?.id) {
      toast({
        variant: "destructive",
        title: "Не удалось определить пользователя",
        description: "Обновите страницу и попробуйте снова.",
      });
      return;
    }

    try {
      const response = await fetchWithRetry(`/api/projects/${projectId}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: user.id }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const message =
          typeof payload?.error === "string" && payload.error.trim()
            ? payload.error
            : "Не удалось удалить проект. Попробуйте снова.";
        throw new Error(message);
      }

      setProjects((prev) => prev.filter((p) => p.id !== projectId));
      setSessions((prev) => prev.filter((s) => s.projectId !== projectId));
      
      // If we're currently viewing the deleted project, go back to selection
      if (selectedProjectId === projectId) {
        setIsInWorkspace(false);
        setSelectedProjectId(null);
      }
      
      toast({
        title: "Проект удалён",
        description: "Проект и все его данные были успешно удалены.",
      });
    } catch (error) {
      console.error("Не удалось удалить проект:", error);
      toast({
        variant: "destructive",
        title: "Не удалось удалить проект",
        description: error instanceof Error ? error.message : "Попробуйте снова чуть позже.",
      });
    }
  }, [setProjects, setSessions, selectedProjectId, toast, user?.id]);

  const processDocumentFiles = useCallback(
    async (fileList: FileList | null) => {
      if (!selectedProjectId || !fileList || fileList.length === 0) {
        return;
      }
      if (!user?.id) {
        toast({
          variant: "destructive",
          title: "Не удалось определить пользователя",
          description: "Обновите страницу и попробуйте снова.",
        });
        return;
      }

      setIsUploadingDocument(true);

      const files = Array.from(fileList);
      // Stable chat id (client-generated, exists before the chat row) so the
      // backend can tag the extraction trace with chat_id for LangSmith.
      const chatId = activeSession?.backendSessionId ?? activeSessionId;
      for (const file of files) {
        try {
          // Step 1: Get presigned URL
          const presignResponse = await fetchWithRetry("/api/upload/presign", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              mimeType: file.type || "application/octet-stream",
              size: file.size,
              projectId: selectedProjectId,
              userId: user.id,
            }),
          });

          if (!presignResponse.ok) {
            const errorPayload = await presignResponse.json().catch(() => ({}));
            const message =
              typeof errorPayload?.error === "string" && errorPayload.error.trim()
                ? errorPayload.error
                : "Не удалось получить ссылку для загрузки.";
            throw new Error(message);
          }

          const { uploadUrl, objectKey } = await presignResponse.json();

          // Step 2: Upload file directly to S3
          const uploadResponse = await fetch(uploadUrl, {
            method: "PUT",
            headers: { "Content-Type": file.type || "application/octet-stream" },
            body: file,
          });

          if (!uploadResponse.ok) {
            throw new Error(`Ошибка загрузки файла в хранилище (${uploadResponse.status}).`);
          }

          // Step 3: Notify backend to process the uploaded file.
          // Распознавание многостраничных сканов (постранично через gemini) легко
          // выходит за прежние 3 минуты, из-за чего загрузка постоянно падала по
          // таймауту. Поднимаем клиентский таймаут до предела серверного контейнера
          // (execution-timeout 1800s = 30 минут), чтобы сервер успевал вернуть
          // нормальный ответ/ошибку, а не браузер абортил запрос раньше времени.
          // TODO: уйти от таймаута на фронте вовсе — перевести извлечение в
          // асинхронную модель (job + polling/SSE прогресса).
          // Ретраи сводим к минимуму: повтор перезапускает тяжёлую обработку
          // (скачивание из S3 + OCR) целиком — лучше показать ошибку, чем дублировать.
          const response = await fetchWithRetry(
            `/api/projects/${selectedProjectId}/documents`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                objectKey,
                filename: file.name,
                mimeType: file.type || "application/octet-stream",
                size: file.size,
                userId: user.id,
                // Stable chat id in the BODY (not a custom header): a cross-origin
                // proxy (NEXT_PUBLIC_PROXY_URL) would drop a custom header at the
                // CORS preflight. The Next route forwards it to the backend as
                // X-Chat-Id, exactly like the chat path does from the URL.
                chatId,
              }),
            },
            1, // maxRetries
            1000, // retryDelay
            1800000, // timeoutMs — 30 минут (предел контейнера execution-timeout 1800s)
          );

          if (!response.ok) {
            const errorPayload = await response.json().catch(() => ({}));
            const message =
              typeof errorPayload?.error === "string" && errorPayload.error.trim()
                ? errorPayload.error
                : "Не удалось обработать документ. Попробуйте другой файл.";
            throw new Error(message);
          }

          const data = await response.json();
          const normalized = normalizeDocument(data?.document);
          if (!normalized) {
            throw new Error("Ответ сервера не содержит текст документа.");
          }

          setPendingMessageDocuments((prev) => {
            const withoutDuplicate = prev.filter((document) => document.id !== normalized.id);
            return [...withoutDuplicate, normalized];
          });

          setProjects((prev) => {
            const next = prev.map((project) => {
              if (project.id !== selectedProjectId) {
                return project;
              }
              const existing = project.documents.some((doc) => doc.id === normalized.id);
              const updatedDocuments = existing
                ? project.documents.map((doc) => (doc.id === normalized.id ? normalized : doc))
                : [...project.documents, normalized];
              const sortedDocuments = updatedDocuments
                .slice()
                .sort((a, b) => new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime());
              return {
                ...project,
                documents: sortedDocuments,
                updated_at: new Date().toISOString(),
              };
            });

            return next.sort(
              (a, b) =>
                new Date(b.updated_at ?? b.created_at).getTime() -
                new Date(a.updated_at ?? a.created_at).getTime(),
            );
          });

          toast({
            title: "Документ прикреплён",
            description: `«${normalized.name}» будет отправлен вместе со следующим сообщением.`,
          });
        } catch (error) {
          console.error("Ошибка при обработке документа:", error);
          toast({
            variant: "destructive",
            title: "Не удалось обработать документ",
            description:
              error instanceof Error ? error.message : "Попробуйте другой файл или повторите попытку позже.",
          });
        }
      }

      setIsUploadingDocument(false);
    },
    [selectedProjectId, setProjects, toast, user?.id, activeSession?.backendSessionId, activeSessionId],
  );

  const handleRemovePendingDocument = useCallback((documentId: string) => {
    setPendingMessageDocuments((prev) => prev.filter((document) => document.id !== documentId));
  }, []);

  const handleExportMessage = useCallback(
    async (messageIndex: number) => {
      if (!activeSession || !activeProject) return;

      const message = activeSession.messages[messageIndex];
      if (!message || message.role !== "assistant") {
        toast({
          variant: "destructive",
          title: "Ошибка экспорта",
          description: "Можно экспортировать только ответы помощника.",
        });
        return;
      }

      const result = await exportMessage({
        projectName: activeProject.name,
        sessionTitle: activeSession.title,
        aiResponse: message.content,
        timestamp: new Date(),
      });

      if (result.success) {
        toast({
          title: "Документ создан",
          description: "Ответ успешно экспортирован в формате DOCX.",
        });
      } else {
        toast({
          variant: "destructive",
          title: "Ошибка экспорта",
          description: result.error || "Не удалось создать документ.",
        });
      }
    },
    [activeSession, activeProject, exportMessage, toast]
  );

  const handleSendMessage = useCallback(async (override?: {
    content: string;
    attachedDocumentIds: string[];
    attachedDocuments: ChatMessage["attachedDocuments"];
    baseMessages: ChatMessage[];
  }) => {
    if (!activeSession || isLoadingMessages) return;
    if (!selectedProjectId) {
      toast({
        variant: "destructive",
        title: "Проект не выбран",
        description: "Выберите проект или создайте новый, прежде чем отправлять сообщения.",
      });
      return;
    }
    if (!user?.id) {
      toast({
        variant: "destructive",
        title: "Не удалось определить пользователя",
        description: "Обновите страницу и попробуйте снова.",
      });
      return;
    }
    const trimmedMessage = override ? override.content : input.trim();
    const attachedDocuments = override
      ? override.attachedDocuments ?? []
      : pendingMessageDocuments.map(toMessageDocument);
    const attachedDocumentIds = override
      ? override.attachedDocumentIds
      : attachedDocuments.map((document) => document.id);
    if (!override && !trimmedMessage && attachedDocumentIds.length === 0) return;

    const sessionLocalId = activeSession.id;
    const chatId = activeSession.backendSessionId ?? activeSession.id;
    // Don't double-send the SAME chat, but never block a different chat.
    if (inflightSessionsRef.current.has(sessionLocalId)) return;
    // После отправки локальная история становится актуальной — лениво перезагружать её не нужно
    loadedMessageSessionsRef.current.add(sessionLocalId);
    loadedMessageSessionsRef.current.add(chatId);

    // Committed history only: a previously failed (uncommitted) turn is dropped
    // when the user sends/retries (variant A). For a retry we rebuild from the
    // history that preceded the failed message (override.baseMessages).
    const committed = (override ? override.baseMessages : activeSession.messages).filter(
      (message) => message.status !== "failed",
    );
    const isFirstUserMessage = !committed.some((message) => message.role === "user");
    const userMessage: ChatMessage = {
      role: "user",
      content: trimmedMessage,
      ...(attachedDocumentIds.length > 0
        ? { attachedDocumentIds, attachedDocuments }
        : {}),
    };
    const messagesForRequest = [...committed, userMessage];

    if (!override) {
      setInput("");
      setPendingMessageDocuments([]);
    }
    inflightSessionsRef.current.add(sessionLocalId);
    setStreamStates((prev) => ({
      ...prev,
      [sessionLocalId]: { phase: "thinking", draft: "", toolLabel: null, startedAt: Date.now() },
    }));

    // Сохраняем информацию о запросе для возможного восстановления
    setPendingRequest({
      sessionLocalId,
      messagesForRequest,
      backendSessionId: chatId,
      isFirstUserMessage,
      trimmedMessage: userMessage.content,
    });

    if (!activeSession.backendSessionId) {
      setSessions((prev) =>
        prev.map((session) =>
          session.id === sessionLocalId ? { ...session, backendSessionId: chatId } : session,
        ),
      );
    }

    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== sessionLocalId) return session;
        return {
          ...session,
          messages: messagesForRequest,
          title: isFirstUserMessage ? generateTitle(userMessage.content) : session.title,
        };
      }),
    );

    try {
      // Используем увеличенный таймаут (35 минут) для долгих thinking-запросов
      // Сервер настроен на 30 минут, добавляем запас
      // Теперь используем streaming для получения ответа с heartbeat
      const resolvedUrl = resolveApiUrl(`/api/chat/${encodeURIComponent(chatId)}/messages${utmQuery}`);

      // Если страница станет невидимой, не отменяем запрос сразу,
      // но отслеживаем это состояние
      const visibilityHandler = () => {
        if (document.hidden) {
          console.log('[Background Mode] Страница ушла в фон, но запрос продолжается');
          // Не отменяем запрос, современные браузеры должны его поддерживать
        }
      };
      
      document.addEventListener('visibilitychange', visibilityHandler);
      
      const response = await fetch(resolvedUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: messagesForRequest,
          projectId: selectedProjectId,
          selectedModel,
        }),
        signal: AbortSignal.timeout(2100000), // 35 минут таймаут
      });

      document.removeEventListener('visibilitychange', visibilityHandler);

      if (!response.ok) {
        throw new Error("Не удалось отправить сообщение");
      }

      // Читаем streaming ответ (Server-Sent Events)
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      if (!reader) {
        throw new Error("Не удалось получить поток данных");
      }

      let data: any = null;

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        
        // Обрабатываем SSE сообщения
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Оставляем неполную строку в буфере
        
        for (const line of lines) {
          // Heartbeat comments (": …") keep the connection warm — skip them.
          if (line.trim() === '' || line.startsWith(':')) {
            continue;
          }
          if (!line.startsWith('data: ')) {
            continue;
          }

          let event: any;
          try {
            event = JSON.parse(line.slice(6));
          } catch (parseError) {
            console.error('Error parsing SSE data:', parseError);
            continue;
          }

          // Error event (new {type:"error"} or legacy {error}).
          if (event.type === 'error' || event.error) {
            throw new Error(event.details || event.error || 'Ошибка генерации');
          }

          // Token delta — grow this session's live draft.
          // SERVERLESS NOTE: the backend emits these per-token, but Yandex
          // Serverless buffers the whole response, so in prod they all arrive in
          // one burst at the end (draft jumps to full, then commits) instead of
          // typing out. On a normal server / VM this animates live, no changes.
          if (event.type === 'token' && typeof event.delta === 'string') {
            const delta = event.delta;
            setStreamStates((prev) => {
              const current = prev[sessionLocalId];
              return {
                ...prev,
                [sessionLocalId]: {
                  phase: 'streaming',
                  draft: (current?.draft ?? '') + delta,
                  toolLabel: null,
                  startedAt: current?.startedAt ?? Date.now(),
                },
              };
            });
            continue;
          }

          // A tool started — surface its status. The pre-tool preamble is
          // ephemeral, so reset the draft and show the status line instead.
          // SERVERLESS NOTE: like tokens, these statuses are buffered by Yandex
          // Serverless and only land at the end, so "Ищу практику…" doesn't show
          // live in prod (that's why we discussed polling). Live on a VM.
          if (event.type === 'status') {
            const label = event.label || 'Работаю с источниками…';
            setStreamStates((prev) => ({
              ...prev,
              [sessionLocalId]: {
                phase: 'thinking',
                draft: '',
                toolLabel: label,
                startedAt: prev[sessionLocalId]?.startedAt ?? Date.now(),
              },
            }));
            continue;
          }

          // Final answer (new {type:"final"} or legacy {message}).
          if (event.type === 'final' || event.message) {
            data = event;

            if (event.metadata) {
              console.log('[AI Response]', {
                model: event.metadata.modelUsed,
                fallback: event.metadata.fallbackOccurred,
                tokens: event.metadata.totalTokens,
                time: `${event.metadata.responseTimeMs}ms`,
              });
            }

            const wasReasoning = event.metadata?.modelUsed === 'reasoning' ||
              (event.metadata?.responseTimeMs && event.metadata.responseTimeMs > 5000);
            const thinkingTimeSeconds = event.metadata?.responseTimeMs
              ? Math.floor(event.metadata.responseTimeMs / 1000)
              : undefined;

            const assistantMessage: ChatMessage = {
              role: "assistant",
              content: event.message ?? '',
              metadata: {
                modelUsed: event.metadata?.modelUsed,
                thinkingTimeSeconds,
                wasReasoning,
              },
            };

            // Commit the answer and drop this session's live draft in the same
            // render so the bubble swaps cleanly (no duplicate-text frame).
            setSessions((prev) =>
              prev.map((session) => {
                if (session.id !== sessionLocalId) return session;
                return {
                  ...session,
                  backendSessionId: event.sessionId ?? session.backendSessionId ?? chatId,
                  messages: [...session.messages, assistantMessage],
                  projectId: event.projectId ?? session.projectId ?? selectedProjectId,
                };
              }),
            );
            setStreamStates((prev) => {
              if (!(sessionLocalId in prev)) return prev;
              const next = { ...prev };
              delete next[sessionLocalId];
              return next;
            });

            setProjects((prev) =>
              prev
                .map((project) =>
                  project.id === (event.projectId ?? selectedProjectId)
                    ? { ...project, updated_at: new Date().toISOString() }
                    : project,
                )
                .sort(
                  (a, b) =>
                    new Date(b.updated_at ?? b.created_at).getTime() -
                    new Date(a.updated_at ?? a.created_at).getTime(),
                ),
            );
          }
        }
      }

      if (!data || !data.message) {
        throw new Error("Не удалось получить ответ от сервера");
      }
      
      // Запрос успешно завершен, очищаем pending request
      setPendingRequest(null);
    } catch (error) {
      console.error("Ошибка при отправке сообщения:", error);
      
      // Проверяем, была ли ошибка связана с фоновым режимом
      const isBackgroundError = error instanceof Error && 
        (error.name === 'AbortError' || error.message.includes('fetch'));
      
      if (isBackgroundError && !isPageVisible) {
        console.log('[Background Error] Запрос прерван в фоновом режиме, сохраняем для повтора');
      } else {
        // Очищаем pending request при обычной ошибке
        setPendingRequest(null);
      }
      
      // Mark the in-flight user message (the last one) as failed instead of
      // appending a bot error reply. It stays retryable and is dropped from
      // history/localStorage, so the on-screen history never diverges from what
      // the backend persisted — a failed turn is persisted nowhere.
      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== sessionLocalId) return session;
          const messages = [...session.messages];
          const lastIndex = messages.length - 1;
          if (lastIndex >= 0 && messages[lastIndex].role === "user") {
            messages[lastIndex] = { ...messages[lastIndex], status: "failed" };
          }
          return { ...session, messages };
        }),
      );
    } finally {
      inflightSessionsRef.current.delete(sessionLocalId);
      setStreamStates((prev) => {
        if (!(sessionLocalId in prev)) return prev;
        const next = { ...prev };
        delete next[sessionLocalId];
        return next;
      });
    }
  }, [activeSession, input, isLoadingMessages, isPageVisible, pendingMessageDocuments, selectedProjectId, selectedModel, toast, user?.id, utmQuery]);

  // Retry a failed user turn: re-send its content/attachments with the history
  // that preceded it. The failed message is always the last one, but we slice
  // by index defensively.
  const handleRetryMessage = useCallback(
    (messageIndex: number) => {
      if (!activeSession) return;
      const target = activeSession.messages[messageIndex];
      if (!target || target.role !== "user") return;
      void handleSendMessage({
        content: target.content,
        attachedDocumentIds: target.attachedDocumentIds ?? [],
        attachedDocuments: target.attachedDocuments ?? [],
        baseMessages: activeSession.messages.slice(0, messageIndex),
      });
    },
    [activeSession, handleSendMessage],
  );

  // Show loading while checking auth or resolving a /chat/[chatId] deep link
  if (authLoading || (user && isResolvingInitialChat)) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-current border-r-transparent align-[-0.125em] motion-reduce:animate-[spin_1.5s_linear_infinite]" />
          <p className="mt-4 text-muted-foreground">Загрузка...</p>
        </div>
      </div>
    );
  }

  // Redirect to auth is handled in useEffect
  if (!user) {
    return null;
  }

  // Render appropriate screen based on navigation state
  if (!isInWorkspace || !selectedProjectId) {
  return (
    <>
      <CaseSelectionScreen
        projects={projects}
        sessions={sessions}
        isLoading={isProjectsLoading}
        onSelectProject={handleSelectProject}
        onCreateProject={handleCreateProject}
        onRenameProject={handleRenameProject}
        onDeleteProject={handleDeleteProject}
        onSignOut={signOut}
      />
      <ToasterClient />
    </>
  );
  }

  const currentProject = projects.find((p) => p.id === selectedProjectId);
  if (!currentProject) {
    if (isProjectsLoading) {
      return (
        <div className="flex min-h-screen items-center justify-center">
          <div className="text-center">
            <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-current border-r-transparent align-[-0.125em] motion-reduce:animate-[spin_1.5s_linear_infinite]" />
            <p className="mt-4 text-muted-foreground">Загрузка...</p>
          </div>
        </div>
      );
    }
    return null;
  }

  const currentProjectSessions = sessions.filter((s) => s.projectId === selectedProjectId);

  return (
    <>
      <CaseWorkspace
        project={currentProject}
        sessions={currentProjectSessions}
        activeSessionId={activeSessionId}
        input={input}
        isLoading={isLoading}
        isThinking={isThinking}
        streamingDraft={activeStream?.draft ?? ""}
        toolStatus={activeStream?.toolLabel ?? null}
        thinkingStartedAt={activeStream?.startedAt ?? null}
        isUploadingDocument={isUploadingDocument}
        isLoadingChats={isLoadingChatsFromDB}
        isLoadingMessages={isLoadingMessages}
        pendingDocuments={pendingMessageDocuments}
        selectedModel={selectedModel}
        onModelChange={setSelectedModel}
        onBack={handleBackToSelection}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        onInputChange={setInput}
        onSendMessage={handleSendMessage}
        onAttachDocument={processDocumentFiles}
        onRemovePendingDocument={handleRemovePendingDocument}
        onExportMessage={handleExportMessage}
        onRetryMessage={handleRetryMessage}
        onSignOut={signOut}
      />
      <ToasterClient />
    </>
  );
}