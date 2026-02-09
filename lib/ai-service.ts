/**
 * AI Service для работы с несколькими моделями с автоматическим fallback
 * Поддерживает OpenRouter (primary) и OpenAI (fallback)
 */

import OpenAI from 'openai';
import {
  ModelName,
  getModelConfig,
  selectModel,
  getFallbackModels,
  getOpenRouterModelConfig,
} from './model-config';
import { generateWithChunking, ChunkedResponse } from './response-chunker';
import { createOpenRouterClient, isOpenRouterAvailable } from './openrouter-client';
import type { SelectedModel, AIProvider } from './types';

/**
 * Результат генерации ответа
 */
export interface AIResponse {
  /** Текст ответа */
  content: string;
  /** Использованная модель */
  modelUsed: string;
  /** Произошел ли fallback на другую модель */
  fallbackOccurred: boolean;
  /** Причина fallback (если был) */
  fallbackReason?: string;
  /** Количество частей (chunks) */
  chunksCount: number;
  /** Общее количество токенов */
  totalTokens: number;
  /** Причина завершения */
  finishReason: string;
  /** Время генерации в миллисекундах */
  responseTimeMs: number;
  /** Использованный провайдер (openrouter или openai) */
  provider?: AIProvider;
}

/**
 * Ошибки, при которых нужен fallback
 */
const FALLBACK_ERROR_CODES = [
  'rate_limit_exceeded',
  'insufficient_quota',
  'server_error',
  'timeout',
  'model_not_found',
  'invalid_request_error',
];

/**
 * Проверяет, нужен ли fallback для данной ошибки
 */
function shouldFallback(error: any): boolean {
  if (!error) return false;
  
  // Проверяем код ошибки OpenAI
  if (error.code && FALLBACK_ERROR_CODES.includes(error.code)) {
    return true;
  }
  
  // Проверяем тип ошибки
  if (error.type && FALLBACK_ERROR_CODES.includes(error.type)) {
    return true;
  }
  
  // Проверяем статус код HTTP
  if (error.status) {
    const status = parseInt(error.status, 10);
    // 429 = rate limit, 500+ = server errors
    if (status === 429 || status >= 500) {
      return true;
    }
  }
  
  // Проверяем сообщение ошибки
  const errorMessage = error.message?.toLowerCase() || '';
  if (
    errorMessage.includes('rate limit') ||
    errorMessage.includes('quota') ||
    errorMessage.includes('timeout') ||
    errorMessage.includes('server error') ||
    errorMessage.includes('unavailable')
  ) {
    return true;
  }
  
  return false;
}

/**
 * Извлекает читаемое описание ошибки
 */
function getErrorDescription(error: any): string {
  if (error.message) {
    return error.message;
  }
  if (error.code) {
    return error.code;
  }
  if (error.type) {
    return error.type;
  }
  return 'Unknown error';
}

/**
 * Type guard для проверки, что модель выбрана и доступна
 */
function isModelSelected(model: SelectedModel | undefined): model is SelectedModel {
  return model !== undefined;
}

/**
 * Основная функция генерации ответа с автоматическим fallback
 * Пробует OpenRouter сначала (если доступен и выбран), затем fallback на OpenAI
 */
