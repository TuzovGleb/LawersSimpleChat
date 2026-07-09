"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
// Render single newlines as <br>, like LangSmith / most chat UIs. Without this,
// CommonMark folds a soft line break into a space, so label lines the model
// emits one per line ("Суд:\nДата:\nСтороны:") collapse into one paragraph.
import remarkBreaks from "remark-breaks";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThinkingIndicator } from "@/components/thinking-indicator";
import { DocumentPreviewPanel } from "@/components/document-preview-panel";
import { SubscriptionBanner } from "@/components/subscription-banner";
import { useAppHeight } from "@/hooks/use-app-height";
import { cn } from "@/lib/utils";
import type { Entitlement } from "@/lib/entitlement";
import type { ChatMessage, Project, SessionDocument, SelectedModel, UploadingDocument } from "@/lib/types";
import {
  AlertCircle,
  ArrowLeft,
  Eye,
  FileText,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Paperclip,
  Plus,
  RotateCcw,
  Send,
  X,
} from "lucide-react";

// Max height of the auto-growing composer textarea before it starts scrolling.
const COMPOSER_MAX_HEIGHT = 160;

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

interface CaseWorkspaceProps {
  project: ProjectState;
  sessions: LocalChatSession[];
  activeSessionId: string | null;
  input: string;
  isLoading: boolean;
  isThinking: boolean;
  streamingDraft?: string;
  toolStatus?: string | null;
  thinkingStartedAt?: number | null;
  isUploadingDocument: boolean;
  isLoadingChats: boolean;
  isLoadingMessages: boolean;
  pendingDocuments: SessionDocument[];
  uploadingDocuments: UploadingDocument[];
  selectedModel: SelectedModel;
  entitlement: Entitlement | null;
  // Read-only режим (доступ истёк): композер и загрузка/создание отключены,
  // история и скачивание готовых документов работают как раньше.
  accessExpired: boolean;
  onRedeemed: (entitlement: Entitlement) => void;
  onModelChange: (model: SelectedModel) => void;
  onBack: () => void;
  onSelectSession: (sessionId: string) => void;
  onNewChat: () => void;
  onInputChange: (value: string) => void;
  onSendMessage: () => void;
  onAttachDocument: (files: FileList | null) => void;
  onRemovePendingDocument: (documentId: string) => void;
  onRemoveUploadingDocument: (localId: string) => void;
  onRetryMessage?: (messageIndex: number) => void;
  onSignOut: () => void;
}

// The remove button paints outside the chip's layout box (negative margins) so
// its touch target is ~34px on phones and ~26px with a pointer, without
// inflating the chip itself.
const CHIP_REMOVE_CLASS =
  "-my-2.5 -mr-2 shrink-0 rounded-full p-2.5 md:-my-1.5 md:-mr-1.5 md:p-1.5";

// One document-chip family across every surface: the composer, attachments
// inside a sent user message, and .docx artifacts under assistant replies.
// Geometry, type scale and the state language are shared — solid border =
// attached, dashed + spinner = uploading, tinted background + visible error
// text = failed (red alone can't mean "failed" here: red is the brand accent).
// The fill sits one step lighter than the chip's surface: a soft tint on the
// white composer, a white "sheet" on the gray bubble and the paper page.
// Interactive chips (onActivate) render as a real button with hover + trailing
// affordance; inert record chips are plain spans. onActivate and onRemove are
// mutually exclusive — combining them would nest a button inside a button.
// Uploading chips reserve the remove button's width so the row doesn't shift
// when the X appears.
function DocumentChip({
  variant = "attached",
  name,
  error,
  fill = "soft",
  muted = false,
  trailing,
  onActivate,
  title,
  onRemove,
}: {
  variant?: "attached" | "uploading" | "error";
  name: string;
  error?: string;
  fill?: "soft" | "white";
  muted?: boolean;
  trailing?: ReactNode;
  onActivate?: () => void;
  title?: string;
  onRemove?: () => void;
}) {
  const isError = variant === "error";
  const errorText = error ?? "Не удалось обработать документ";
  const chipClass = cn(
    "inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-2 text-[13px] md:py-1.5",
    isError
      ? "bg-[hsl(var(--destructive)/0.06)]"
      : fill === "white"
        ? "bg-white"
        : "bg-[var(--bg-soft)]",
    onActivate && "transition-colors hover:bg-[var(--bg-soft)]",
  );
  const chipStyle = {
    border: `1px ${variant === "uploading" ? "dashed" : "solid"} ${
      isError ? "hsl(var(--destructive))" : "var(--border-strong)"
    }`,
    color:
      variant === "uploading" || muted ? "var(--text-secondary)" : "var(--text-primary)",
  };
  const content = (
    <>
      {variant === "uploading" ? (
        <Loader2
          className="h-3.5 w-3.5 shrink-0 animate-spin"
          style={{ color: "var(--brand-accent)" }}
        />
      ) : isError ? (
        <AlertCircle
          className="h-3.5 w-3.5 shrink-0"
          style={{ color: "hsl(var(--destructive))" }}
        />
      ) : (
        <FileText
          className="h-3.5 w-3.5 shrink-0"
          style={{ color: "var(--brand-accent)" }}
        />
      )}
      <span
        className="max-w-[240px] truncate"
        title={onActivate ? undefined : title ?? name}
      >
        {name}
      </span>
      {variant === "uploading" && <span className="sr-only">Загружается…</span>}
      {isError && (
        // The reason must be visible, not tooltip-only: touch devices have no
        // hover and the toast is transient.
        <span
          className="max-w-[220px] truncate"
          style={{ color: "hsl(var(--destructive))" }}
          title={errorText}
        >
          {errorText}
        </span>
      )}
      {trailing}
      {onRemove ? (
        <button
          type="button"
          className={cn(CHIP_REMOVE_CLASS, "hover:opacity-70")}
          onClick={onRemove}
          aria-label={isError ? `Убрать ${name} (не загружен)` : `Убрать ${name}`}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      ) : variant === "uploading" ? (
        <span aria-hidden="true" className={cn(CHIP_REMOVE_CLASS, "invisible")}>
          <X className="h-3.5 w-3.5" />
        </span>
      ) : null}
    </>
  );
  if (onActivate) {
    return (
      <button type="button" onClick={onActivate} title={title} className={chipClass} style={chipStyle}>
        {content}
      </button>
    );
  }
  return (
    <span
      role={variant === "uploading" ? "status" : undefined}
      className={chipClass}
      style={chipStyle}
    >
      {content}
    </span>
  );
}

