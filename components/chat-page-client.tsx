"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { v4 as uuidv4 } from "uuid";
import { CaseSelectionScreen } from "@/components/case-selection-screen";
import { CaseWorkspace } from "@/components/case-workspace";
import type { ChatMessage, Project, SessionDocument, SelectedModel } from "@/lib/types";
import type { AnonymizationProgress, AnonymizationMapData } from "@/lib/anonymizer/types";
import { useToast } from "@/hooks/use-toast";
import { useExportMessage } from "@/hooks/use-export-message";
import { useAuth } from "@/hooks/use-auth";
import { ToasterClient } from "@/components/toaster-client";
import { fetchWithRetry, safeJsonResponse, resolveApiUrl } from "@/lib/utils";

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
  return {
    id: uuidv4(),
    title: "Новый чат",
    messages: [],
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
  return value === "text" || value === "pdf" || value === "docx" || value === "vision" || value === "llm-file";
}

function mergeMapData(a: AnonymizationMapData, b: AnonymizationMapData): AnonymizationMapData {
  const existingOriginals = new Set(a.entries.map((e) => e.original));
  const merged = [...a.entries];
  const counters = { ...a.counters };

  for (const entry of b.entries) {
    if (!existingOriginals.has(entry.original)) {
      counters[entry.piiType] = (counters[entry.piiType] ?? 0) + 1;
      merged.push(entry);
    }
  }

  return { entries: merged, counters };
}

function deanonymizeText(text: string, mapData: AnonymizationMapData): string {
  let result = text;
  for (const entry of mapData.entries) {
    result = result.split(entry.placeholder).join(entry.original);
  }
  return result;
}

