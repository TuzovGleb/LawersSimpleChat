"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThinkingIndicator } from "@/components/thinking-indicator";
import { cn } from "@/lib/utils";
import type { ChatMessage, Project, SessionDocument, SelectedModel } from "@/lib/types";
import {
  ArrowLeft,
  Download,
  FileText,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Paperclip,
  Plus,
  Send,
  X,
  Zap,
  Brain,
} from "lucide-react";

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
  isUploadingDocument: boolean;
  isLoadingChats: boolean;
  pendingDocuments: SessionDocument[];
  selectedModel: SelectedModel;
  onModelChange: (model: SelectedModel) => void;
  onBack: () => void;
  onSelectSession: (sessionId: string) => void;
  onNewChat: () => void;
  onInputChange: (value: string) => void;
  onSendMessage: () => void;
  onAttachDocument: (files: FileList | null) => void;
  onRemovePendingDocument: (documentId: string) => void;
  onExportMessage?: (messageIndex: number) => void;
  onSignOut: () => void;
}

export function CaseWorkspace({
  project,
  sessions,
  activeSessionId,
  input,
  isLoading,
  isThinking,
  isUploadingDocument,
  isLoadingChats,
  pendingDocuments,
  selectedModel,
  onModelChange,
  onBack,
  onSelectSession,
  onNewChat,
  onInputChange,
  onSendMessage,
  onAttachDocument,
  onRemovePendingDocument,
  onExportMessage,
  onSignOut,
}: CaseWorkspaceProps) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  const sortedSessions = useMemo(
    () =>
      [...sessions].sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      ),
    [sessions],
  );

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeSession?.messages]);

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

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      dragCounterRef.current = 0;
      setIsDragging(false);
      if (e.dataTransfer.files.length > 0) {
        onAttachDocument(e.dataTransfer.files);
      }
    },
    [onAttachDocument],
  );

  const handlePageDragEnter = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      dragCounterRef.current += 1;
      setIsDragging(true);
    }
  }, []);

  const handlePageDragLeave = useCallback(() => {
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) {
      setIsDragging(false);
    }
  }, []);

  const handlePageDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      dragCounterRef.current = 0;
      setIsDragging(false);
      if (e.dataTransfer.files.length > 0) {
        onAttachDocument(e.dataTransfer.files);
      }
    },
    [onAttachDocument],
  );

  return (
    <div
      className="relative flex h-screen flex-col"
      style={{ background: "var(--bg)" }}
      onDragEnter={handlePageDragEnter}
      onDragLeave={handlePageDragLeave}
      onDragOver={(e) => e.preventDefault()}
      onDrop={handlePageDrop}
    >
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
        className="flex-shrink-0"
        style={{
          background: "var(--bg)",
          borderBottom: "1px solid var(--border-strong)",
        }}
      >
        <div className="flex items-center justify-between px-5" style={{ height: 60 }}>
          <div className="flex items-center gap-3 min-w-0">
            <Button
              variant="ghost"
              size="icon"
              onClick={onBack}
              title="К списку дел"
              style={{ color: "var(--text-secondary)" }}
            >
              <ArrowLeft className="h-5 w-5" />
              <span className="sr-only">Вернуться к списку дел</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(true)}
              className="md:hidden"
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
            <ModeToggle selectedModel={selectedModel} onChange={onModelChange} />
            <Button
              variant="ghost"
              size="icon"
              onClick={onSignOut}
              title="Выйти"
              style={{ color: "var(--text-secondary)" }}
            >
              <LogOut className="h-5 w-5" />
              <span className="sr-only">Выйти</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Main */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-80 flex-col transition-transform duration-300 md:static md:translate-x-0",
            isSidebarOpen ? "translate-x-0" : "-translate-x-full",
          )}
          style={{
            background: "var(--bg)",
            borderRight: "1px solid var(--border-strong)",
            minHeight: 0,
          }}
        >
          <div
            className="flex items-center justify-between border-b p-4 md:hidden"
            style={{ borderBottomColor: "var(--border-strong)" }}
          >
            <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              Чаты
            </h2>
            <Button variant="ghost" size="icon" onClick={() => setIsSidebarOpen(false)}>
              <X className="h-5 w-5" />
              <span className="sr-only">Закрыть панель</span>
            </Button>
          </div>

          <div style={{ padding: 12, borderBottom: "1px solid var(--border-soft)" }}>
            <Button
              onClick={onNewChat}
              className="btn btn-outline-accent w-full"
              style={{ padding: "9px 12px", fontSize: 13.5 }}
            >
              <Plus className="h-4 w-4 mr-1.5" />
              Новый чат
            </Button>
          </div>

          <div className="flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="space-y-1 p-2">
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
                        onClick={() => {
                          onSelectSession(session.id);
                          setIsSidebarOpen(false);
                        }}
                        className="flex w-full items-start gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors"
                        style={{
                          background: isActive ? "var(--brand-accent-bg)" : "transparent",
                          color: "var(--text-primary)",
                        }}
                        onMouseEnter={(e) => {
                          if (!isActive) {
                            (e.currentTarget as HTMLElement).style.background = "#F1EFE7";
                          }
                        }}
                        onMouseLeave={(e) => {
                          if (!isActive) {
                            (e.currentTarget as HTMLElement).style.background = "transparent";
                          }
                        }}
                      >
                        <MessageSquare
                          className="h-4 w-4 flex-shrink-0 mt-0.5"
                          style={{
                            color: isActive ? "var(--brand-accent)" : "var(--text-secondary)",
                          }}
                        />
                        <div className="min-w-0 flex-1">
                          <div
                            className="text-sm font-medium truncate"
                            style={{ color: "var(--text-primary)" }}
                          >
                            {session.title || "Новый чат"}
                          </div>
                          <div
                            className="text-xs mt-0.5"
                            style={{ color: "var(--text-muted)" }}
                          >
                            {new Date(session.createdAt).toLocaleString("ru-RU", {
                              day: "numeric",
                              month: "short",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </div>
                        </div>
                      </button>
                    );
                  })
                )}
              </div>
            </ScrollArea>
          </div>
        </aside>

        {/* Chat area */}
        <main className="flex flex-1 flex-col" style={{ background: "#FBFAF6", minWidth: 0 }}>
          <div className="flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="chat-container mx-auto flex w-full max-w-3xl flex-col gap-4 px-5 py-8">
                {isLoadingChats && !activeSession ? (
                  <div className="mt-10 text-center" style={{ color: "var(--text-secondary)" }}>
                    <Loader2 className="mx-auto h-10 w-10 animate-spin" />
                    <h2 className="text-xl font-semibold mt-4" style={{ color: "var(--text-primary)" }}>
                      Загрузка чатов...
                    </h2>
                    <p className="mt-2 text-sm">Получаем вашу историю разговоров</p>
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
                          className="message-content rounded-2xl px-4 py-3"
                          style={{ background: "#F0F0EE" }}
                        >
                          <p
                            className="mobile-safe-text whitespace-pre-wrap text-sm leading-relaxed"
                            style={{ wordBreak: "normal", overflowWrap: "break-word", color: "var(--text-primary)" }}
                          >
                            {message.content}
                          </p>
                          {Array.isArray(message.attachedDocumentIds) &&
                            message.attachedDocumentIds.length > 0 && (
                              <div className="mt-2 flex flex-wrap justify-end gap-1.5">
                                {message.attachedDocumentIds.map((documentId) => {
                                  const document = message.attachedDocuments?.find(
                                    (item) => item.id === documentId,
                                  );
                                  const label =
                                    document?.name ?? `Документ ${documentId.slice(0, 8)}`;
                                  return (
                                    <span
                                      key={documentId}
                                      className="inline-flex max-w-[240px] items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]"
                                      style={{
                                        border: "1px solid var(--border-strong)",
                                        background: "#fff",
                                        color: "var(--text-secondary)",
                                      }}
                                      title={label}
                                    >
                                      <FileText
                                        className="h-3 w-3 shrink-0"
                                        style={{ color: "var(--brand-accent)" }}
                                      />
                                      <span className="truncate">{label}</span>
                                    </span>
                                  );
                                })}
                              </div>
                            )}
                        </div>
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
                          className="message-content prose prose-sm prose-mobile max-w-none"
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
                            remarkPlugins={[remarkGfm]}
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
                        {onExportMessage && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="absolute -right-10 top-0 h-8 w-8 opacity-0 transition-opacity group-hover:opacity-100"
                            onClick={() => onExportMessage(index)}
                            title="Скачать ответ в формате DOCX"
                          >
                            <Download className="h-4 w-4" />
                            <span className="sr-only">Скачать ответ</span>
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                ))}

                {isLoading && (
                  <div className="flex justify-start">
                    <div className="max-w-full md:max-w-[80%]">
                      {isThinking ? (
                        <ThinkingIndicator isThinking={true} />
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
              onDragOver={handleDragOver}
              onDrop={handleDrop}
              className="mx-auto flex w-full max-w-3xl flex-col gap-2"
              style={{ padding: "14px 20px 18px" }}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple
                accept=".pdf,.doc,.docx,.txt,.md,.rtf,image/*"
                onChange={handleFileInputChange}
              />

              {pendingDocuments.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {pendingDocuments.map((document) => (
                    <span
                      key={document.id}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs"
                      style={{
                        border: "1px solid var(--border-strong)",
                        background: "var(--bg-soft)",
                        color: "var(--text-primary)",
                      }}
                    >
                      <FileText
                        className="h-3 w-3 shrink-0"
                        style={{ color: "var(--brand-accent)" }}
                      />
                      <span className="max-w-[240px] truncate" title={document.name}>
                        {document.name}
                      </span>
                      <button
                        type="button"
                        className="-mr-1 rounded-full p-0.5 hover:opacity-70"
                        onClick={() => onRemovePendingDocument(document.id)}
                        aria-label={`Убрать ${document.name}`}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
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
                  value={input}
                  onChange={(event) => onInputChange(event.target.value)}
                  onKeyDown={handleInputKeyDown}
                  placeholder="Опишите ситуацию, вопрос или запрос…"
                  className="flex-1 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
                  style={{
                    minHeight: 24,
                    maxHeight: 160,
                    padding: 4,
                    fontSize: 15,
                    color: "var(--text-primary)",
                    background: "transparent",
                  }}
                  disabled={isLoading || isLoadingChats}
                />
                <div className="flex gap-1.5 flex-shrink-0">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={handleAttachButtonClick}
                    disabled={isUploadingDocument}
                    title="Прикрепить файл"
                    style={{
                      width: 36,
                      height: 36,
                      border: "1px solid var(--border-strong)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {isUploadingDocument ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Paperclip className="h-4 w-4" />
                    )}
                    <span className="sr-only">Прикрепить</span>
                  </Button>
                  <Button
                    type="submit"
                    disabled={
                      isLoading ||
                      isUploadingDocument ||
                      isLoadingChats ||
                      (!input.trim() && pendingDocuments.length === 0)
                    }
                    size="icon"
                    title="Отправить"
                    style={{
                      width: 36,
                      height: 36,
                      background: "var(--brand-accent)",
                      color: "#fff",
                    }}
                  >
                    {isLoading || isLoadingChats ? (
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
                Enter — отправить · Shift+Enter — новая строка
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
}: {
  selectedModel: SelectedModel;
  onChange: (m: SelectedModel) => void;
}) {
  const isFast = selectedModel === "openai";
  const isDeep = selectedModel === "thinking";

  const baseBtnStyle: React.CSSProperties = {
    border: 0,
    background: "transparent",
    padding: "6px 14px",
    borderRadius: 999,
    fontSize: 13,
    fontWeight: 500,
    color: "var(--text-secondary)",
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    cursor: "pointer",
    transition: "color .15s, background .15s",
  };

  const activeStyle: React.CSSProperties = {
    background: "#fff",
    color: "var(--text-primary)",
    boxShadow: "var(--shadow-sm)",
  };

  return (
    <div
      role="tablist"
      className="hidden sm:inline-flex"
      style={{
        background: "#F1EFE7",
        borderRadius: 999,
        padding: 3,
        gap: 2,
      }}
    >
      <button
        type="button"
        role="tab"
        aria-selected={isFast}
        onClick={() => onChange("openai")}
        style={{ ...baseBtnStyle, ...(isFast ? activeStyle : {}) }}
        title="Быстрая модель"
      >
        <Zap className="h-3.5 w-3.5" />
        Быстрая
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={isDeep}
        onClick={() => onChange("thinking")}
        style={{ ...baseBtnStyle, ...(isDeep ? activeStyle : {}) }}
        title="Думающая модель"
      >
        <Brain className="h-3.5 w-3.5" />
        Думающая
      </button>
    </div>
  );
}
