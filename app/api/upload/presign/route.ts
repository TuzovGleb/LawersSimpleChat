import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { generatePresignedUploadUrl } from '@/lib/s3-client';
import { createClient } from '@/lib/supabase/server';
import { isProjectOwnedBy } from '@/lib/chat-access';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'nodejs';

// Upper bound on a single upload (bytes). Keeps a hostile caller from requesting
// a presigned URL for an arbitrarily huge object.
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024; // 100 MB

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
  const startedAt = Date.now();
  try {
    const body = await req.json();
    // NB: userId is derived from the authenticated session, NOT taken from the
    // request body — a caller must not be able to act on behalf of another user.
    const { filename, mimeType, size, projectId } = body;

    if (!filename || typeof filename !== 'string') {
      return NextResponse.json({ error: 'filename is required' }, { status: 400 });
    }
    if (!mimeType || typeof mimeType !== 'string') {
      return NextResponse.json({ error: 'mimeType is required' }, { status: 400 });
    }
    if (!size || typeof size !== 'number' || size <= 0) {
      return NextResponse.json({ error: 'size must be a positive number' }, { status: 400 });
    }
    if (size > MAX_UPLOAD_BYTES) {
      return NextResponse.json({ error: 'Файл слишком большой.' }, { status: 400 });
    }
    if (!projectId || typeof projectId !== 'string') {
      return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
    }

    // Require an authenticated user who owns the target project.
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    if (!(await isProjectOwnedBy(supabase, projectId, user.id))) {
      return NextResponse.json({ error: 'Проект не найден или нет доступа.' }, { status: 404 });
    }

    if (!ALLOWED_MIME_TYPES.has(mimeType)) {
      return NextResponse.json(
        { error: `Тип файла "${mimeType}" не поддерживается.` },
        { status: 400 },
      );
    }

    // macOS junk (AppleDouble "._*" sidecars, .DS_Store): xattr metadata, not
    // documents. The client filters them too; this catches direct API callers.
    if (filename.startsWith('._') || filename === '.DS_Store') {
      return NextResponse.json(
        { error: `«${filename}» — служебный файл macOS, а не документ.` },
        { status: 400 },
      );
    }

    const sanitizedFilename = filename.replace(/[^a-zA-Z0-9._\-а-яА-ЯёЁ]/g, '_');
    const objectKey = `uploads/${projectId}/${uuidv4()}/${sanitizedFilename}`;

    const uploadUrl = await generatePresignedUploadUrl(objectKey, mimeType);

    logger.info('Presigned upload URL issued', {
      request_id: requestId,
      event: 'presign_issued',
      duration_ms: Date.now() - startedAt,
      project_id: projectId,
      filename,
    });
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