export function ChatPageClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  
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
  const [isDocumentsLoading, setIsDocumentsLoading] = useState<boolean>(false);
  const [sessions, setSessions] = useState<LocalChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isThinking, setIsThinking] = useState(false); // Для reasoning модели
  const [hasInitialized, setHasInitialized] = useState(false);
  const [isUploadingDocument, setIsUploadingDocument] = useState(false);
  const [isLoadingChatsFromDB, setIsLoadingChatsFromDB] = useState(false);
  const [selectedModel, setSelectedModel] = useState<SelectedModel>('openai'); // Выбранная модель (по умолчанию openai)
  const [anonymousMode, setAnonymousMode] = useState(false);
  const [anonymizationMaps, setAnonymizationMaps] = useState<Map<string, AnonymizationMapData>>(new Map());
  const [anonymizationProgress, setAnonymizationProgress] = useState<AnonymizationProgress | null>(null);
  const { toast } = useToast();
  const { exportMessage } = useExportMessage();

  // Redirect to auth if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/auth");
    }
  }, [authLoading, user, router]);

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
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(sessions));
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

        // Load messages for each session
        const sessionsWithMessages = await Promise.all(
          dbChats.map(async (chat: any) => {
            try {
              const messagesResponse = await fetchWithRetry(`/api/chat/${chat.id}/messages`);
              if (!messagesResponse.ok) {
                console.warn(`Failed to load messages for session ${chat.id}`);
                return null;
              }

              const messagesData = await messagesResponse.json();
              const messages = Array.isArray(messagesData?.messages) 
                ? messagesData.messages.map((msg: any) => ({
                    role: msg.role,
                    content: msg.content,
                  }))
                : [];

              const localSession: LocalChatSession = {
                id: chat.id,
                title: generateTitle(chat.initial_message || 'Новый чат'),
                messages,
                backendSessionId: chat.id,
                createdAt: chat.created_at,
                documents: [],
                projectId: chat.project_id,
              };

              return localSession;
            } catch (error) {
              console.error(`Error loading messages for session ${chat.id}:`, error);
              return null;
            }
          })
        );

        if (isCancelled) return;

        const validSessions = sessionsWithMessages.filter((s): s is LocalChatSession => s !== null);

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
    if (!sessionsForProject.length) {
      setActiveSessionId(null);
      return;
    }

    if (!activeSessionId || !sessionsForProject.some((session) => session.id === activeSessionId)) {
      setActiveSessionId(sessionsForProject[0].id);
    }
  }, [activeSessionId, selectedProjectId, sessions]);

  useEffect(() => {
    if (!selectedProjectId) return;
    let isCancelled = false;

    setProjects((prev) =>
      prev.map((project) => {
        if (project.id !== selectedProjectId) {
          return project;
        }
        return {
          ...project,
          documents: [],
        };
      }),
    );

    const loadDocuments = async () => {
      setIsDocumentsLoading(true);
      try {
        const response = await fetchWithRetry(`/api/projects/${selectedProjectId}/documents`);
        if (response.ok) {
          const data = await response.json();
          const docs: SessionDocument[] = Array.isArray(data?.documents)
            ? (data.documents
                .map((doc: any) => normalizeDocument(doc))
                .filter((doc: any): doc is SessionDocument => Boolean(doc)) as SessionDocument[])
                .sort((a, b) => new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime())
            : [];

          if (!isCancelled) {
            setProjects((prev) =>
              prev.map((project) => {
                if (project.id !== selectedProjectId) {
                  return project;
                }
                return {
                  ...project,
                  documents: docs,
                };
              }),
            );
          }
        } else {
          console.error("Не удалось получить документы проекта:", await response.text());
        }
      } catch (error) {
        console.error("Ошибка при загрузке документов проекта:", error);
      } finally {
        if (!isCancelled) {
          setIsDocumentsLoading(false);
        }
      }
    };

    void loadDocuments();

    return () => {
      isCancelled = true;
    };
  }, [selectedProjectId]);

  const activeProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
  const projectDocuments = useMemo(
    () => activeProject?.documents ?? [],
    [activeProject],
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


  const handleNewChat = useCallback(() => {
    if (!selectedProjectId) return;
    const newSession = createEmptySession(selectedProjectId);
    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(newSession.id);
    setInput("");
  }, [selectedProjectId]);

  const handleSelectSession = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId);
    setInput("");
  }, []);

  const handleSelectProject = useCallback(
    (projectId: string) => {
      setSelectedProjectId(projectId);
      setIsInWorkspace(true);
    },
    [],
  );

  const handleBackToSelection = useCallback(() => {
    setIsInWorkspace(false);
    setSelectedProjectId(null);
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
          description: `Папка «${project.name}» готова. Добавляйте документы и создавайте чаты.`,
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
      if (!selectedProjectId || !activeSession || !fileList || fileList.length === 0) {
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

      const sessionLocalId = activeSession.id;
      setIsUploadingDocument(true);

      const files = Array.from(fileList);
      for (const file of files) {
        try {
          let fileToUpload: File = file;
          let wasAnonymized = false;

          const isImage = file.type.startsWith("image/") ||
            /\.(png|jpe?g|gif|webp|bmp|heic)$/i.test(file.name);

          if (anonymousMode && isImage) {
            setAnonymizationProgress({ stage: 'loading', progress: 0, message: 'Начинаем анонимизацию...' });
            try {
              const { anonymizeDocument } = await import("@/lib/anonymizer/document-anonymizer");
              const result = await anonymizeDocument(file, setAnonymizationProgress);

              if (result.anonymousText.trim()) {
                const anonBlob = new Blob([result.anonymousText], { type: "text/plain" });
                const anonFileName = file.name.replace(/\.[^.]+$/, "_anon.txt");
                fileToUpload = new File([anonBlob], anonFileName, { type: "text/plain" });

                setAnonymizationMaps((prev) => {
                  const next = new Map(prev);
                  const existing = next.get(selectedProjectId);
                  if (existing) {
                    const merged = mergeMapData(existing, result.map);
                    next.set(selectedProjectId, merged);
                  } else {
                    next.set(selectedProjectId, result.map);
                  }
                  return next;
                });

                wasAnonymized = true;
              }
            } catch (anonError) {
              console.error("Ошибка анонимизации, загружаем без анонимизации:", anonError);
              toast({
                variant: "destructive",
                title: "Ошибка анонимизации",
                description: "Документ будет загружен без анонимизации.",
              });
            } finally {
              setAnonymizationProgress(null);
            }
          }

          const formData = new FormData();
          formData.append("file", fileToUpload);
          formData.append("userId", user.id);

          const response = await fetchWithRetry(`/api/projects/${selectedProjectId}/documents`, {
            method: "POST",
            body: formData,
          });

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

          const statusText = wasAnonymized
            ? `Документ «${file.name}» анонимизирован и добавлен в контекст проекта.`
            : `Документ «${normalized.name}» добавлен в контекст этого проекта.`;

          const contextMessage: ChatMessage = {
            role: "assistant",
            content: statusText,
          };

          setSessions((prev) =>
            prev.map((session) => {
              if (session.id !== sessionLocalId) {
                return session;
              }
              return {
                ...session,
                messages: [...session.messages, contextMessage],
              };
            }),
          );

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
            title: wasAnonymized ? "Документ анонимизирован" : "Документ добавлен",
            description: wasAnonymized
              ? `«${file.name}» анонимизирован. Персональные данные защищены.`
              : `Текст из «${normalized.name}» будет использоваться при ответах.`,
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
    [activeSession, anonymousMode, selectedProjectId, setSessions, setProjects, toast, user?.id],
  );

  const handleDocumentInputChange = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      await processDocumentFiles(event.target.files);
      if (event.target) {
        event.target.value = "";
      }
    },
    [processDocumentFiles],
  );

  const handleRemoveDocument = useCallback(
    async (documentId: string) => {
      if (!selectedProjectId) {
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

      const removedDocument = projectDocuments.find((doc) => doc.id === documentId);

      setProjects((prev) => {
        const next = prev.map((project) => {
          if (project.id !== selectedProjectId) {
            return project;
          }
          return {
            ...project,
            documents: project.documents.filter((document) => document.id !== documentId),
            updated_at: new Date().toISOString(),
          };
        });

        return next.sort(
          (a, b) =>
            new Date(b.updated_at ?? b.created_at).getTime() - new Date(a.updated_at ?? a.created_at).getTime(),
        );
      });

      try {
        const response = await fetchWithRetry(`/api/projects/${selectedProjectId}/documents/${documentId}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ userId: user.id }),
        });

        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          const message =
            typeof payload?.error === "string" && payload.error.trim()
              ? payload.error
              : "Не удалось удалить документ. Попробуйте снова.";
          throw new Error(message);
        }

        toast({
          title: "Документ удалён",
          description: "Этот документ больше не будет использоваться в ответах.",
        });
      } catch (error) {
        console.error("Ошибка при удалении документа:", error);
        if (removedDocument) {
          setProjects((prev) => {
            const next = prev.map((project) => {
              if (project.id !== selectedProjectId) {
                return project;
              }
              const restored = [...project.documents, removedDocument].sort(
                (a, b) => new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime(),
              );
              return {
                ...project,
                documents: restored,
                updated_at: new Date().toISOString(),
              };
            });

            return next.sort(
              (a, b) =>
                new Date(b.updated_at ?? b.created_at).getTime() -
                new Date(a.updated_at ?? a.created_at).getTime(),
            );
          });
        }
        toast({
          variant: "destructive",
          title: "Не удалось удалить документ",
          description:
            error instanceof Error ? error.message : "Попробуйте другой файл или повторите попытку позже.",
        });
      }
    },
    [projectDocuments, selectedProjectId, setProjects, toast, user?.id],
  );

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

  const handleSendMessage = useCallback(async () => {
    if (!activeSession || isLoading) return;
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
    const trimmedMessage = input.trim();
    if (!trimmedMessage) return;

    const sessionLocalId = activeSession.id;
    const backendSessionId = activeSession.backendSessionId;
    const hasUserMessages = activeSession.messages.some((message) => message.role === "user");
    const isFirstUserMessage = !hasUserMessages;
    const userMessage: ChatMessage = {
      role: "user",
      content: trimmedMessage,
    };
    const messagesForRequest = [...activeSession.messages, userMessage];
    const documentsForRequest = activeSession.documents.map((document) => ({
      id: document.id,
      name: document.name,
      text: document.text,
    }));

    setInput("");
    setIsLoading(true);
    
    // Reasoning модель включена на постоянку - всегда показываем thinking indicator
    setIsThinking(true);

    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== sessionLocalId) return session;
        return {
          ...session,
          messages: messagesForRequest,
          title: isFirstUserMessage ? generateTitle(trimmedMessage) : session.title,
        };
      }),
    );

    try {
      // Используем увеличенный таймаут (35 минут) для долгих thinking-запросов
      // Сервер настроен на 30 минут, добавляем запас
      // Теперь используем streaming для получения ответа с heartbeat
      const resolvedUrl = resolveApiUrl(`/api/chat${utmQuery}`);
      const response = await fetch(resolvedUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: messagesForRequest,
          sessionId: backendSessionId,
          documents: documentsForRequest,
          projectId: selectedProjectId,
          userId: user.id,
          selectedModel, // Передаем выбранную модель для OpenRouter
        }),
        signal: AbortSignal.timeout(2100000), // 35 минут таймаут
      });

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
          // Игнорируем heartbeat сообщения (строки начинающиеся с ':')
          if (line.trim() === '' || line.startsWith(':')) {
            continue;
          }
          
          if (line.startsWith('data: ')) {
            try {
              data = JSON.parse(line.slice(6));
              
              // Если это ошибка
              if (data.error) {
                throw new Error(data.details || data.error);
              }
              
              // Если это финальный ответ (содержит message)
              if (data.message) {
                // Логируем метаданные AI ответа
                if (data.metadata) {
                  console.log('[AI Response]', {
                    model: data.metadata.modelUsed,
                    fallback: data.metadata.fallbackOccurred,
                    chunks: data.metadata.chunksCount,
                    tokens: data.metadata.totalTokens,
                    time: `${data.metadata.responseTimeMs}ms`
                  });
                  
                  // Показываем уведомление если было несколько chunks
                  if (data.metadata.chunksCount > 1) {
                    console.info(`✨ Ответ был сгенерирован в ${data.metadata.chunksCount} частей для обеспечения полноты`);
                  }
                  
                  // Показываем уведомление если был fallback
                  if (data.metadata.fallbackOccurred) {
                    console.warn(`⚠️ Была использована резервная модель из-за: ${data.metadata.fallbackReason}`);
                  }
                }
                
                // Определяем была ли использована reasoning модель и время размышления
                const wasReasoning = data.metadata?.modelUsed === 'reasoning' || 
                  (data.metadata?.responseTimeMs && data.metadata.responseTimeMs > 5000);
                const thinkingTimeSeconds = data.metadata?.responseTimeMs 
                  ? Math.floor(data.metadata.responseTimeMs / 1000) 
                  : undefined;
                
                let messageContent: string = data.message;
                const projectMapData = selectedProjectId
                  ? anonymizationMaps.get(selectedProjectId)
                  : undefined;
                if (projectMapData && projectMapData.entries.length > 0) {
                  messageContent = deanonymizeText(messageContent, projectMapData);
                }

                const assistantMessage: ChatMessage = {
                  role: "assistant",
                  content: messageContent,
                  metadata: {
                    modelUsed: data.metadata?.modelUsed,
                    thinkingTimeSeconds,
                    wasReasoning,
                  },
                };

                setIsThinking(false);

                setSessions((prev) =>
                  prev.map((session) => {
                    if (session.id !== sessionLocalId) return session;
                    return {
                      ...session,
                      backendSessionId: data.sessionId ?? session.backendSessionId,
                      messages: [...session.messages, assistantMessage],
                      projectId: data.projectId ?? session.projectId ?? selectedProjectId,
                    };
                  }),
                );

                setProjects((prev) =>
                  prev
                    .map((project) =>
                      project.id === (data.projectId ?? selectedProjectId)
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
            } catch (parseError) {
              console.error('Error parsing SSE data:', parseError);
              // Продолжаем обработку, возможно следующая строка будет валидной
            }
          }
        }
      }

      if (!data || !data.message) {
        throw new Error("Не удалось получить ответ от сервера");
      }
    } catch (error) {
      console.error("Ошибка при отправке сообщения:", error);
      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== sessionLocalId) return session;
          return {
            ...session,
            messages: [
              ...session.messages,
              {
                role: "assistant",
                content: "Извините, произошла ошибка при обработке запроса. Попробуйте ещё раз.",
              },
            ],
          };
        }),
      );
    } finally {
      setIsLoading(false);
      setIsThinking(false);
    }
  }, [activeSession, anonymizationMaps, input, isLoading, selectedProjectId, selectedModel, toast, user?.id, utmQuery]);

  // Show loading while checking auth
  if (authLoading) {
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
      />
      <ToasterClient />
    </>
  );
  }

  const currentProject = projects.find((p) => p.id === selectedProjectId);
  if (!currentProject) {
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
        isUploadingDocument={isUploadingDocument}
        isDocumentsLoading={isDocumentsLoading}
        isLoadingChats={isLoadingChatsFromDB}
        selectedModel={selectedModel}
        anonymousMode={anonymousMode}
        anonymizationProgress={anonymizationProgress}
        onModelChange={setSelectedModel}
        onAnonymousModeChange={setAnonymousMode}
        onBack={handleBackToSelection}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        onInputChange={setInput}
        onSendMessage={handleSendMessage}
        onAttachDocument={processDocumentFiles}
        onRemoveDocument={handleRemoveDocument}
        onExportMessage={handleExportMessage}
      />
      <ToasterClient />
    </>
  );
}