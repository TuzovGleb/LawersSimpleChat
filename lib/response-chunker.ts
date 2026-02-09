/**
 * Сервис для обработки длинных ответов через chunking (разделение на части)
 */

import OpenAI from 'openai';

/**
 * Максимальное количество попыток получения продолжения
 */
const MAX_CONTINUATION_ATTEMPTS = 5;

/**
 * Результат генерации с поддержкой chunking
 */
export interface ChunkedResponse {
  /** Полный текст ответа (все части объединены) */
  content: string;
  /** Количество частей (chunks) */
  chunksCount: number;
  /** Был ли ответ разделен на части */
  wasChunked: boolean;
  /** Общее количество токенов использовано */
  totalTokens: number;
  /** Причина завершения последнего chunk */
  finishReason: string;
  /** Использованная модель */
  model: string;
}

/**
 * Генерирует ответ с автоматическим chunking при необходимости
 * Работает с OpenAI и OpenRouter клиентами (оба используют OpenAI-compatible API)
 * 
 * @param openai - инстанс OpenAI или OpenRouter клиента
 * @param model - имя модели (для OpenRouter используйте формат provider/model-name)
 * @param messages - массив сообщений для контекста
 * @param maxTokens - максимальное количество токенов для одного ответа
 * @param temperature - температура генерации (undefined если модель не поддерживает)
 * @param additionalParams - дополнительные параметры модели
 * @returns полный ответ с метаданными
 */
export async function generateWithChunking(
  openai: OpenAI,
  model: string,
  messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[],
  maxTokens: number,
  temperature?: number,
  additionalParams?: {
    reasoning_effort?: 'low' | 'medium' | 'high';
    verbosity?: 'low' | 'medium' | 'high';
    useMaxCompletionTokens?: boolean;
    enableWebSearch?: boolean;
    webSearchMaxResults?: number;
  }
): Promise<ChunkedResponse> {
  const chunks: string[] = [];
  let totalTokens = 0;
  let currentMessages = [...messages];
  let finishReason = 'stop';
  let attempts = 0;

  console.log(`[Chunker] Starting generation with model: ${model}, max_tokens: ${maxTokens}`);

  while (attempts < MAX_CONTINUATION_ATTEMPTS) {
    attempts++;
    
    console.log(`[Chunker] Attempt ${attempts}/${MAX_CONTINUATION_ATTEMPTS}`);

    try {
      // Подготовка параметров запроса
      const requestParams: any = {
        model,
        messages: currentMessages,
      };

      // Добавляем temperature только если модель поддерживает
      if (temperature !== undefined) {
        requestParams.temperature = temperature;
      }

      // Для O1 моделей используем max_completion_tokens, для других - max_tokens
      if (additionalParams?.useMaxCompletionTokens) {
        requestParams.max_completion_tokens = maxTokens;
      } else {
        requestParams.max_tokens = maxTokens;
      }
      
      // Добавляем специфичные параметры если есть
      if (additionalParams?.reasoning_effort) {
        requestParams.reasoning_effort = additionalParams.reasoning_effort;
      }
      if (additionalParams?.verbosity) {
        requestParams.verbosity = additionalParams.verbosity;
      }

      // Добавляем web search для OpenRouter
      if (additionalParams?.enableWebSearch) {
        requestParams.plugins = [
          {
            id: 'web',
            max_results: additionalParams.webSearchMaxResults || 5,
          },
        ];
        console.log(`[Chunker] Web search enabled with ${requestParams.plugins[0].max_results} results`);
      }

      // Вызов OpenAI API
      const completion = await openai.chat.completions.create(requestParams);

      const choice = completion.choices[0];
      const content = choice.message?.content || '';
      finishReason = choice.finish_reason || 'stop';

      console.log(`[Chunker] Received chunk ${attempts}: ${content.length} chars, finish_reason: ${finishReason}`);

      // Добавляем полученный chunk
      if (content) {
        chunks.push(content);
      }

      // Учитываем использованные токены
      if (completion.usage) {
        totalTokens += completion.usage.total_tokens;
      }

      // Если ответ не был обрезан - завершаем
      if (finishReason !== 'length') {
        console.log(`[Chunker] Generation completed. Finish reason: ${finishReason}`);
        break;
      }

      // Если ответ был обрезан - запрашиваем продолжение
      console.log(`[Chunker] Response was truncated. Requesting continuation...`);

      // Добавляем полученный ответ в контекст
      currentMessages = [
        ...currentMessages,
        {
          role: 'assistant',
          content,
        },
        {
          role: 'user',
          content: 'Продолжи документ с того места, где остановился. Не повторяй уже написанное, только продолжение.',
        },
      ];

    } catch (error) {
      console.error(`[Chunker] Error in attempt ${attempts}:`, error);
      
      // Если это первая попытка и она упала - пробрасываем ошибку дальше
      if (attempts === 1) {
        throw error;
      }
      
      // Если это не первая попытка - возвращаем то, что уже получили
      console.warn(`[Chunker] Returning partial response after error in continuation`);
      break;
    }
  }

  // Если достигли лимита попыток
  if (attempts >= MAX_CONTINUATION_ATTEMPTS && finishReason === 'length') {
    console.warn(`[Chunker] Reached max continuation attempts (${MAX_CONTINUATION_ATTEMPTS})`);
  }

  const fullContent = chunks.join('');
  
  console.log(`[Chunker] Final result: ${chunks.length} chunks, ${fullContent.length} chars, ${totalTokens} tokens`);

  return {
    content: fullContent,
    chunksCount: chunks.length,
    wasChunked: chunks.length > 1,
    totalTokens,
    finishReason,
    model,
  };
}

/**
 * Проверяет, был ли ответ обрезан
 */
export function wasResponseTruncated(finishReason: string): boolean {
  return finishReason === 'length';
}

/**
 * Оценивает примерное количество токенов в тексте
 * (грубая оценка: 1 токен ≈ 0.75 слова для русского языка)
 */
export function estimateTokens(text: string): number {
  const words = text.split(/\s+/).length;
  return Math.ceil(words / 0.75);
}

/**
 * Проверяет, нужен ли chunking для данного запроса
 * (на основе анализа запрошенной длины ответа)
 */
export function shouldUseChunking(userMessage: string): boolean {
  const messageLower = userMessage.toLowerCase();
  
  // Ищем указания на большой объем текста
  const volumePatterns = [
    /(\d+)\s*(слов|слова|word|words)/i,
    /(\d+)\s*(тысяч|thousand|k)\s*(слов|слова|word|words)/i,
    /(\d+)\s*(токен|токена|токенов|token|tokens)/i,
    /(большой|длинный|подробный|детальный|глубокий)\s*(текст|ответ|документ|анализ)/i,
    /не\s*менее\s*(\d+)/i,
  ];
  
  for (const pattern of volumePatterns) {
    const match = messageLower.match(pattern);
    if (match) {
      // Если есть число в запросе
      if (match[1]) {
        const number = parseInt(match[1], 10);
        // Если запрашивается больше 3000 слов/токенов
        if (number >= 3000) {
          return true;
        }
      } else {
        // Если просто есть указание на большой объем
        return true;
      }
    }
  }
  
  return false;
}

