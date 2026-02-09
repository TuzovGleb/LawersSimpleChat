/**
 * Конфигурация моделей AI с приоритизацией и параметрами
 */

import type { SelectedModel } from './types';

export type ModelName = 'primary' | 'reasoning' | 'fallback';

export interface ModelConfig {
  /** Имя модели для OpenAI API */
  name: string;
  /** Максимальное количество токенов для ответа */
  maxTokens: number;
  /** Максимальное контекстное окно (токенов) */
  contextWindow: number;
  /** Температура (0-2), undefined если модель не поддерживает */
  temperature?: number;
  /** Параметр для O1 моделей: max_completion_tokens вместо max_tokens */
  useMaxCompletionTokens?: boolean;
  /** Усилие reasoning (только для моделей с поддержкой) */
  reasoningEffort?: 'low' | 'medium' | 'high';
  /** Уровень детализации (только для моделей с поддержкой) */
  verbosity?: 'low' | 'medium' | 'high';
  /** Поддерживает ли system messages */
  supportsSystemMessages: boolean;
  /** Использовать developer message вместо system (для O1) */
  useDeveloperMessage?: boolean;
  /** Приоритет (чем меньше число, тем выше приоритет) */
  priority: number;
  /** Описание модели */
  description: string;
  /** Включить web search (только для OpenRouter) */
  enableWebSearch?: boolean;
  /** Количество результатов поиска (1-10, только для OpenRouter с enableWebSearch) */
  webSearchMaxResults?: number;
}

/**
 * Конфигурация всех доступных моделей для прямого OpenAI API
 */
export const MODEL_CONFIGS: Record<ModelName, ModelConfig> = {
  'primary': {
    name: 'gpt-5',
    maxTokens: 32000,
    contextWindow: 400000,
    temperature: undefined,
    useMaxCompletionTokens: true,
    reasoningEffort: 'medium',
    verbosity: 'medium',
    supportsSystemMessages: true,
    priority: 1,
    description: 'GPT-5 основная модель с балансом скорости и качества',
  },
  'reasoning': {
    name: 'gpt-5',
    maxTokens: 32000,
    contextWindow: 400000,
    temperature: undefined,
    useMaxCompletionTokens: true,
    reasoningEffort: 'high',
    verbosity: 'high',
    supportsSystemMessages: true,
    priority: 0,
    description: 'GPT-5 режим глубокого анализа для сложных юридических задач',
  },
  'fallback': {
    name: 'gpt-4.1',
    maxTokens: 32000,
    contextWindow: 128000,
    temperature: 0.7,
    supportsSystemMessages: true,
    priority: 2,
    description: 'GPT-4.1 - быстрая и надежная fallback модель',
  },
};

/**
 * Получает конфигурацию модели по имени
 */
export function getModelConfig(modelName: ModelName): ModelConfig {
  return MODEL_CONFIGS[modelName];
}

/**
 * Определяет, какую модель использовать для запроса
 * Всегда используем reasoning модель для максимального качества
 */
export function selectModel(
  userMessage: string,
  forceModel?: ModelName
): ModelName {
  if (forceModel) {
    return forceModel;
  }
  
  return 'reasoning';
}

/**
 * Получает список моделей для fallback в порядке приоритета
 * @param primaryModel - основная модель
 * @returns массив моделей для fallback
 */
export function getFallbackModels(primaryModel: ModelName): ModelName[] {
  const allModels: ModelName[] = ['reasoning', 'primary', 'fallback'];
  
  // Убираем основную модель и сортируем по приоритету
  return allModels
    .filter(m => m !== primaryModel)
    .sort((a, b) => {
      const configA = MODEL_CONFIGS[a];
      const configB = MODEL_CONFIGS[b];
      return configA.priority - configB.priority;
    });
}

/**
 * Конфигурация моделей для OpenRouter
 * OpenRouter использует формат provider/model-name
 */
export const OPENROUTER_MODEL_CONFIGS: Record<SelectedModel, ModelConfig> = {
  'openai': {
    name: 'anthropic/claude-sonnet-4.5',
    maxTokens: 16000,
    contextWindow: 200000,
    temperature: 0.7,
    supportsSystemMessages: true,
    priority: 1,
    description: 'Claude Sonnet 4.5 - быстрая и качественная модель с поиском в интернете',
    enableWebSearch: true,
    webSearchMaxResults: 5,
  },
  'anthropic': {
    name: 'anthropic/claude-opus-4.5',
    maxTokens: 16000,
    contextWindow: 200000,
    temperature: 0.7,
    supportsSystemMessages: true,
    priority: 1,
    description: 'Anthropic Claude Opus 4.5 - последняя модель Anthropic с поиском в интернете',
    enableWebSearch: true,
    webSearchMaxResults: 5,
  },
  'gemini': {
    name: 'google/gemini-2.5-flash',
    maxTokens: 16000,
    contextWindow: 1000000,
    temperature: 0.7,
    supportsSystemMessages: true,
    priority: 1,
    description: 'Google Gemini 2.5 Flash - последняя модель Google с поиском в интернете',
    enableWebSearch: true,
    webSearchMaxResults: 5,
  },
  'thinking': {
    name: 'openai/gpt-5.2',
    maxTokens: 32000,
    contextWindow: 400000,
    temperature: undefined,
    useMaxCompletionTokens: true,
    reasoningEffort: 'high',
    verbosity: 'high',
    supportsSystemMessages: true,
    priority: 0,
    description: 'GPT-5.2 - думающая модель для глубокого анализа через OpenRouter с поиском в интернете',
    enableWebSearch: true,
    webSearchMaxResults: 5,
  },
};

/**
 * Получает конфигурацию модели OpenRouter по выбранной модели
 */
export function getOpenRouterModelConfig(selectedModel: SelectedModel): ModelConfig {
  return OPENROUTER_MODEL_CONFIGS[selectedModel];
}

/**
 * Получает отображаемое название модели для UI
 */
export function getModelDisplayName(selectedModel: SelectedModel): string {
  const displayNames: Record<SelectedModel, string> = {
    'openai': 'Быстрая',
    'anthropic': 'Claude Opus 4.5',
    'gemini': 'Gemini 2.5 Flash',
    'thinking': 'Думающая',
  };
  return displayNames[selectedModel];
}