export async function generateAIResponse(
  openai: OpenAI,
  messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[],
  userMessage: string,
  forceModel?: ModelName,
  selectedModel?: SelectedModel
): Promise<AIResponse> {
  const startTime = Date.now();
  
  // Если выбран OpenRouter и он доступен - пробуем сначала его (включая thinking)
  if (isModelSelected(selectedModel) && isOpenRouterAvailable()) {
    const openRouterClient = createOpenRouterClient();
    if (openRouterClient) {
      // Для thinking модели используем OpenRouter с O1
      if (selectedModel === 'thinking') {
        try {
          console.log(`[AI Service] Using Thinking model via OpenRouter (gpt-5)`);
          const result = await generateWithOpenRouter(
            openRouterClient,
            messages,
            selectedModel
          );
          
          const responseTimeMs = Date.now() - startTime;
          console.log(`[AI Service] Success with OpenRouter thinking model`);
          console.log(`[AI Service] Response: ${result.content.length} chars, ${result.chunksCount} chunks, ${result.totalTokens} tokens, ${responseTimeMs}ms`);
          
          return {
            content: result.content,
            modelUsed: 'thinking',
            fallbackOccurred: false,
            chunksCount: result.chunksCount,
            totalTokens: result.totalTokens,
            finishReason: result.finishReason,
            responseTimeMs,
            provider: 'openrouter',
          };
        } catch (error: any) {
          console.error(`[AI Service] OpenRouter thinking model failed:`, error);
          // Для thinking модели OpenRouter - единственный способ (так как прямой OpenAI не работает из России)
          throw error;
        }
      }
      
      // Для модели 'openai' реализуем fallback GPT → Gemini
      if (selectedModel === 'openai') {
        const modelsToTry: Exclude<SelectedModel, 'thinking'>[] = ['openai', 'gemini'];
        let lastError: any = null;
        let fallbackOccurred = false;
        let fallbackReason: string | undefined = undefined;
        
        for (let i = 0; i < modelsToTry.length; i++) {
          const modelToTry = modelsToTry[i];
          try {
            console.log(`[AI Service] Attempting OpenRouter with model: ${modelToTry} (${i + 1}/${modelsToTry.length})`);
            const result = await generateWithOpenRouter(
              openRouterClient,
              messages,
              modelToTry
            );
            
            const responseTimeMs = Date.now() - startTime;
            console.log(`[AI Service] Success with OpenRouter model: ${modelToTry}`);
            console.log(`[AI Service] Response: ${result.content.length} chars, ${result.chunksCount} chunks, ${result.totalTokens} tokens, ${responseTimeMs}ms`);
            
            return {
              content: result.content,
              modelUsed: result.model,
              fallbackOccurred,
              fallbackReason,
              chunksCount: result.chunksCount,
              totalTokens: result.totalTokens,
              finishReason: result.finishReason,
              responseTimeMs,
              provider: 'openrouter',
            };
          } catch (error: any) {
            console.warn(`[AI Service] OpenRouter model ${modelToTry} failed:`, error);
            lastError = error;
            
            if (i < modelsToTry.length - 1 && shouldFallback(error)) {
              fallbackOccurred = true;
              fallbackReason = getErrorDescription(error);
              continue;
            }
            // Если это последняя модель или ошибка не требует fallback - пробуем OpenAI fallback
            break;
          }
        }
        
        // Если все OpenRouter модели провалились, продолжаем к OpenAI fallback
        console.warn(`[AI Service] All OpenRouter models failed, falling back to OpenAI`);
      } else {
        // Для других моделей (anthropic, gemini) используем стандартную логику
        try {
          console.log(`[AI Service] Attempting OpenRouter with model: ${selectedModel}`);
          const result = await generateWithOpenRouter(
            openRouterClient,
            messages,
            selectedModel
          );
          
          const responseTimeMs = Date.now() - startTime;
          console.log(`[AI Service] Success with OpenRouter model: ${selectedModel}`);
          console.log(`[AI Service] Response: ${result.content.length} chars, ${result.chunksCount} chunks, ${result.totalTokens} tokens, ${responseTimeMs}ms`);
          
          return {
            content: result.content,
            modelUsed: result.model,
            fallbackOccurred: false,
            chunksCount: result.chunksCount,
            totalTokens: result.totalTokens,
            finishReason: result.finishReason,
            responseTimeMs,
            provider: 'openrouter',
          };
        } catch (error: any) {
          console.warn(`[AI Service] OpenRouter failed, falling back to OpenAI:`, error);
          // Продолжаем к OpenAI fallback
        }
      }
    }
  }
  
  // Fallback на OpenAI (существующая логика)
  console.log(`[AI Service] Using OpenAI fallback`);
  
  // Выбираем основную модель
  const primaryModel = selectModel(userMessage, forceModel);
  console.log(`[AI Service] Primary model selected: ${primaryModel}`);
  
  // Получаем список моделей для fallback
  const fallbackModels = getFallbackModels(primaryModel);
  console.log(`[AI Service] Fallback chain: ${primaryModel} -> ${fallbackModels.join(' -> ')}`);
  
  // Пробуем модели по очереди
  const modelsToTry: ModelName[] = [primaryModel, ...fallbackModels];
  
  let lastError: any = null;
  let fallbackOccurred = false;
  let fallbackReason: string | undefined = undefined;
  
  for (let i = 0; i < modelsToTry.length; i++) {
    const modelName = modelsToTry[i];
    const config = getModelConfig(modelName);
    
    console.log(`[AI Service] Attempting model: ${modelName} (${i + 1}/${modelsToTry.length})`);
    
    try {
      // Адаптируем messages для моделей с особыми требованиями
      let adaptedMessages = messages;
      
      // O1 модели не поддерживают system messages, нужно конвертировать в developer или user
      if (config.useDeveloperMessage || !config.supportsSystemMessages) {
        adaptedMessages = messages.map(msg => {
          if (msg.role === 'system') {
            // O1 использует developer message вместо system
            return {
              role: 'developer' as any,
              content: msg.content,
            };
          }
          return msg;
        });
        console.log(`[AI Service] Converted system messages to developer messages for O1`);
      }
      
      // Подготовка дополнительных параметров модели
      const additionalParams: any = {};
      
      if (config.reasoningEffort) {
        additionalParams.reasoning_effort = config.reasoningEffort;
      }
      if (config.verbosity) {
        additionalParams.verbosity = config.verbosity;
      }
      if (config.useMaxCompletionTokens) {
        additionalParams.useMaxCompletionTokens = true;
      }
      
      // Генерируем ответ с chunking
      const result: ChunkedResponse = await generateWithChunking(
        openai,
        config.name,
        adaptedMessages,
        config.maxTokens,
        config.temperature,
        additionalParams
      );
      
      const responseTimeMs = Date.now() - startTime;
      
      console.log(`[AI Service] Success with model: ${modelName}`);
      console.log(`[AI Service] Response: ${result.content.length} chars, ${result.chunksCount} chunks, ${result.totalTokens} tokens, ${responseTimeMs}ms`);
      
      return {
        content: result.content,
        modelUsed: modelName,
        fallbackOccurred,
        fallbackReason,
        chunksCount: result.chunksCount,
        totalTokens: result.totalTokens,
        finishReason: result.finishReason,
        responseTimeMs,
        provider: 'openai',
      };
      
    } catch (error: any) {
      console.error(`[AI Service] Error with model ${modelName}:`, error);
      lastError = error;
      
      // Проверяем, нужен ли fallback
      if (i < modelsToTry.length - 1 && shouldFallback(error)) {
        fallbackOccurred = true;
        fallbackReason = getErrorDescription(error);
        console.log(`[AI Service] Falling back to next model. Reason: ${fallbackReason}`);
        continue;
      }
      
      // Если это последняя модель или ошибка не требует fallback - пробрасываем
      throw error;
    }
  }
  
  // Если дошли сюда - все модели упали
  console.error('[AI Service] All models failed');
  throw lastError || new Error('All models failed to generate response');
}

