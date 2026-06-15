// NOTE: На Cloudflare Edge Runtime Node.js-специфичные библиотеки недоступны
// Используем динамические импорты и fallback через OpenAI API
// Поддерживает OpenRouter (приоритет) и OpenAI (fallback)
// 
import { createOpenRouterClient, isOpenRouterAvailable } from './openrouter-client';
import { OPENROUTER_DOCUMENT_EXTRACTION_MODEL } from './model-config';

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

const MIN_TEXT_LENGTH_FOR_SUCCESS = 80;
const DOCUMENT_EXTRACTION_MODEL_OPENAI = 'gpt-4o-mini';
const DOCUMENT_EXTRACTION_MODEL_OPENROUTER = OPENROUTER_DOCUMENT_EXTRACTION_MODEL;
const DOCUMENT_EXTRACTION_MAX_TOKENS = 16384;

function getDocumentExtractionModel(): string {
  return isOpenRouterAvailable()
    ? DOCUMENT_EXTRACTION_MODEL_OPENROUTER
    : DOCUMENT_EXTRACTION_MODEL_OPENAI;
}

export type ExtractedDocument = {
  text: string;
  rawTextLength: number;
  truncated: boolean;
  strategy: 'text' | 'pdf' | 'docx' | 'doc' | 'vision' | 'llm-file' | 'pdf-pages';
};

