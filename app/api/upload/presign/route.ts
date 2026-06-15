import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { generatePresignedUploadUrl } from '@/lib/s3-client';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'nodejs';

const ALLOWED_MIME_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
  'text/markdown',
  'text/csv',
  'application/json',
  'application/rtf',
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/bmp',
  'image/heic',
]);

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  try {
    const body = await req.json();
    const { filename, mimeType, size, projectId, userId } = body;

    if (!filename || typeof filename !== 'string') {
      return NextResponse.json({ error: 'filename is required' }, { status: 400 });
    }
    if (!mimeType || typeof mimeType !== 'string') {
      return NextResponse.json({ error: 'mimeType is required' }, { status: 400 });
    }
    if (!size || typeof size !== 'number' || size <= 0) {
      return NextResponse.json({ error: 'size must be a positive number' }, { status: 400 });
    }
    if (!projectId || typeof projectId !== 'string') {
      return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
    }
    if (!userId || typeof userId !== 'string') {
      return NextResponse.json({ error: 'userId is required' }, { status: 400 });
    }

    if (!ALLOWED_MIME_TYPES.has(mimeType)) {
      return NextResponse.json(
        { error: `Тип файла "${mimeType}" не поддерживается.` },
        { status: 400 },
      );
    }

    const sanitizedFilename = filename.replace(/[^a-zA-Z0-9._\-а-яА-ЯёЁ]/g, '_');
    const objectKey = `uploads/${projectId}/${uuidv4()}/${sanitizedFilename}`;

    const uploadUrl = await generatePresignedUploadUrl(objectKey, mimeType);

    return NextResponse.json({ uploadUrl, objectKey });
  } catch (error) {
    logger.error('Error generating presigned URL', {
      request_id: requestId,
      event: 'presign_post_error',
      err: error,
    });
    return NextResponse.json(
      { error: 'Не удалось сгенерировать ссылку для загрузки.' },
      { status: 500 },
    );
  }
}