/**
 * Генерирует ответ используя OpenRouter
 */
async function generateWithOpenRouter(
  openRouterClient: OpenAI,
  messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[],
  selectedModel: SelectedModel
): Promise<ChunkedResponse> {
  const config = getOpenRouterModelConfig(selectedModel);
  
  // Адаптируем messages для моделей с особыми требованиями
  let adaptedMessages = messages;
  
  // O1 модели требуют developer message вместо system (но gpt-5 поддерживает system)
  if (config.useDeveloperMessage || !config.supportsSystemMessages) {
    adaptedMessages = messages.map(msg => {
      if (msg.role === 'system') {
        return {
          role: 'developer' as any,
          content: msg.content,
        };
      }
      return msg;
    });
    console.log(`[AI Service] Converted system messages to developer messages`);
  }
  
  // Подготовка дополнительных параметров модели
  const additionalParams: any = {};
  
  if (config.useMaxCompletionTokens) {
    additionalParams.useMaxCompletionTokens = true;
  }
  
  // GPT-5 поддерживает reasoning параметры
  if (config.reasoningEffort) {
    additionalParams.reasoning_effort = config.reasoningEffort;
  }
  if (config.verbosity) {
    additionalParams.verbosity = config.verbosity;
  }
  
  // Добавляем web search параметры для OpenRouter
  if (config.enableWebSearch) {
    additionalParams.enableWebSearch = true;
    additionalParams.webSearchMaxResults = config.webSearchMaxResults || 5;
    console.log(`[AI Service] Web search enabled for OpenRouter: ${additionalParams.webSearchMaxResults} results`);
  }
  
  // Генерируем ответ с chunking
  const result = await generateWithChunking(
    openRouterClient,
    config.name,
    adaptedMessages,
    config.maxTokens,
    config.temperature,
    additionalParams
  );
  
  return result;
}

/**
 * Упрощенная версия для быстрых ответов без chunking (для простых запросов)
 */
export async function generateSimpleResponse(
  openai: OpenAI,
  messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[],
  modelName: ModelName = 'primary'
): Promise<string> {
  const config = getModelConfig(modelName);
  
  const requestParams: any = {
    model: config.name,
    messages,
    temperature: config.temperature ?? 0.7,
  };
  
  // Для моделей с useMaxCompletionTokens используем max_completion_tokens
  if (config.useMaxCompletionTokens) {
    requestParams.max_completion_tokens = config.maxTokens;
  } else {
    requestParams.max_tokens = config.maxTokens;
  }
  
  const completion = await openai.chat.completions.create(requestParams);
  return completion.choices[0].message?.content || '';
}

/**
 * Экспортируем типы для использования в других модулях
 */
export type { ModelName };