export function CaseWorkspace({
  project,
  sessions,
  activeSessionId,
  input,
  isLoading,
  isThinking,
  streamingDraft = "",
  toolStatus = null,
  thinkingStartedAt = null,
  isUploadingDocument,
  isLoadingChats,
  isLoadingMessages,
  pendingDocuments,
  uploadingDocuments,
  selectedModel,
  entitlement,
  accessExpired,
  onRedeemed,
  onModelChange,
  onBack,
  onSelectSession,
  onNewChat,
  onInputChange,
  onSendMessage,
  onAttachDocument,
  onRemovePendingDocument,
  onRemoveUploadingDocument,
  onRetryMessage,
  onSignOut,
}: CaseWorkspaceProps) {
  // Keep the shell sized to the visible viewport (iOS keyboard handling).
  useAppHeight();

  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  // Drafted document currently open in the right-side preview panel.
  const [preview, setPreview] = useState<{ id: string; fileName: string } | null>(null);
  const dragCounterRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  // Persisted chat id used to route artifact downloads (matches the messages API).
  const chatId = activeSession?.backendSessionId ?? activeSession?.id ?? "";

  const sortedSessions = useMemo(
    () =>
      [...sessions].sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      ),
    [sessions],
  );

  // Stick-to-bottom autoscroll. "Pinned" flips only on real user scrolling:
  // scrolling UP unpins (so streaming doesn't yank the reader back on every
  // token), returning to the bottom re-pins. Content growth alone never
  // unpins — a tall message appended while pinned still follows.
  const isPinnedRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  useEffect(() => {
    const viewport = messagesEndRef.current?.closest(
      "[data-radix-scroll-area-viewport]",
    );
    if (!viewport) {
      return;
    }
    const handleScroll = () => {
      const distanceFromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      if (viewport.scrollTop < lastScrollTopRef.current - 1) {
        isPinnedRef.current = false;
      } else if (distanceFromBottom < 120) {
        isPinnedRef.current = true;
      }
      lastScrollTopRef.current = viewport.scrollTop;
    };
    viewport.addEventListener("scroll", handleScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", handleScroll);
  }, []);

  // Jump to the bottom when a chat's content first shows up, then follow new
  // messages while pinned. The session-switch jump must not be consumed by
  // the lazy-loading spinner render: messages arrive asynchronously after
  // activeSessionId changes, so the switch counts only once content exists.
  const lastAutoscrollSessionRef = useRef<string | null>(null);
  useEffect(() => {
    const end = messagesEndRef.current;
    if (!end) {
      return;
    }
    const hasContent =
      (activeSession?.messages.length ?? 0) > 0 || Boolean(streamingDraft);
    const sessionChanged = lastAutoscrollSessionRef.current !== activeSessionId;
    if (sessionChanged) {
      if (!hasContent) {
        return; // history still lazy-loading — keep the jump for its arrival
      }
      lastAutoscrollSessionRef.current = activeSessionId;
      isPinnedRef.current = true;
      end.scrollIntoView({ behavior: "auto" });
      return;
    }
    if (isPinnedRef.current) {
      end.scrollIntoView({ behavior: "smooth" });
    }
  }, [activeSessionId, activeSession?.messages, streamingDraft, toolStatus]);

  // Grow the composer textarea to fit its content, up to a max height (then scroll).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) {
      return;
    }
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
    el.style.overflowY = el.scrollHeight > COMPOSER_MAX_HEIGHT ? "auto" : "hidden";
  }, [input]);

  const resetDragState = useCallback(() => {
    dragCounterRef.current = 0;
    setIsDragging(false);
  }, []);

  useEffect(() => {
    if (!isDragging) {
      return;
    }

    const handleWindowKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        resetDragState();
      }
    };

    const handleWindowDragEnd = () => {
      resetDragState();
    };

    const handleWindowDrop = () => {
      resetDragState();
    };

    window.addEventListener("keydown", handleWindowKeyDown);
    window.addEventListener("dragend", handleWindowDragEnd);
    window.addEventListener("drop", handleWindowDrop);

    return () => {
      window.removeEventListener("keydown", handleWindowKeyDown);
      window.removeEventListener("dragend", handleWindowDragEnd);
      window.removeEventListener("drop", handleWindowDrop);
    };
  }, [isDragging, resetDragState]);

  const handleSubmit = useCallback(
    (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      onSendMessage();
    },
    [onSendMessage],
  );

  const handleInputKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        onSendMessage();
      }
    },
    [onSendMessage],
  );

  const handleAttachButtonClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      onAttachDocument(event.target.files);
      if (event.target) {
        event.target.value = "";
      }
    },
    [onAttachDocument],
  );

  const handlePageDragEnter = useCallback(
    (e: React.DragEvent) => {
      // Read-only режим: drag-n-drop — тот же канал загрузки, что и кнопка.
      if (accessExpired) return;
      if (e.dataTransfer.types.includes("Files")) {
        dragCounterRef.current += 1;
        setIsDragging(true);
      }
    },
    [accessExpired],
  );

  const handlePageDragLeave = useCallback(() => {
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      resetDragState();
    }
  }, [resetDragState]);

  const handlePageDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      resetDragState();
      if (accessExpired) return;
      if (e.dataTransfer.files.length > 0) {
        onAttachDocument(e.dataTransfer.files);
      }
    },
    [accessExpired, onAttachDocument, resetDragState],
  );

  return (
    <div
      className="relative flex h-[var(--app-h,100dvh)] flex-col"
      style={{ background: "var(--bg)" }}
      onDragEnter={handlePageDragEnter}
      onDragLeave={handlePageDragLeave}
      onDragOver={(e) => e.preventDefault()}
      onDrop={handlePageDrop}
    >
      {preview && chatId && (
        <DocumentPreviewPanel
          chatId={chatId}
          artifactId={preview.id}
          fileName={preview.fileName}
          onClose={() => setPreview(null)}
        />
      )}
      {isDragging && (
        <div
          className="pointer-events-none absolute inset-0 z-50 flex flex-col items-center justify-center gap-3 rounded-none border-4 border-dashed"
          style={{
            borderColor: "var(--brand-accent)",
            background: "rgba(250, 250, 247, .88)",
          }}
        >
          <Paperclip className="h-12 w-12" style={{ color: "var(--brand-accent)" }} />
          <p className="text-lg font-medium" style={{ color: "var(--brand-accent)" }}>
            Отпустите файл для прикрепления
          </p>
        </div>
      )}

      {/* Header */}
      <header
        className="flex-shrink-0 pt-[env(safe-area-inset-top)]"
        style={{
          background: "var(--bg)",
          borderBottom: "1px solid var(--border-strong)",
        }}
      >
        <div
          className="flex items-center justify-between pl-[max(1.25rem,env(safe-area-inset-left))] pr-[max(1.25rem,env(safe-area-inset-right))]"
          style={{ height: 60 }}
        >
          <div className="flex items-center gap-3 min-w-0">
            <Button
              variant="ghost"
              size="icon"
              onClick={onBack}
              title="К списку дел"
              className="h-11 w-11 md:h-10 md:w-10"
              style={{ color: "var(--text-secondary)" }}
            >
              <ArrowLeft className="h-5 w-5" />
              <span className="sr-only">Вернуться к списку дел</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(true)}
              className="h-11 w-11 md:hidden"
            >
              <Menu className="h-5 w-5" />
              <span className="sr-only">Открыть меню</span>
            </Button>
            <div className="flex flex-col min-w-0">
              <span
                style={{
                  fontFamily: "var(--font-serif-family)",
                  fontSize: 18,
                  fontWeight: 600,
                  lineHeight: 1.2,
                  color: "var(--text-primary)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {project.name}
              </span>
              <span
                className="hidden sm:block"
                style={{ fontSize: 12.5, color: "var(--text-secondary)" }}
              >
                {isLoadingChats ? (
                  <>
                    <Loader2 className="inline h-3 w-3 animate-spin" /> чатов
                  </>
                ) : (
                  `${sessions.length} ${
                    sessions.length === 1 ? "чат" : sessions.length < 5 ? "чата" : "чатов"
                  }`
                )}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <ModeToggle
              selectedModel={selectedModel}
              onChange={onModelChange}
              className="hidden sm:inline-flex"
            />
            <Button
              variant="ghost"
              size="icon"
              onClick={onSignOut}
              title="Выйти"
              className="h-11 w-11 md:h-10 md:w-10"
              style={{ color: "var(--text-secondary)" }}
            >
              <LogOut className="h-5 w-5" />
              <span className="sr-only">Выйти</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Access banner (renders nothing while access is active and not expiring).
          The wrapper carries only horizontal padding, so it collapses to zero
          height when the banner is hidden. */}
      <div className="flex-shrink-0 pl-[max(1.25rem,env(safe-area-inset-left))] pr-[max(1.25rem,env(safe-area-inset-right))]">
        <SubscriptionBanner entitlement={entitlement} onRedeemed={onRedeemed} className="mt-3" />
      </div>

      {/* Main */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-[min(280px,85vw)] shrink-0 flex-col pl-[env(safe-area-inset-left)] pt-[env(safe-area-inset-top)] transition-transform duration-300 md:static md:w-[280px] md:translate-x-0 md:pl-0 md:pt-0",
            isSidebarOpen ? "translate-x-0" : "-translate-x-full",
          )}
          style={{
            background: "var(--bg)",
            borderRight: "1px solid var(--border-strong)",
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          {/* Mobile close header */}
          <div
            className="flex items-center justify-between border-b p-4 md:hidden"
            style={{ borderBottomColor: "var(--border-strong)" }}
          >
            <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              Чаты
            </h2>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(false)}
              className="h-11 w-11 md:h-10 md:w-10"
            >
              <X className="h-5 w-5" />
              <span className="sr-only">Закрыть панель</span>
            </Button>
          </div>

          {/* Model switcher — the header hides it on phones; the drawer is its mobile home */}
          <div className="border-b p-3 sm:hidden" style={{ borderBottomColor: "var(--border-soft)" }}>
            <ModeToggle
              selectedModel={selectedModel}
              onChange={onModelChange}
              className="grid w-full grid-cols-2"
            />
          </div>

          {/* New chat button */}
          <div className="p-3" style={{ borderBottom: "1px solid var(--border-soft)" }}>
            <button
              type="button"
              onClick={onNewChat}
              disabled={accessExpired}
              title={accessExpired ? "Доступ приостановлен" : undefined}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--brand-accent)] bg-transparent px-3 py-3 text-[13.5px] font-medium text-[var(--brand-accent)] transition-colors hover:bg-[var(--brand-accent-bg)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent md:py-[9px]"
            >
              <Plus className="h-4 w-4" />
              Новый чат
            </button>
          </div>

          {/* Chat list — plain overflow div, NOT ScrollArea (avoids horizontal bleed) */}
          <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
            <div style={{ padding: 8 }}>
              {isLoadingChats ? (
                <div
                  className="rounded-lg border border-dashed px-4 py-8 text-center"
                  style={{ borderColor: "var(--border-strong)" }}
                >
                  <Loader2
                    className="mx-auto h-8 w-8 animate-spin"
                    style={{ color: "var(--text-secondary)" }}
                  />
                  <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                    Загрузка чатов...
                  </p>
                </div>
              ) : sortedSessions.length === 0 ? (
                <div
                  className="rounded-lg border border-dashed px-4 py-8 text-center"
                  style={{ borderColor: "var(--border-strong)" }}
                >
                  <MessageSquare
                    className="mx-auto h-8 w-8"
                    style={{ color: "var(--text-secondary)" }}
                  />
                  <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                    Нет чатов
                  </p>
                  <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                    Создайте первый чат
                  </p>
                </div>
              ) : (
                sortedSessions.map((session) => {
                  const isActive = session.id === activeSessionId;
                  return (
                    <button
                      key={session.id}
                      type="button"
                      onClick={() => {
                        onSelectSession(session.id);
                        setIsSidebarOpen(false);
                      }}
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 10,
                        width: "100%",
                        padding: "10px",
                        borderRadius: 8,
                        border: 0,
                        marginBottom: 2,
                        background: isActive ? "var(--brand-accent-bg)" : "transparent",
                        cursor: "pointer",
                        textAlign: "left",
                        fontFamily: "inherit",
                        transition: "background .15s",
                        boxSizing: "border-box",
                        minWidth: 0,
                        overflow: "hidden",
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) (e.currentTarget as HTMLElement).style.background = "#F1EFE7";
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) (e.currentTarget as HTMLElement).style.background = "transparent";
                      }}
                    >
                      <MessageSquare
                        style={{
                          width: 18,
                          height: 18,
                          flexShrink: 0,
                          marginTop: 2,
                          color: isActive ? "var(--brand-accent)" : "var(--text-secondary)",
                        }}
                      />
                      {/* body: min-width:0 + flex:1 — ключ для truncate */}
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div
                          style={{
                            fontSize: 13.5,
                            fontWeight: 500,
                            color: "var(--text-primary)",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {session.title || "Новый чат"}
                        </div>
                      </div>
                      {/* timestamp: flex-shrink:0 — остаётся справа */}
                      <span
                        style={{
                          fontSize: 11.5,
                          color: "var(--text-muted)",
                          flexShrink: 0,
                          marginLeft: 6,
                          marginTop: 2,
                          whiteSpace: "nowrap",
                        }}
                      >
                        {new Date(session.createdAt).toLocaleString("ru-RU", {
                          day: "numeric",
                          month: "short",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </aside>

        {/* Chat area */}
        <main className="flex flex-1 flex-col" style={{ background: "#FBFAF6", minWidth: 0 }}>
          <div className="flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="mx-auto flex w-full max-w-[860px] flex-col gap-4 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] py-6 md:pl-[max(2rem,env(safe-area-inset-left))] md:pr-[max(2rem,env(safe-area-inset-right))] md:py-8">
                {isLoadingChats && !activeSession ? (
                  <div className="mt-10 text-center" style={{ color: "var(--text-secondary)" }}>
                    <Loader2 className="mx-auto h-10 w-10 animate-spin" />
                    <h2 className="text-xl font-semibold mt-4" style={{ color: "var(--text-primary)" }}>
                      Загрузка чатов...
                    </h2>
                    <p className="mt-2 text-sm">Получаем вашу историю разговоров</p>
                  </div>
                ) : isLoadingMessages && activeSession && activeSession.messages.length === 0 ? (
                  <div className="mt-10 text-center" style={{ color: "var(--text-secondary)" }}>
                    <Loader2 className="mx-auto h-10 w-10 animate-spin" />
                    <h2 className="text-xl font-semibold mt-4" style={{ color: "var(--text-primary)" }}>
                      Загрузка сообщений...
                    </h2>
                  </div>
                ) : activeSession && activeSession.messages.length === 0 && !isLoading ? (
                  <div className="mt-10 text-center">
                    <h2
                      className="text-2xl"
                      style={{
                        color: "var(--text-primary)",
                        fontFamily: "var(--font-serif-family)",
                        fontWeight: 500,
                      }}
                    >
                      С чего начнём?
                    </h2>
                    <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                      Опишите ситуацию или загрузите документы — помогу разобраться со стратегией и рисками.
                    </p>
                  </div>
                ) : null}

                {activeSession?.messages.map((message, index) => (
                  <div
                    key={`${message.role}-${index}`}
                    className={cn(
                      "flex w-full",
                      message.role === "user" ? "justify-end" : "justify-start",
                    )}
                  >
                    {message.role === "user" ? (
                      <div className="max-w-full md:max-w-[80%]">
                        <div
                          className="rounded-2xl px-4 py-3"
                          style={{
                            background: "#F0F0EE",
                            ...(message.status === "failed"
                              ? { border: "1px solid #DC2626" }
                              : {}),
                          }}
                        >
                          {message.content.trim() ? (
                            <p
                              className="whitespace-pre-wrap break-words text-sm leading-relaxed"
                              style={{ wordBreak: "normal", overflowWrap: "break-word", color: "var(--text-primary)" }}
                            >
                              {message.content}
                            </p>
                          ) : null}
                          {Array.isArray(message.attachedDocumentIds) &&
                            message.attachedDocumentIds.length > 0 && (
                              <div className="mt-2 flex flex-wrap justify-end gap-2">
                                {message.attachedDocumentIds.map((documentId) => {
                                  const document = message.attachedDocuments?.find(
                                    (item) => item.id === documentId,
                                  );
                                  const label =
                                    document?.name ?? `Документ ${documentId.slice(0, 8)}`;
                                  return (
                                    <DocumentChip
                                      key={documentId}
                                      name={label}
                                      fill="white"
                                      muted
                                    />
                                  );
                                })}
                              </div>
                            )}
                        </div>
                        {message.status === "failed" && (
                          <div className="mt-1 flex items-center justify-end gap-2">
                            <span
                              className="inline-flex items-center gap-1 text-xs"
                              style={{ color: "#DC2626" }}
                            >
                              <AlertCircle className="h-3 w-3 shrink-0" />
                              {message.errorText || "Не удалось отправить"}
                            </span>
                            <button
                              type="button"
                              onClick={() => onRetryMessage?.(index)}
                              className="inline-flex items-center gap-1 text-xs font-medium hover:underline"
                              style={{ color: "#DC2626" }}
                            >
                              <RotateCcw className="h-3 w-3 shrink-0" />
                              Повторить
                            </button>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="group relative max-w-full md:max-w-[85%]">
                        {message.metadata?.wasReasoning && message.metadata?.thinkingTimeSeconds && (
                          <div className="mb-2">
                            <ThinkingIndicator
                              isThinking={false}
                              thinkingTime={message.metadata.thinkingTimeSeconds}
                              modelName={message.metadata.modelUsed}
                            />
                          </div>
                        )}
                        <div
                          className="prose prose-sm prose-mobile max-w-none"
                          style={{
                            background: "#fff",
                            border: "1px solid var(--border-strong)",
                            padding: "18px 22px",
                            borderRadius: 12,
                            wordBreak: "normal",
                            overflowWrap: "break-word",
                            color: "var(--text-primary)",
                          }}
                        >
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkBreaks]}
                            components={{
                              p: ({ children }) => (
                                <p
                                  className="text-sm leading-relaxed mb-3 last:mb-0"
                                  style={{ color: "var(--text-primary)" }}
                                >
                                  {children}
                                </p>
                              ),
                              h1: ({ children }) => (
                                <h1
                                  style={{
                                    fontFamily: "var(--font-serif-family)",
                                    fontSize: 22,
                                    fontWeight: 500,
                                    margin: "16px 0 8px",
                                    color: "var(--text-primary)",
                                  }}
                                >
                                  {children}
                                </h1>
                              ),
                              h2: ({ children }) => (
                                <h2
                                  style={{
                                    fontFamily: "var(--font-serif-family)",
                                    fontSize: 19,
                                    fontWeight: 500,
                                    margin: "14px 0 6px",
                                    color: "var(--text-primary)",
                                  }}
                                >
                                  {children}
                                </h2>
                              ),
                              h3: ({ children }) => (
                                <h3
                                  style={{
                                    fontFamily: "var(--font-serif-family)",
                                    fontSize: 17,
                                    fontWeight: 500,
                                    margin: "12px 0 6px",
                                    color: "var(--text-primary)",
                                  }}
                                >
                                  {children}
                                </h3>
                              ),
                              ul: ({ children }) => (
                                <ul className="list-disc list-outside mb-3 space-y-1 ml-6">{children}</ul>
                              ),
                              ol: ({ children }) => (
                                <ol className="list-decimal list-outside mb-3 space-y-1 ml-6">{children}</ol>
                              ),
                              li: ({ children }) => (
                                <li
                                  className="text-sm leading-relaxed"
                                  style={{ color: "var(--text-primary)" }}
                                >
                                  {children}
                                </li>
                              ),
                              blockquote: ({ children }) => (
                                <blockquote
                                  style={{
                                    borderLeft: "3px solid var(--brand-accent)",
                                    background: "#FBFAF5",
                                    padding: "10px 14px",
                                    margin: "12px 0",
                                    fontSize: 14.5,
                                    color: "#2A313D",
                                    borderRadius: "0 6px 6px 0",
                                  }}
                                >
                                  {children}
                                </blockquote>
                              ),
                              code: ({ className, children, ...props }) => {
                                const isInline = !className;
                                if (isInline) {
                                  return (
                                    <code
                                      className="px-1.5 py-0.5 rounded text-xs font-mono"
                                      style={{ background: "var(--bg-soft)" }}
                                      {...props}
                                    >
                                      {children}
                                    </code>
                                  );
                                }
                                return (
                                  <code
                                    className="block p-3 rounded text-xs font-mono overflow-x-auto my-2"
                                    style={{ background: "var(--bg-soft)" }}
                                    {...props}
                                  >
                                    {children}
                                  </code>
                                );
                              },
                              pre: ({ children }) => <pre className="mb-2">{children}</pre>,
                              strong: ({ children }) => <strong className="font-bold">{children}</strong>,
                              em: ({ children }) => <em className="italic">{children}</em>,
                              a: ({ children, href }) => (
                                <a
                                  href={href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{ color: "var(--brand-accent)", textDecoration: "underline" }}
                                >
                                  {children}
                                </a>
                              ),
                              hr: () => (
                                <hr
                                  style={{
                                    margin: "16px 0",
                                    border: 0,
                                    borderTop: "1px solid var(--border-soft)",
                                  }}
                                />
                              ),
                              table: ({ children }) => (
                                <div className="overflow-x-auto my-2">
                                  <table
                                    className="min-w-full border-collapse"
                                    style={{ border: "1px solid var(--border-strong)" }}
                                  >
                                    {children}
                                  </table>
                                </div>
                              ),
                              thead: ({ children }) => (
                                <thead style={{ background: "var(--bg-soft)" }}>{children}</thead>
                              ),
                              tbody: ({ children }) => <tbody>{children}</tbody>,
                              tr: ({ children }) => (
                                <tr style={{ borderBottom: "1px solid var(--border-soft)" }}>{children}</tr>
                              ),
                              th: ({ children }) => (
                                <th
                                  className="px-2 py-1 text-left font-bold text-sm"
                                  style={{
                                    color: "var(--text-primary)",
                                    border: "1px solid var(--border-soft)",
                                  }}
                                >
                                  {children}
                                </th>
                              ),
                              td: ({ children }) => (
                                <td
                                  className="px-2 py-1 text-sm"
                                  style={{
                                    color: "var(--text-primary)",
                                    border: "1px solid var(--border-soft)",
                                  }}
                                >
                                  {children}
                                </td>
                              ),
                            }}
                          >
                            {message.content}
                          </ReactMarkdown>
                        </div>
                        {Array.isArray(message.artifacts) && message.artifacts.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {message.artifacts.map((artifact) =>
                              artifact.status === "ready" ? (
                                <DocumentChip
                                  key={artifact.id}
                                  name={`${artifact.fileName}.docx`}
                                  fill="white"
                                  onActivate={() =>
                                    setPreview({ id: artifact.id, fileName: artifact.fileName })
                                  }
                                  title={`Открыть «${artifact.fileName}.docx»`}
                                  trailing={
                                    <Eye
                                      className="h-3.5 w-3.5 shrink-0"
                                      style={{ color: "var(--text-secondary)" }}
                                    />
                                  }
                                />
                              ) : (
                                <DocumentChip
                                  key={artifact.id}
                                  variant="error"
                                  name={`${artifact.fileName}.docx`}
                                  fill="white"
                                  error="Не удалось оформить документ"
                                />
                              ),
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}

                {isLoading && (
                  <div className="flex justify-start">
                    <div className="max-w-full md:max-w-[80%]">
                      {toolStatus ? (
                        // A tool is running — show what the agent is doing.
                        <div
                          className="flex items-center gap-2 text-sm"
                          style={{ color: "var(--text-secondary)" }}
                        >
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span>{toolStatus}</span>
                        </div>
                      ) : streamingDraft ? (
                        // The answer is streaming — render it live (plain text;
                        // full markdown renders once the message is committed).
                        // SERVERLESS NOTE: this typewriter only animates if the
                        // backend response actually streams. On Yandex Serverless
                        // it's buffered, so the draft appears all at once; on a
                        // normal server / VM it types out token by token.
                        <div
                          className="whitespace-pre-wrap text-sm leading-relaxed"
                          style={{ color: "var(--text-primary)" }}
                        >
                          {streamingDraft}
                          <span
                            className="ml-0.5 inline-block h-4 w-[2px] animate-pulse align-text-bottom"
                            style={{ background: "var(--brand-accent)" }}
                          />
                        </div>
                      ) : isThinking ? (
                        <ThinkingIndicator isThinking={true} startedAt={thinkingStartedAt} />
                      ) : (
                        <div
                          className="flex items-center gap-2 text-sm"
                          style={{ color: "var(--text-secondary)" }}
                        >
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span>AI обрабатывает запрос…</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>
          </div>

          {/* Composer */}
          <div
            style={{
              flexShrink: 0,
              borderTop: "1px solid var(--border-strong)",
              background: "#fff",
            }}
          >
            <form
              onSubmit={handleSubmit}
              className="mx-auto flex w-full max-w-[860px] flex-col gap-2 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] pt-3.5 pb-[max(18px,env(safe-area-inset-bottom))] md:pl-[max(2rem,env(safe-area-inset-left))] md:pr-[max(2rem,env(safe-area-inset-right))]"
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple
                accept=".pdf,.doc,.docx,.txt,.md,.rtf,image/*"
                onChange={handleFileInputChange}
              />

              {(pendingDocuments.length > 0 || uploadingDocuments.length > 0) && (
                <div className="flex flex-wrap gap-2">
                  {/* Attached first, uploads after: while uploads succeed in
                      pick order a chip keeps its slot when it flips to
                      "attached"; an errored file's chip stays on the right
                      with the still-uploading ones. */}
                  {pendingDocuments.map((document) => (
                    <DocumentChip
                      key={document.id}
                      variant="attached"
                      name={document.name}
                      onRemove={() => onRemovePendingDocument(document.id)}
                    />
                  ))}
                  {uploadingDocuments.map((upload) => (
                    <DocumentChip
                      key={upload.localId}
                      variant={upload.status === "uploading" ? "uploading" : "error"}
                      name={upload.name}
                      error={upload.error}
                      onRemove={
                        upload.status === "error"
                          ? () => onRemoveUploadingDocument(upload.localId)
                          : undefined
                      }
                    />
                  ))}
                </div>
              )}

              <div
                className="composer-row"
                style={{
                  display: "flex",
                  alignItems: "flex-end",
                  gap: 10,
                  background: "#fff",
                  border: "1px solid var(--border-strong)",
                  borderRadius: 12,
                  padding: "10px 12px",
                  transition: "border-color .15s, box-shadow .15s",
                }}
              >
                <Textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(event) => onInputChange(event.target.value)}
                  onKeyDown={handleInputKeyDown}
                  rows={1}
                  placeholder={
                    accessExpired
                      ? "Доступ приостановлен"
                      : "Опишите ситуацию, вопрос или запрос…"
                  }
                  className="flex-1 resize-none border-0 bg-transparent text-base shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 focus:outline-none outline-none min-h-0 md:text-[15px]"
                  style={{
                    minHeight: 24,
                    maxHeight: COMPOSER_MAX_HEIGHT,
                    padding: 4,
                    lineHeight: 1.5,
                    color: "var(--text-primary)",
                    background: "transparent",
                    overflowY: "hidden",
                  }}
                  disabled={isLoading || isLoadingChats || accessExpired}
                />
                <div className="flex gap-1.5 flex-shrink-0">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={handleAttachButtonClick}
                    disabled={!activeSession || accessExpired}
                    title={accessExpired ? "Доступ приостановлен" : "Прикрепить файл"}
                    className="size-11 border border-[var(--border-strong)] text-[var(--text-secondary)] md:size-9"
                  >
                    <Paperclip className="h-4 w-4" />
                    <span className="sr-only">Прикрепить</span>
                  </Button>
                  <Button
                    type="submit"
                    disabled={
                      accessExpired ||
                      isLoading ||
                      isUploadingDocument ||
                      isLoadingChats ||
                      isLoadingMessages ||
                      (!input.trim() && pendingDocuments.length === 0)
                    }
                    size="icon"
                    title={accessExpired ? "Доступ приостановлен" : "Отправить"}
                    className="size-11 bg-[var(--brand-accent)] text-white hover:bg-[var(--brand-accent-hover)] md:size-9"
                  >
                    {isLoading || isLoadingChats || isLoadingMessages ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Send className="h-4 w-4" />
                    )}
                    <span className="sr-only">Отправить</span>
                  </Button>
                </div>
              </div>
              <p
                className="px-1"
                style={{ fontSize: 12, color: "var(--text-muted)" }}
              >
                {accessExpired
                  ? "Доступ приостановлен. Свяжитесь с нами, чтобы продолжить работу."
                  : "Enter — отправить · Shift+Enter — новая строка"}
              </p>
            </form>
          </div>
        </main>
      </div>

      {isSidebarOpen && (
        <button
          type="button"
          onClick={() => setIsSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-black/40 backdrop-blur-sm md:hidden"
          aria-label="Закрыть панель"
        />
      )}
    </div>
  );
}

function ModeToggle({
  selectedModel,
  onChange,
  className,
}: {
  selectedModel: SelectedModel;
  onChange: (m: SelectedModel) => void;
  className?: string;
}) {
  const options = [
    { key: "fast", icon: "⚡", label: "Быстрая", title: "Быстрая модель" },
    { key: "thinking", icon: "🧠", label: "Думающая", title: "Думающая модель" },
  ] as const;

  return (
    <div
      role="tablist"
      className={cn("inline-flex gap-0.5 rounded-full bg-[#F1EFE7] p-[3px]", className)}
    >
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          role="tab"
          aria-selected={selectedModel === option.key}
          onClick={() => onChange(option.key)}
          title={option.title}
          className={cn(
            "inline-flex items-center justify-center gap-1.5 rounded-full border-0 bg-transparent px-3.5 py-2.5 text-sm font-medium leading-[1.6] text-[var(--text-secondary)] transition-colors md:py-1.5 md:text-[13px]",
            selectedModel === option.key &&
              "bg-white text-[var(--text-primary)] shadow-[var(--shadow-sm)]",
          )}
        >
          <span>{option.icon}</span>
          {option.label}
        </button>
      ))}
    </div>
  );
}
