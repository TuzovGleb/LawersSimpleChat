import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function slugify(input: string, fallback?: string) {
  const normalized = input
    .normalize("NFKD")
    .toLowerCase()
    .trim()
    .replace(/[\u0300-\u036f]/g, "");

  const slug = normalized
    .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");

  if (slug) {
    return slug;
  }

  return fallback ?? "";
}

/**
 * Выполняет fetch запрос с автоматическим retry при сетевых ошибках
 * Обрабатывает ERR_HTTP2_PING_FAILED, ERR_CONNECTION_RESET и другие сетевые ошибки
 * 
 * @param timeoutMs - таймаут запроса в миллисекундах (по умолчанию 30 секунд)
 */
export async function fetchWithRetry(
  url: string,
  options: RequestInit = {},
  maxRetries: number = 3,
  retryDelay: number = 1000,
  timeoutMs: number = 30000 // 30 секунд по умолчанию
): Promise<Response> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      // Добавляем таймаут для запроса
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

      try {
        // keepalive не используем для запросов с телом (POST/FormData): в Safari — лимит 64 КБ тела,
        // в Firefox — "NetworkError when attempting to fetch resource". Для запросов с телом явно выключаем.
        const fetchOptions: RequestInit = {
          ...options,
          signal: controller.signal,
          keepalive: options.body != null ? false : options.keepalive ?? true,
        };
        const response = await fetch(url, fetchOptions);

        clearTimeout(timeoutId);
        
        // Проверяем, что ответ действительно получен
        // Если статус не в диапазоне 200-299, но это не критично для retry
        if (!response.ok && response.status >= 500) {
          // Серверные ошибки можно повторить
          if (attempt < maxRetries) {
            const delay = retryDelay * Math.pow(2, attempt);
            await new Promise(resolve => setTimeout(resolve, delay));
            continue;
          }
        }
        
        return response;
      } catch (fetchError: any) {
        clearTimeout(timeoutId);
        throw fetchError;
      }
    } catch (error: any) {
      lastError = error;
      
      // Проверяем, является ли это сетевой ошибкой, которую стоит повторить
      const errorMessage = error?.message || '';
      const errorName = error?.name || '';
      const errorCode = error?.code || '';
      
      const isRetryableError = 
        errorMessage.includes('ERR_HTTP2_PING_FAILED') ||
        errorMessage.includes('ERR_CONNECTION_RESET') ||
        errorMessage.includes('ERR_NETWORK') ||
        errorMessage.includes('Failed to fetch') ||
        errorMessage.includes('NetworkError') ||
        errorMessage.includes('Load failed') ||
        errorName === 'AbortError' ||
        errorName === 'TypeError' ||
        errorCode === 'ECONNRESET' ||
        errorCode === 'ETIMEDOUT' ||
        errorCode === 'ENOTFOUND';

      // Если это последняя попытка или ошибка не подлежит retry, выбрасываем ошибку
      if (attempt === maxRetries || !isRetryableError) {
        throw error;
      }

      // Экспоненциальная задержка: 1s, 2s, 4s
      const delay = retryDelay * Math.pow(2, attempt);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }

  throw lastError || new Error('Failed to fetch after retries');
}

/**
 * Безопасное чтение JSON из Response с обработкой ошибок соединения
 */
export async function safeJsonResponse<T = any>(response: Response): Promise<T> {
  try {
    const text = await response.text();
    if (!text) {
      throw new Error('Empty response body');
    }
    return JSON.parse(text) as T;
  } catch (error: any) {
    // Если ошибка при чтении, это может быть проблема с соединением
    if (error?.message?.includes('ERR_HTTP2_PING_FAILED') || 
        error?.message?.includes('ERR_CONNECTION_RESET')) {
      throw new Error('Connection error while reading response');
    }
    throw error;
  }
}