// Постраничная обработка: сколько страниц распознаём одновременно.
// Каждый запрос к gemini по странице ~25-30с; параллелизм сокращает общее
// время до «самой медленной страницы», но не перегружает rate limit.
const PDF_PAGE_CONCURRENCY = 5;
const PDF_PAGE_MAX_RETRIES = 2;

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
    // Скан без текстового слоя: режем на страницы и распознаём их параллельно.
    // Это укладывает каждый запрос в таймаут и обходит лимит output-токенов
    // на больших делах. Возвращает null → откатываемся на единый запрос.
    const perPage = await extractPdfPerPage(buffer, filename);
    if (perPage) {
      return normalizeResult(perPage, 'pdf-pages');
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

async function extractDoc(buffer: Buffer): Promise<string> {
  try {
    const WordExtractor = (await import('word-extractor')).default;
    const extractor = new WordExtractor();
    const doc = await extractor.extract(buffer);
    return (doc.getBody() ?? '').trim();
  } catch {
    return '';
  }
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
    const model = getDocumentExtractionModel();

    const completion = await openai.chat.completions.create({
      model,
      temperature: 0,
      max_tokens: DOCUMENT_EXTRACTION_MAX_TOKENS,
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

    return extractContentFromCompletion(completion.choices?.[0]?.message?.content);
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

// Низкоуровневый вызов модели извлечения. Раньше completion.choices[0] падал
// с "Cannot read properties of undefined (reading '0')", маскируя настоящую
// ошибку провайдера (она лежит в теле ответа, а не в choices). Здесь разбираем
// безопасно и логируем реальную причину.
async function runExtractionCompletion(content: any[], context: Record<string, unknown>): Promise<string> {
  const openai = await getAIClient();
  if (!openai) {
    throw new Error('Neither OPENROUTER_API_KEY nor OPENAI_API_KEY is configured');
  }

  let completion: any;
  try {
    completion = await openai.chat.completions.create({
      model: getDocumentExtractionModel(),
      temperature: 0,
      max_tokens: DOCUMENT_EXTRACTION_MAX_TOKENS,
      messages: [
        {
          role: 'system',
          content: 'You are a meticulous legal transcription assistant. Extract the complete plain text from provided documents without adding commentary.',
        },
        { role: 'user', content },
      ],
    });
  } catch (apiError: any) {
    console.error('[document-processing] extraction API call failed:', {
      ...context,
      error: {
        message: apiError?.message,
        status: apiError?.status,
        code: apiError?.code,
        type: apiError?.type,
      },
    });
    throw new Error(`File extraction API error: ${apiError?.message || 'Unknown error'}`);
  }

  const choice = completion?.choices?.[0];
  if (!choice) {
    const providerError = JSON.stringify(completion?.error ?? completion ?? null).slice(0, 1000);
    console.error('[document-processing] extraction returned no choices:', { ...context, providerError });
    throw new Error(`File extraction returned no choices from provider: ${providerError}`);
  }
  return extractContentFromCompletion(choice.message?.content);
}

async function extractWithFileAttachment(buffer: Buffer, filename: string): Promise<string> {
  const extension = getFileExtension(filename);
  const isPdfFile = extension === '.pdf';
  const isImageFile = isImageFileType(extension);
  const base64 = buffer.toString('base64');
  const mimeType = getMimeTypeFromExtension(extension);

  const content: any[] = [
    {
      type: 'text',
      text: `Read the attached document "${filename}" and return only its textual content.`,
    },
  ];

  if (isPdfFile || isImageFile) {
    // PDF и изображения передаём как base64 data URI (OpenRouter обрабатывает сам)
    content.push({ type: 'image_url', image_url: { url: `data:${mimeType};base64,${base64}` } });
  } else {
    // Для прочих файлов пробуем как текст, иначе — base64
    try {
      const text = buffer.toString('utf-8');
      content.push({ type: 'text', text: `Document content:\n\n${text}` });
    } catch {
      content.push({ type: 'image_url', image_url: { url: `data:${mimeType};base64,${base64}` } });
    }
  }

  return runExtractionCompletion(content, { filename, extension });
}

// Параллельный map с ограничением одновременных задач.
async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  async function worker() {
    while (true) {
      const index = cursor++;
      if (index >= items.length) return;
      results[index] = await fn(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

// Распознаём одну страницу PDF через gemini (type:file, как в проверенном эксперименте),
// с несколькими повторами на случай разовой ошибки провайдера.
async function extractSinglePdfPage(
  pageBuffer: Buffer,
  filename: string,
  pageNumber: number,
  totalPages: number,
): Promise<string> {
  const base64 = pageBuffer.toString('base64');
  const content: any[] = [
    {
      type: 'text',
      text: `This is page ${pageNumber} of ${totalPages} of "${filename}". Return ONLY its full textual content verbatim, preserving paragraph structure. No commentary.`,
    },
    {
      type: 'file',
      file: { filename: `${filename}#page-${pageNumber}`, file_data: `data:application/pdf;base64,${base64}` },
    },
  ];

  let lastError: unknown;
  for (let attempt = 0; attempt <= PDF_PAGE_MAX_RETRIES; attempt++) {
    try {
      return await runExtractionCompletion(content, { filename, page: pageNumber, attempt });
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError instanceof Error ? lastError : new Error(`page ${pageNumber} extraction failed`);
}

// Постраничная обработка скана: режем PDF на одностраничные и распознаём их
// параллельно. Возвращает null (→ откат на единый запрос), если постранично
// неприменимо: не OpenRouter, одна страница, не удалось разрезать, или хотя бы
// одна страница не распозналась (чтобы не сохранить документ с дырами).
async function extractPdfPerPage(buffer: Buffer, filename: string): Promise<string | null> {
  // gemini читает страницу PDF нативно только через OpenRouter (type:file).
  // Для прямого OpenAI gpt-4o-mini это не работает — откат на единый запрос.
  if (!isOpenRouterAvailable()) return null;

  let pageBuffers: Buffer[];
  try {
    const { PDFDocument } = await import('pdf-lib');
    const src = await PDFDocument.load(buffer, { ignoreEncryption: true });
    const count = src.getPageCount();
    if (count <= 1) return null; // одну страницу резать нет смысла
    pageBuffers = [];
    for (let i = 0; i < count; i++) {
      const single = await PDFDocument.create();
      const [page] = await single.copyPages(src, [i]);
      single.addPage(page);
      pageBuffers.push(Buffer.from(await single.save()));
    }
  } catch (err) {
    console.error('[document-processing] PDF split failed, fallback to single request:', {
      filename,
      error: err instanceof Error ? err.message : err,
    });
    return null;
  }

  try {
    const pages = await mapWithConcurrency(pageBuffers, PDF_PAGE_CONCURRENCY, (pageBuffer, index) =>
      extractSinglePdfPage(pageBuffer, filename, index + 1, pageBuffers.length),
    );
    return pages.join('\n\n').trim();
  } catch (err) {
    console.error('[document-processing] per-page extraction failed, fallback to single request:', {
      filename,
      pages: pageBuffers.length,
      error: err instanceof Error ? err.message : err,
    });
    return null;
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
  return {
    text: cleaned,
    rawTextLength: cleaned.length,
    truncated: false,
    strategy,
  };
}

// Функция ensureOpenAIKey удалена - теперь используется getAIClient()

