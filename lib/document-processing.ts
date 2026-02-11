// NOTE: На Cloudflare Edge Runtime Node.js-специфичные библиотеки недоступны
// Используем динамические импорты и fallback через OpenAI API
// Поддерживает OpenRouter (приоритет) и OpenAI (fallback)
// 
import { createOpenRouterClient, isOpenRouterAvailable } from './openrouter-client';

// Функция для получения расширения файла без использования модуля 'path'
function getFileExtension(filename: string): string {
  const lastDot = filename.lastIndexOf('.');
  return lastDot >= 0 ? filename.substring(lastDot).toLowerCase() : '';
}

// Динамический импорт OpenAI
let openaiModule: typeof import('openai') | null = null;
async function getOpenAIModule() {
  if (!openaiModule) {
    openaiModule = await import('openai');
  }
  return openaiModule;
}

async function getAIClient() {
  // Сначала пробуем OpenRouter (если доступен)
  if (isOpenRouterAvailable()) {
    const openRouterClient = createOpenRouterClient();
    if (openRouterClient) {
      return openRouterClient;
    }
  }
  
  // Fallback на прямой OpenAI клиент
  if (process.env.OPENAI_API_KEY) {
    const OpenAI = (await getOpenAIModule()).default;
    return new OpenAI({
      apiKey: process.env.OPENAI_API_KEY,
    });
  }
  
  return null;
}

const MAX_DOCUMENT_TEXT_LENGTH = 18000;
const MIN_TEXT_LENGTH_FOR_SUCCESS = 80;

export type ExtractedDocument = {
  text: string;
  rawTextLength: number;
  truncated: boolean;
  strategy: 'text' | 'pdf' | 'docx' | 'doc' | 'vision' | 'llm-file';
};

export async function extractTextFromDocument(buffer: Buffer, mimeType: string, filename: string): Promise<ExtractedDocument> {
  const extension = getFileExtension(filename);

  if (isPlainText(mimeType, extension)) {
    const text = buffer.toString('utf-8');
    return normalizeResult(text, 'text');
  }

  if (isDocx(mimeType, extension)) {
    const docxResult = await extractDocx(buffer);
    if (docxResult) {
      return normalizeResult(docxResult, 'docx');
    }
    const llmResult = await extractWithFileAttachment(buffer, filename);
    return normalizeResult(llmResult, 'llm-file');
  }

  if (isDoc(mimeType, extension)) {
    const docResult = await extractDoc(buffer);
    if (docResult) {
      return normalizeResult(docResult, 'doc');
    }
    const llmResult = await extractWithFileAttachment(buffer, filename);
    return normalizeResult(llmResult, 'llm-file');
  }

  if (isPdf(mimeType, extension)) {
    const pdfResult = await extractPdf(buffer);
    if (pdfResult && pdfResult.length >= MIN_TEXT_LENGTH_FOR_SUCCESS) {
      return normalizeResult(pdfResult, 'pdf');
    }
    const llmResult = await extractWithFileAttachment(buffer, filename);
    return normalizeResult(llmResult, 'llm-file');
  }

  if (isImage(mimeType, extension)) {
    const visionResult = await extractWithVision(buffer, mimeType, filename);
    return normalizeResult(visionResult, 'vision');
  }

  const llmResult = await extractWithFileAttachment(buffer, filename);
  return normalizeResult(llmResult, 'llm-file');
}

function isPlainText(mimeType: string, extension: string) {
  return mimeType.startsWith('text/') || ['.txt', '.md', '.csv', '.json'].includes(extension);
}

function isDocx(mimeType: string, extension: string) {
  return (
    mimeType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ||
    extension === '.docx'
  );
}

function isDoc(mimeType: string, extension: string) {
  return mimeType === 'application/msword' || extension === '.doc';
}

function isPdf(mimeType: string, extension: string) {
  return mimeType === 'application/pdf' || extension === '.pdf';
}

function isImage(mimeType: string, extension: string) {
  return mimeType.startsWith('image/') || ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic'].includes(extension);
}

