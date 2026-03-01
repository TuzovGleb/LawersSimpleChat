"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/theme-toggle";
import { ThinkingIndicator } from "@/components/thinking-indicator";
import { cn } from "@/lib/utils";
import type { ChatMessage, Project, SessionDocument, SelectedModel } from "@/lib/types";
import type { AnonymizationProgress } from "@/lib/anonymizer/types";
import { getModelDisplayName } from "@/lib/model-config";
import { useToast } from "@/hooks/use-toast";
import {
  ArrowLeft,
  Bot,
  Download,
  FileText,
  Loader2,
  Menu,
  MessageSquare,
  Moon,
  Paperclip,
  Plus,
  Send,
  Shield,
  ShieldOff,
  Sun,
  Trash2,
  Upload,
  X,
  ChevronDown,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

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
  isDocumentsLoading: boolean;
  isLoadingChats: boolean;
  selectedModel: SelectedModel;
  anonymousMode: boolean;
  anonymizationProgress: AnonymizationProgress | null;
  onModelChange: (model: SelectedModel) => void;
  onAnonymousModeChange: (enabled: boolean) => void;
  onBack: () => void;
  onSelectSession: (sessionId: string) => void;
  onNewChat: () => void;
  onInputChange: (value: string) => void;
  onSendMessage: () => void;
  onAttachDocument: (files: FileList | null) => void;
  onRemoveDocument: (documentId: string) => void;
  onExportMessage?: (messageIndex: number) => void;
}

