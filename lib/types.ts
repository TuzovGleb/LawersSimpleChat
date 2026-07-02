/**
 * Скачиваемый артефакт, прикреплённый к ответу помощника (например .docx,
 * собранный инструментом draft_document). Рендерится чипом под сообщением;
 * файл собирается по требованию по адресу /api/chat/{sessionId}/documents/{id}.
 */
export interface MessageArtifact {
  id: string;
  kind: 'docx';
  fileName: string;
  status: 'ready' | 'failed';
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  attachedDocumentIds?: string[];
  attachedDocuments?: ChatMessageDocument[];
  artifacts?: MessageArtifact[];
  metadata?: {
    modelUsed?: string;
    thinkingTimeSeconds?: number;
    wasReasoning?: boolean;
  };
  // Set on a user message whose turn failed to generate. Such a message is a
  // local-only, retryable artifact: it is never persisted server-side and is
  // excluded from request payloads and localStorage. Cleared on retry, dropped
  // when the user sends a new message instead of retrying.
  status?: 'failed';
  errorText?: string;
}

export interface ChatMessageDocument {
  id: string;
  name: string;
  mimeType?: string;
  size?: number;
}

export interface SessionDocument {
  id: string;
  name: string;
  mimeType: string;
  size: number;
  text: string;
  truncated: boolean;
  rawTextLength: number;
  strategy: 'text' | 'pdf' | 'docx' | 'doc' | 'vision' | 'llm-file';
  uploadedAt: string;
}

// Optimistic composer chip for a file that is still uploading/processing (or
// failed): exists only on the client, keyed by a local id — the server id
// (SessionDocument.id) appears only after extraction succeeds. sessionId pins
// the chip to the chat where the file was picked, so multi-minute extractions
// don't leak chips or send-blocking into other chats.
export interface UploadingDocument {
  localId: string;
  sessionId: string | null;
  name: string;
  status: 'uploading' | 'error';
  error?: string;
}

export interface ChatRequestDocument {
  id: string;
  name: string;
  text: string;
}

export interface Project {
  id: string;
  user_id?: string | null;
  name: string;
  slug?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectDocument {
  id: string;
  project_id: string;
  name: string;
  mimeType: string;
  size: number;
  text: string;
  truncated: boolean;
  rawTextLength: number;
  strategy: SessionDocument['strategy'];
  uploadedAt: string;
}

export interface ChatSession {
  id: string;
  user_id?: string;
  project_id?: string | null;
  initial_message: string;
  created_at: string;
  utm?: UTMData | null;
  document_type?: string;
}

export interface UTMData {
  utm_source?: string;
  utm_medium?: string;
  utm_campaign?: string;
  utm_term?: string;
  utm_content?: string;
  landing_type?: string;
}

/**
 * Метаданные ответа AI для логирования и аналитики
 */
export interface AIResponseMetadata {
  /** Использованная модель */
  modelUsed: string;
  /** Произошел ли fallback на другую модель */
  fallbackOccurred: boolean;
  /** Причина fallback (если был) */
  fallbackReason?: string;
  /** Количество частей (chunks) в ответе */
  chunksCount: number;
  /** Общее количество использованных токенов */
  totalTokens: number;
  /** Причина завершения генерации */
  finishReason: string;
  /** Время генерации в миллисекундах */
  responseTimeMs: number;
  /** Использованный провайдер (openrouter или openai) */
  provider?: 'openrouter' | 'openai';
}

/**
 * Доступные модели для выбора пользователем
 */
export type SelectedModel = 'openai' | 'anthropic' | 'gemini' | 'thinking';

/**
 * Провайдер AI (OpenRouter или OpenAI)
 */
export type AIProvider = 'openrouter' | 'openai';

// Supabase database types
export interface Database {
  public: {
    Tables: {
      chat_sessions: {
        Row: {
          id: string;
          user_id: string | null;
          project_id: string | null;
          initial_message: string;
          created_at: string;
          utm: any | null;
          document_type: string | null;
        };
        Insert: {
          id: string;
          user_id?: string | null;
          project_id?: string | null;
          initial_message: string;
          created_at?: string;
          utm?: any | null;
          document_type?: string | null;
        };
        Update: {
          id?: string;
          user_id?: string | null;
          project_id?: string | null;
          initial_message?: string;
          created_at?: string;
          utm?: any | null;
          document_type?: string | null;
        };
      };
      chat_messages: {
        Row: {
          id: string;
          session_id: string;
          role: string;
          content: string;
          attached_document_ids: string[];
          created_at: string;
        };
        Insert: {
          id?: string;
          session_id: string;
          role: string;
          content: string;
          attached_document_ids?: string[];
          created_at?: string;
        };
        Update: {
          id?: string;
          session_id?: string;
          role?: string;
          content?: string;
          attached_document_ids?: string[];
          created_at?: string;
        };
      };
      projects: {
        Row: {
          id: string;
          user_id: string | null;
          name: string;
          slug: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          user_id?: string | null;
          name: string;
          slug?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          user_id?: string | null;
          name?: string;
          slug?: string | null;
          created_at?: string;
          updated_at?: string;
        };
      };
      project_documents: {
        Row: {
          id: string;
          project_id: string;
          name: string;
          mime_type: string;
          size: number;
          text: string;
          truncated: boolean;
          raw_text_length: number;
          strategy: string;
          uploaded_at: string;
          checksum: string | null;
          created_at: string;
          object_key: string | null;
        };
        Insert: {
          id?: string;
          project_id: string;
          name: string;
          mime_type: string;
          size: number;
          text: string;
          truncated: boolean;
          raw_text_length: number;
          strategy: string;
          uploaded_at?: string;
          checksum?: string | null;
          created_at?: string;
          object_key?: string | null;
        };
        Update: {
          id?: string;
          project_id?: string;
          name?: string;
          mime_type?: string;
          size?: number;
          text?: string;
          truncated?: boolean;
          raw_text_length?: number;
          strategy?: string;
          uploaded_at?: string;
          checksum?: string | null;
          created_at?: string;
          object_key?: string | null;
        };
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: Record<string, never>;
    CompositeTypes: Record<string, never>;
  };
}