async function extractDocx(buffer: Buffer): Promise<string> {
  try {
    const mammoth = await import('mammoth');
    const result = await mammoth.extractRawText({ buffer });
    return (result?.value ?? '').trim();
  } catch {
    // mammoth недоступен на Edge Runtime или ошибка парсинга — fallback через API
    return '';
  }
}

async function extractDoc(_buffer: Buffer) {
  // .doc (бинарный MS Word) — mammoth не поддерживает, оставляем fallback через API
  return '';
}

async function extractPdf(buffer: Buffer): Promise<string> {
  try {
    const pdfParse = (await import('pdf-parse')).default;
    const result = await pdfParse(buffer);
    return (result?.text ?? '').trim();
  } catch {
    // pdf-parse недоступен на Edge Runtime или ошибка парсинга — fallback через API
    return '';
  }
}

async function extractWithVision(buffer: Buffer, mimeType: string, filename: string) {
  const openai = await getAIClient();
  if (!openai) {
    throw new Error('Neither OPENROUTER_API_KEY nor OPENAI_API_KEY is configured');
  }
  
  try {
    const base64 = buffer.toString('base64');
    // Используем модель для OpenRouter или OpenAI в зависимости от клиента
    const model = isOpenRouterAvailable() 
      ? (process.env.OPENAI_VISION_MODEL ?? 'openai/gpt-4o-mini')
      : (process.env.OPENAI_VISION_MODEL ?? 'gpt-4o-mini');
    
    const completion = await openai.chat.completions.create({
      model,
      temperature: 0,
      max_tokens: 2000,
      messages: [
        {
          role: 'system',
          content:
            'You are a precise transcription assistant. Extract all legible text from provided legal document images. Preserve the original wording and paragraph structure when possible.',
        },
        {
          role: 'user',
          content: [
            {
              type: 'text',
              text: `Transcribe the text content from this document image: ${filename}. Return only the text.`,
            },
            {
              type: 'image_url',
              image_url: {
                url: `data:${mimeType};base64,${base64}`,
              },
            },
          ],
        },
      ],
    });

    return extractContentFromCompletion(completion.choices[0].message?.content);
  } catch (apiError: any) {
    console.error('[document-processing] API error in extractWithVision:', {
      filename,
      mimeType,
      error: {
        message: apiError?.message,
        status: apiError?.status,
        code: apiError?.code,
        type: apiError?.type,
      },
    });
    throw new Error(`Vision API error: ${apiError?.message || 'Unknown error'}`);
  }
}

