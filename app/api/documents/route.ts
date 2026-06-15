import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { extractTextFromDocument } from '@/lib/document-processing';
import { logger, requestIdFrom } from '@/lib/server-logger';

// NOTE: Cloudflare Pages требует Edge Runtime
// Обработка документов автоматически использует fallback через OpenAI API,
// если Node.js-специфичные библиотеки (pdf-parse, mammoth, word-extractor) недоступны
export const runtime = 'edge';

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();
  try {
    const formData = await req.formData();
    const file = formData.get('file');

    if (!(file instanceof File)) {
      return NextResponse.json({ error: 'Файл не найден в запросе.' }, { status: 400 });
    }

    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);
    const mimeType = file.type || 'application/octet-stream';
    const filename = file.name || 'uploaded-document';

    if (buffer.length === 0) {
      return NextResponse.json({ error: 'Пустой файл не может быть обработан.' }, { status: 400 });
    }

    const extraction = await extractTextFromDocument(buffer, mimeType, filename);

    if (!extraction.text) {
      return NextResponse.json(
        { error: 'Не удалось извлечь текст из документа. Попробуйте другой файл или формат.' },
        { status: 422 },
      );
    }

    logger.info('Document text extracted', {
      request_id: requestId,
      event: 'document_text_extracted',
      duration_ms: Date.now() - startedAt,
      filename,
    });
    return NextResponse.json({
      document: {
        id: uuidv4(),
        name: filename,
        mimeType,
        size: file.size,
        text: extraction.text,
        truncated: extraction.truncated,
        rawTextLength: extraction.rawTextLength,
        strategy: extraction.strategy,
        uploadedAt: new Date().toISOString(),
      },
    });
  } catch (error) {
    logger.error('Error extracting document text', {
      request_id: requestId,
      event: 'document_extract_error',
      err: error,
    });
    return NextResponse.json(
      {
        error:
          'Во время обработки документа произошла ошибка. Попробуйте ещё раз или обратитесь в поддержку, если проблема повторяется.',
      },
      { status: 500 },
    );
  }
}