export function CaseWorkspace({
  project,
  sessions,
  activeSessionId,
  input,
  isLoading,
  isThinking,
  isUploadingDocument,
  isDocumentsLoading,
  isLoadingChats,
  selectedModel,
  anonymousMode,
  anonymizationProgress,
  onModelChange,
  onAnonymousModeChange,
  onBack,
  onSelectSession,
  onNewChat,
  onInputChange,
  onSendMessage,
  onAttachDocument,
  onRemoveDocument,
  onExportMessage,
}: CaseWorkspaceProps) {
  const { toast } = useToast();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [sidebarView, setSidebarView] = useState<'chats' | 'documents'>('chats');
  const [isDarkMode, setIsDarkMode] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()),
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

  const bgColor = isDarkMode ? '#1e293b' : '#fafaf5';
  const textColor = isDarkMode ? '#ffffff' : '#000';
  const borderColor = isDarkMode ? '#334155' : '#982525';
  const sidebarBg = isDarkMode ? '#253141' : '#f0f0eb';
  const messageAiBg = isDarkMode ? '#253141' : '#f0f0eb';
  const mutedTextColor = isDarkMode ? '#cbd5e1' : '#666';

  // Устанавливаем data-атрибут для темы toast
  useEffect(() => {
    if (isDarkMode) {
      document.body.setAttribute('data-chat-theme', 'dark');
    } else {
      document.body.setAttribute('data-chat-theme', 'light');
    }
  }, [isDarkMode]);

  return (
    <div className="flex h-screen flex-col bg-background" style={{ background: bgColor, fontFamily: 'var(--font-roboto), Roboto, sans-serif', fontWeight: 400, color: textColor }}>
      {/* Header */}
      <header className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60" style={{ background: bgColor, borderBottom: `1px solid ${borderColor}` }}>
        <div className="flex h-16 items-center justify-between px-4">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={onBack} className="hidden sm:flex">
              <ArrowLeft className="h-5 w-5" />
              <span className="sr-only">Вернуться к списку дел</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={onBack}
              className="sm:hidden"
            >
              <ArrowLeft className="h-5 w-5" />
              <span className="sr-only">Назад</span>
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
            <div className="flex items-center gap-2">
              <div className="hidden rounded-lg bg-muted p-1.5 sm:block" style={{ background: sidebarBg }}>
                <Bot className="h-4 w-4 text-muted-foreground" style={{ color: isDarkMode ? textColor : '#982525' }} />
              </div>
              <div className="flex flex-col">
                <span className="text-sm font-semibold leading-tight" style={{ color: textColor }}>{project.name}</span>
                <span className="hidden text-xs text-muted-foreground sm:block" style={{ color: mutedTextColor }}>
                  {isDocumentsLoading ? (
                    <>
                      <Loader2 className="inline h-3 w-3 animate-spin" /> документов
                    </>
                  ) : (
                    `${project.documents.length} документов`
                  )} · {isLoadingChats ? (
                    <>
                      <Loader2 className="inline h-3 w-3 animate-spin" /> чатов
                    </>
                  ) : (
                    `${sessions.length} чатов`
                  )}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Model Selection Dropdown */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="hidden sm:flex" style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', color: textColor }}>
                  <span className="text-xs">
                    {getModelDisplayName(selectedModel)}
                  </span>
                  <ChevronDown className="ml-2 h-3 w-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent 
                align="end" 
                className="z-[100]"
                style={{
                  background: isDarkMode ? '#253141' : '#fafaf5',
                  border: `1px solid ${borderColor}`,
                  color: textColor,
                }}
              >
                {(['openai', 'thinking'] as const).map((model) => (
                  <DropdownMenuItem
                    key={model}
                    onClick={() => onModelChange(model)}
                    className={selectedModel === model ? 'bg-accent' : ''}
                    style={{
                      color: textColor,
                      background: selectedModel === model 
                        ? (isDarkMode ? '#334155' : '#f0f0eb')
                        : 'transparent',
                    }}
                    onMouseEnter={(e) => {
                      if (selectedModel !== model) {
                        e.currentTarget.style.background = isDarkMode ? '#334155' : '#f0f0eb';
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (selectedModel !== model) {
                        e.currentTarget.style.background = 'transparent';
                      }
                    }}
                  >
                    {getModelDisplayName(model)}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button
              variant={anonymousMode ? "default" : "ghost"}
              size="icon"
              onClick={() => onAnonymousModeChange(!anonymousMode)}
              title={anonymousMode ? "Анонимный режим включён" : "Включить анонимный режим"}
              style={{
                color: anonymousMode ? '#ffffff' : textColor,
                background: anonymousMode ? (isDarkMode ? '#166534' : '#16a34a') : 'transparent',
              }}
            >
              {anonymousMode ? (
                <Shield className="h-5 w-5" />
              ) : (
                <ShieldOff className="h-5 w-5" />
              )}
              <span className="sr-only">Анонимный режим</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsDarkMode(!isDarkMode)}
              title={isDarkMode ? "Светлая тема" : "Темная тема"}
              style={{ color: textColor }}
            >
              {isDarkMode ? (
                <Sun className="h-5 w-5" />
              ) : (
                <Moon className="h-5 w-5" />
              )}
              <span className="sr-only">Переключить тему чата</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Anonymization Progress Banner */}
      {anonymizationProgress && anonymizationProgress.stage !== 'done' && (
        <div
          className="border-b px-4 py-2"
          style={{ background: isDarkMode ? '#1e3a2e' : '#f0fdf4', borderBottomColor: isDarkMode ? '#166534' : '#86efac' }}
        >
          <div className="mx-auto flex max-w-4xl items-center gap-3">
            <Shield className="h-4 w-4 flex-shrink-0" style={{ color: isDarkMode ? '#86efac' : '#16a34a' }} />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium truncate" style={{ color: isDarkMode ? '#bbf7d0' : '#166534' }}>
                {anonymizationProgress.message}
              </p>
              <div className="mt-1 h-1.5 w-full rounded-full overflow-hidden" style={{ background: isDarkMode ? '#14532d' : '#dcfce7' }}>
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.round(anonymizationProgress.progress * 100)}%`,
                    background: isDarkMode ? '#4ade80' : '#16a34a',
                  }}
                />
              </div>
            </div>
            <span className="text-xs font-mono flex-shrink-0" style={{ color: isDarkMode ? '#86efac' : '#16a34a' }}>
              {Math.round(anonymizationProgress.progress * 100)}%
            </span>
          </div>
        </div>
      )}

      {/* Main Content - Two Columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar - Chats & Documents with Toggle */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-80 flex-col border-r bg-muted/30 transition-transform duration-300 md:static md:translate-x-0",
            isSidebarOpen ? "translate-x-0" : "-translate-x-full",
          )}
          style={{ background: sidebarBg, borderRight: `1px solid ${borderColor}` }}
        >
          {/* Mobile Header */}
          <div className="flex items-center justify-between border-b p-4 md:hidden" style={{ borderBottomColor: borderColor }}>
            <h2 className="text-sm font-semibold" style={{ color: textColor }}>
              {sidebarView === 'chats' ? 'Чаты' : 'Документы'}
            </h2>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsSidebarOpen(false)}
            >
              <X className="h-5 w-5" />
              <span className="sr-only">Закрыть панель</span>
            </Button>
          </div>

          {/* Tab Switcher */}
          <div className="border-b bg-background/50" style={{ background: sidebarBg, borderBottom: `1px solid ${borderColor}` }}>
            <div className="flex">
              <button
                onClick={() => setSidebarView('chats')}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-all",
                  sidebarView === 'chats'
                    ? "border-b-2 border-primary text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
                style={{ borderBottomColor: sidebarView === 'chats' ? (isDarkMode ? textColor : '#982525') : 'transparent', color: textColor }}
              >
                <MessageSquare className="h-4 w-4" style={{ color: sidebarView === 'chats' ? (isDarkMode ? textColor : '#982525') : mutedTextColor }} />
                <span>Чаты</span>
                <span className="text-xs" style={{ color: mutedTextColor }}>
                  {sessions.length}
                </span>
              </button>
              <button
                onClick={() => setSidebarView('documents')}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-all",
                  sidebarView === 'documents'
                    ? "border-b-2 border-primary text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
                style={{ borderBottomColor: sidebarView === 'documents' ? (isDarkMode ? textColor : '#982525') : 'transparent', color: textColor }}
              >
                <FileText className="h-4 w-4" style={{ color: sidebarView === 'documents' ? (isDarkMode ? textColor : '#982525') : mutedTextColor }} />
                <span>Документы</span>
                <span className="text-xs" style={{ color: mutedTextColor }}>
                  {project.documents.length}
                </span>
              </button>
            </div>
          </div>

          {/* Chats View */}
          {sidebarView === 'chats' && (
            <>
              <div className="border-b p-4" style={{ borderBottomColor: borderColor }}>
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-semibold" style={{ color: textColor }}>Чаты проекта</h3>
                  {isLoadingChats && <Loader2 className="h-4 w-4 animate-spin" style={{ color: mutedTextColor }} />}
                </div>
                <Button onClick={onNewChat} variant="outline" className="w-full gap-2" style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', color: textColor }}>
                  <Plus className="h-4 w-4" style={{ color: isDarkMode ? textColor : '#982525' }} />
                  Новый чат
                </Button>
              </div>
              <div className="flex-1 overflow-hidden">
                <ScrollArea className="h-full">
                  <div className="space-y-1 pb-4 px-2 pt-2">
                    {isLoadingChats ? (
                      <div className="rounded-lg border border-dashed px-4 py-8 text-center" style={{ borderColor: borderColor }}>
                        <Loader2 className="mx-auto h-8 w-8 animate-spin" style={{ color: mutedTextColor }} />
                        <p className="mt-2 text-sm" style={{ color: mutedTextColor }}>Загрузка чатов...</p>
                        <p className="mt-1 text-xs" style={{ color: mutedTextColor }}>
                          Получаем историю разговоров
                        </p>
                      </div>
                    ) : sortedSessions.length === 0 ? (
                      <div className="rounded-lg border border-dashed px-4 py-8 text-center" style={{ borderColor: borderColor }}>
                        <MessageSquare className="mx-auto h-8 w-8" style={{ color: mutedTextColor }} />
                        <p className="mt-2 text-sm" style={{ color: mutedTextColor }}>Нет чатов</p>
                        <p className="mt-1 text-xs" style={{ color: mutedTextColor }}>Создайте первый чат</p>
                      </div>
                    ) : (
                      sortedSessions.map((session) => (
                        <button
                          key={session.id}
                          onClick={() => {
                            onSelectSession(session.id);
                            setIsSidebarOpen(false);
                          }}
                          className={cn(
                            "flex w-full flex-col items-start rounded-lg px-3 py-3 text-left transition-all hover:bg-muted",
                            session.id === activeSessionId
                              ? "bg-muted shadow-sm border-l-2"
                              : "bg-transparent",
                          )}
                          style={{ 
                            background: session.id === activeSessionId ? (isDarkMode ? '#334155' : '#f0f0eb') : 'transparent', 
                            color: textColor,
                            borderLeftColor: session.id === activeSessionId ? (isDarkMode ? textColor : '#982525') : 'transparent',
                            borderLeftWidth: session.id === activeSessionId ? '2px' : '0',
                            maxWidth: '100%',
                            overflow: 'hidden'
                          }}
                        >
                          <div className="flex w-full items-start gap-2 min-w-0" style={{ width: '100%' }}>
                            <MessageSquare className="h-4 w-4 flex-shrink-0 mt-0.5" style={{ color: isDarkMode ? textColor : '#982525' }} />
                            <span className="flex-1 text-sm font-medium break-words" style={{ wordBreak: 'break-word', overflowWrap: 'break-word', lineHeight: '1.4', minWidth: 0, color: textColor }}>
                              {session.title || "Новый чат"}
                            </span>
                          </div>
                          <span className="ml-6 mt-1 text-xs text-muted-foreground" style={{ color: mutedTextColor }}>
                            {new Date(session.createdAt).toLocaleString("ru-RU", {
                              day: "numeric",
                              month: "short",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                </ScrollArea>
              </div>
            </>
          )}

          {/* Documents View */}
          {sidebarView === 'documents' && (
            <>
              <div className="border-b p-4" style={{ borderBottom: `1px solid ${borderColor}` }}>
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-semibold" style={{ color: textColor }}>Документы проекта</h3>
                  {isDocumentsLoading && <Loader2 className="h-4 w-4 animate-spin" style={{ color: mutedTextColor }} />}
                </div>
                <Button
                  onClick={handleAttachButtonClick}
                  disabled={isUploadingDocument}
                  variant="outline"
                  className="w-full gap-2"
                  size="sm"
                  style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', color: textColor }}
                >
                  {isUploadingDocument ? (
                    <Loader2 className="h-4 w-4 animate-spin" style={{ color: textColor }} />
                  ) : (
                    <Upload className="h-4 w-4" style={{ color: isDarkMode ? textColor : '#982525' }} />
                  )}
                  <span style={{ color: textColor }}>Загрузить документ</span>
                </Button>
              </div>
              <div className="flex-1 overflow-hidden">
                <ScrollArea className="h-full">
                  <div className="space-y-2 pb-4 px-2 pt-2">
                    {isDocumentsLoading ? (
                      <div className="rounded-lg border border-dashed px-4 py-8 text-center" style={{ borderColor: borderColor }}>
                        <Loader2 className="mx-auto h-8 w-8 animate-spin" style={{ color: mutedTextColor }} />
                        <p className="mt-2 text-sm" style={{ color: mutedTextColor }}>Загрузка документов...</p>
                        <p className="mt-1 text-xs" style={{ color: mutedTextColor }}>
                          Получаем прикрепленные файлы
                        </p>
                      </div>
                    ) : project.documents.length === 0 ? (
                      <div className="rounded-lg border border-dashed px-4 py-8 text-center" style={{ borderColor: borderColor }}>
                        <FileText className="mx-auto h-8 w-8" style={{ color: mutedTextColor }} />
                        <p className="mt-2 text-sm" style={{ color: mutedTextColor }}>Нет документов</p>
                        <p className="mt-1 text-xs" style={{ color: mutedTextColor }}>
                          Загрузите документы для работы
                        </p>
                      </div>
                    ) : (
                      project.documents.map((document) => (
                        <div
                          key={document.id}
                          className="group rounded-lg border bg-card p-3 transition-all hover:shadow-sm"
                          style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', width: 'calc(100% - 0.5rem)', marginLeft: '0.25rem', marginRight: '0.25rem' }}
                        >
                          <div className="flex items-start gap-3" style={{ width: '100%' }}>
                            <div className="rounded-md p-2 shrink-0" style={{ background: isDarkMode ? '#334155' : '#f0f0eb' }}>
                              <FileText className="h-4 w-4" style={{ color: isDarkMode ? textColor : '#982525' }} />
                            </div>
                            <div className="min-w-0 flex-1" style={{ minWidth: 0, overflow: 'hidden' }}>
                              <div className="mb-1 text-sm font-medium leading-tight" style={{ color: textColor, wordBreak: 'break-word', overflowWrap: 'break-word', overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                                {document.name}
                              </div>
                              <div className="space-y-0.5 text-xs" style={{ color: mutedTextColor }}>
                                <div>{formatBytes(document.size)}</div>
                                <div>
                                  {new Date(document.uploadedAt).toLocaleDateString("ru-RU")}
                                </div>
                                <div className="text-xs">{formatStrategy(document.strategy)}</div>
                              </div>
                            </div>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                              onClick={() => onRemoveDocument(document.id)}
                              disabled={isUploadingDocument || isDocumentsLoading}
                            >
                              <Trash2 className="h-4 w-4" style={{ color: mutedTextColor }} />
                              <span className="sr-only">Удалить</span>
                            </Button>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </ScrollArea>
              </div>
            </>
          )}
        </aside>

        {/* Center Column - Chat Messages (Expanded) */}
        <main className="flex flex-1 flex-col">
          <div className="flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-6">
                {isLoadingChats && !activeSession ? (
                  <div className="mt-10 text-center text-muted-foreground">
                    <div className="mx-auto mb-4 rounded-full bg-muted p-6 w-fit">
                      <Loader2 className="h-12 w-12 animate-spin text-muted-foreground" />
                    </div>
                    <h2 className="text-xl font-semibold text-foreground">Загрузка чатов...</h2>
                    <p className="mt-2 text-sm">
                      Получаем вашу историю разговоров
                      <br />Подождите немного
                    </p>
                  </div>
                ) : activeSession && activeSession.messages.length === 0 && !isLoading && (
                  <div className="mt-10 text-center">
                    <div className="mx-auto mb-4 rounded-full bg-muted p-6 w-fit">
                      <Bot className="h-12 w-12 text-foreground" />
                    </div>
                    <h2 className="text-xl font-semibold text-foreground">С чего начнём?</h2>
                    <p className="mt-2 text-sm text-muted-foreground">
                      Опишите ситуацию, из-за которой вы обращаетесь.
                      <br />Я помогу разобраться со стратегией и рисками.
                    </p>
                  </div>
                )}

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
                        <div className="rounded-2xl bg-muted px-4 py-3" style={{ background: 'transparent' }}>
                          <p className="whitespace-pre-wrap text-sm font-normal leading-relaxed text-foreground/90" style={{ wordBreak: 'normal', overflowWrap: 'break-word', color: textColor }}>
                            {message.content}
                          </p>
                        </div>
                      </div>
                    ) : (
                      <div className="group relative max-w-full md:max-w-[80%]">
                        {message.metadata?.wasReasoning && message.metadata?.thinkingTimeSeconds && (
                          <div className="mb-2">
                            <ThinkingIndicator 
                              isThinking={false}
                              thinkingTime={message.metadata.thinkingTimeSeconds}
                              modelName={message.metadata.modelUsed}
                            />
                          </div>
                        )}
                        <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90" style={{ background: messageAiBg, padding: '1rem', borderRadius: '0.5rem', wordBreak: 'normal', overflowWrap: 'break-word', color: textColor }}>
                            <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              p: ({ children }) => <p className="text-sm font-normal leading-relaxed mb-3 last:mb-0" style={{ wordBreak: 'normal', overflowWrap: 'break-word', color: textColor }}>{children}</p>,
                              h1: ({ children }) => <h1 className="text-xl font-bold mb-4 mt-6 first:mt-0" style={{ color: textColor }}>{children}</h1>,
                              h2: ({ children }) => <h2 className="text-lg font-bold mb-4 mt-5 first:mt-0" style={{ color: textColor }}>{children}</h2>,
                              h3: ({ children }) => <h3 className="text-base font-bold mb-3 mt-4 first:mt-0" style={{ color: textColor }}>{children}</h3>,
                              h4: ({ children }) => <h4 className="text-sm font-bold mb-3 mt-3 first:mt-0" style={{ color: textColor }}>{children}</h4>,
                              h5: ({ children }) => <h5 className="text-sm font-bold mb-3 mt-3 first:mt-0" style={{ color: textColor }}>{children}</h5>,
                              h6: ({ children }) => <h6 className="text-sm font-bold mb-3 mt-3 first:mt-0" style={{ color: textColor }}>{children}</h6>,
                              ul: ({ children }) => <ul className="list-disc list-outside mb-3 space-y-1 ml-6">{children}</ul>,
                              ol: ({ children }) => <ol className="list-decimal list-outside mb-3 space-y-1 ml-6">{children}</ol>,
                              li: ({ children }) => <li className="text-sm font-normal leading-relaxed" style={{ wordBreak: 'normal', overflowWrap: 'break-word', color: textColor }}>{children}</li>,
                              blockquote: ({ children }) => <blockquote className="border-l-4 border-muted-foreground/30 pl-4 italic my-3 text-sm font-normal leading-relaxed" style={{ color: textColor, borderLeftColor: isDarkMode ? '#475569' : undefined }}>{children}</blockquote>,
                              code: ({ className, children, ...props }) => {
                                const isInline = !className;
                                if (isInline) {
                                  return (
                                    <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono" {...props}>
                                      {children}
                                    </code>
                                  );
                                }
                                return (
                                  <code className="block bg-muted p-3 rounded text-xs font-mono overflow-x-auto my-2" {...props}>
                                    {children}
                                  </code>
                                );
                              },
                              pre: ({ children }) => <pre className="mb-2">{children}</pre>,
                              strong: ({ children }) => <strong className="font-bold">{children}</strong>,
                              em: ({ children }) => <em className="italic">{children}</em>,
                              a: ({ children, href }) => (
                                <a href={href} className="text-primary underline hover:text-primary/80" target="_blank" rel="noopener noreferrer">
                                  {children}
                                </a>
                              ),
                              hr: () => <hr className="my-4 border-border" />,
                              table: ({ children }) => (
                                <div className="overflow-x-auto my-2">
                                  <table className="min-w-full border-collapse border border-border">
                                    {children}
                                  </table>
                                </div>
                              ),
                              thead: ({ children }) => <thead className="bg-muted">{children}</thead>,
                              tbody: ({ children }) => <tbody>{children}</tbody>,
                              tr: ({ children }) => <tr className="border-b border-border">{children}</tr>,
                              th: ({ children }) => <th className="border border-border px-2 py-1 text-left font-bold text-sm" style={{ color: textColor, borderColor: borderColor }}>{children}</th>,
                              td: ({ children }) => <td className="border border-border px-2 py-1 text-sm font-normal" style={{ color: textColor, borderColor: borderColor }}>{children}</td>,
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
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
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

          {/* Message Input */}
          <div className="border-t bg-background p-4" style={{ background: bgColor, borderTop: `1px solid ${borderColor}` }}>
            <form onSubmit={handleSubmit} className="mx-auto flex w-full max-w-4xl flex-col gap-3">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple
                accept=".pdf,.doc,.docx,.txt,.md,.rtf,image/*"
                onChange={handleFileInputChange}
              />

              <Textarea
                value={input}
                onChange={(event) => onInputChange(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder="Опишите ситуацию, вопрос или запрос к защитнику…"
                className="min-h-[100px] resize-none"
                disabled={isLoading || isLoadingChats}
                style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', color: textColor }}
              />
              <div className="flex items-center justify-between gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleAttachButtonClick}
                  disabled={isUploadingDocument}
                  className="gap-2"
                  style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', color: textColor }}
                >
                  {isUploadingDocument ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Paperclip className="h-4 w-4" style={{ color: isDarkMode ? textColor : '#982525' }} />
                  )}
                  {isUploadingDocument ? "Обработка…" : "Прикрепить"}
                </Button>
              <Button 
                type="submit" 
                disabled={isLoading || isUploadingDocument || isLoadingChats || !input.trim()} 
                variant="outline"
                className="gap-2"
                style={{ border: `1px solid ${borderColor}`, background: isDarkMode ? '#253141' : '#fafaf5', color: textColor }}
              >
                {isLoading || isLoadingChats ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" style={{ color: isDarkMode ? textColor : '#982525' }} />
                )}
                {isLoadingChats ? "Загрузка..." : "Отправить"}
              </Button>
              </div>
            </form>
          </div>
        </main>
      </div>

      {/* Overlay for mobile sidebar */}
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

function formatBytes(size: number) {
  if (!size || size < 0) return "—";
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} КБ`;
  return `${(size / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatStrategy(strategy: SessionDocument["strategy"]) {
  switch (strategy) {
    case "pdf":
      return "PDF";
    case "docx":
      return "Word";
    case "vision":
      return "LLM/vision";
    case "llm-file":
      return "LLM/файл";
    default:
      return "Текст";
  }
}