async function extractWithFileAttachment(buffer: Buffer, filename: string) {
  const openai = await getAIClient();
  if (!openai) {
    throw new Error('Neither OPENROUTER_API_KEY nor OPENAI_API_KEY is configured');
  }
  
  const extension = getFileExtension(filename);
  const isPdfFile = extension === '.pdf';
  const isImageFile = isImageFileType(extension);
  
  try {
    // Определяем, используем ли мы OpenRouter
    const isUsingOpenRouter = isOpenRouterAvailable() && 
      (openai.baseURL?.includes('openrouter.ai') || !process.env.OPENAI_API_KEY);
    
    if (isUsingOpenRouter) {
      // OpenRouter подход: используем base64 (OpenRouter не поддерживает files.create)
      const base64 = buffer.toString('base64');
      const mimeType = getMimeTypeFromExtension(extension);
      
      // Определяем контент в зависимости от типа файла
      let content: any[] = [
        {
          type: 'text',
          text: `Read the attached document "${filename}" and return only its textual content.`,
        },
      ];
      
      if (isPdfFile || isImageFile) {
        // Для PDF и изображений используем image_url с base64
        // OpenRouter автоматически обработает PDF
        content.push({
          type: 'image_url',
          image_url: {
            url: `data:${mimeType};base64,${base64}`,
          },
        });
      } else {
        // Для других файлов пробуем передать как текст (если это текстовый файл)
        try {
          const text = buffer.toString('utf-8');
          content.push({
            type: 'text',
            text: `Document content:\n\n${text}`,
          });
        } catch {
          // Если не текстовый, используем base64 в data URI
          content.push({
            type: 'image_url',
            image_url: {
              url: `data:${mimeType};base64,${base64}`,
            },
          });
        }
      }
      
      const model = process.env.OPENAI_EXTRACTION_MODEL ?? 'openai/gpt-4o-mini';
      
      const completion = await openai.chat.completions.create({
        model,
        messages: [
          {
            role: 'system',
            content: 'You are a meticulous legal transcription assistant. Extract the complete plain text from provided documents without adding commentary.',
          },
          {
            role: 'user',
            content: content,
          },
        ],
        temperature: 0,
      });
      
      const text = completion.choices[0]?.message?.content ?? '';
      return text.trim();
    } else {
      // Старый подход для прямого OpenAI (если используется)
      // Пробуем использовать chat.completions с файлом через base64
      const base64 = buffer.toString('base64');
      const mimeType = getMimeTypeFromExtension(extension);
      
      let content: any[] = [
        {
          type: 'text',
          text: `Read the attached document "${filename}" and return only its textual content.`,
        },
      ];
      
      if (isPdfFile || isImageFile) {
        content.push({
          type: 'image_url',
          image_url: {
            url: `data:${mimeType};base64,${base64}`,
          },
        });
      } else {
        try {
          const text = buffer.toString('utf-8');
          content.push({
            type: 'text',
            text: `Document content:\n\n${text}`,
          });
        } catch {
          content.push({
            type: 'image_url',
            image_url: {
              url: `data:${mimeType};base64,${base64}`,
            },
          });
        }
      }
      
      const completion = await openai.chat.completions.create({
        model: process.env.OPENAI_EXTRACTION_MODEL ?? 'gpt-4o-mini',
        messages: [
          {
            role: 'system',
            content: 'You are a meticulous legal transcription assistant. Extract the complete plain text from provided documents without adding commentary.',
          },
          {
            role: 'user',
            content: content,
          },
        ],
        temperature: 0,
      });
      
      const text = completion.choices[0]?.message?.content ?? '';
      return text.trim();
    }
  } catch (apiError: any) {
    console.error('[document-processing] API error in extractWithFileAttachment:', {
      filename,
      extension,
      error: {
        message: apiError?.message,
        status: apiError?.status,
        code: apiError?.code,
        type: apiError?.type,
        stack: apiError?.stack,
      },
    });
    throw new Error(`File extraction API error: ${apiError?.message || 'Unknown error'}`);
  }
}

// Вспомогательные функции
function getMimeTypeFromExtension(extension: string): string {
  const mimeTypes: Record<string, string> = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc': 'application/msword',
    '.txt': 'text/plain',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
  };
  return mimeTypes[extension.toLowerCase()] || 'application/octet-stream';
}

function isImageFileType(extension: string): boolean {
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic'].includes(extension.toLowerCase());
}

function extractContentFromCompletion(content: unknown): string {
  if (!content) return '';
  if (typeof content === 'string') {
    return content.trim();
  }

  if (Array.isArray(content)) {
    return content
      .map((item: any) => {
        if (typeof item === 'string') {
          return item;
        }
        if (item && typeof item === 'object' && 'text' in item) {
          return String(item.text);
        }
        return '';
      })
      .join('\n')
      .trim();
  }

  return '';
}

function normalizeResult(rawText: string, strategy: ExtractedDocument['strategy']): ExtractedDocument {
  const cleaned = rawText.replace(/\u0000/g, '').trim();
  const truncated = cleaned.length > MAX_DOCUMENT_TEXT_LENGTH;
  const text = truncated ? cleaned.slice(0, MAX_DOCUMENT_TEXT_LENGTH) : cleaned;
  return {
    text,
    rawTextLength: cleaned.length,
    truncated,
    strategy,
  };
}

// Функция ensureOpenAIKey удалена - теперь используется getAIClient()

