import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { getSupabase } from '@/lib/supabase';
import { extractTextFromDocument } from '@/lib/document-processing';
import { mapProjectDocument } from '@/lib/projects';
import { downloadFileFromS3 } from '@/lib/s3-client';

// NOTE: Для Yandex Cloud Serverless Containers используем Node.js runtime
// Node.js runtime обеспечивает лучшую поддержку DNS lookup и внешних API
// Обработка документов использует Node.js-специфичные библиотеки (pdf-parse, mammoth, word-extractor)
export const runtime = 'nodejs';

function getProjectIdFromRequest(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const projectsIndex = segments.lastIndexOf('projects');
  if (projectsIndex >= 0 && segments.length > projectsIndex + 1) {
    return segments[projectsIndex + 1];
  }
  return null;
}

export async function GET(req: NextRequest) {
  const projectId = getProjectIdFromRequest(req);
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  // Validate environment variables before proceeding
  const hasSupabaseUrl = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
  const hasSupabaseKey = !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  
  if (!hasSupabaseUrl || !hasSupabaseKey) {
    const missing = [];
    if (!hasSupabaseUrl) missing.push('NEXT_PUBLIC_SUPABASE_URL');
    if (!hasSupabaseKey) missing.push('NEXT_PUBLIC_SUPABASE_ANON_KEY');
    
    console.error('[project-documents][GET] Missing environment variables:', {
      projectId,
      missing,
      hasSupabaseUrl,
      hasSupabaseKey,
    });
    
    return NextResponse.json(
      { error: 'Server configuration error. Please contact support.' },
      { status: 500 }
    );
  }

  try {
    const supabase = await getSupabase();
    
    if (!supabase) {
      console.error('[project-documents][GET] Failed to create Supabase client:', {
        projectId,
        hasSupabaseUrl,
        hasSupabaseKey,
      });
      return NextResponse.json(
        { error: 'Не удалось подключиться к базе данных.' },
        { status: 500 }
      );
    }

    const { data, error } = await supabase
      .from('project_documents')
      .select('*')
      .eq('project_id', projectId)
      .order('uploaded_at', { ascending: false });

    if (error) {
      console.error('[project-documents][GET] Supabase query error:', {
        projectId,
        error: {
          message: error.message,
          code: error.code,
          details: error.details,
          hint: error.hint,
        },
        stack: error instanceof Error ? error.stack : undefined,
      });
      return NextResponse.json(
        { error: 'Не удалось получить документы проекта.' },
        { status: 500 }
      );
    }

    try {
      const documents = (data ?? []).map(mapProjectDocument);
      return NextResponse.json({ documents });
    } catch (mappingError) {
      console.error('[project-documents][GET] Document mapping error:', {
        projectId,
        dataCount: data?.length ?? 0,
        error: mappingError instanceof Error ? {
          message: mappingError.message,
          stack: mappingError.stack,
        } : mappingError,
      });
      return NextResponse.json(
        { error: 'Ошибка при обработке данных документов.' },
        { status: 500 }
      );
    }
  } catch (error) {
    console.error('[project-documents][GET] Unexpected error:', {
      projectId,
      error: error instanceof Error ? {
        message: error.message,
        stack: error.stack,
        name: error.name,
      } : error,
      hasSupabaseUrl,
      hasSupabaseKey,
    });
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера. Попробуйте позже.' },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  const projectId = getProjectIdFromRequest(req);
  if (!projectId) {
    return NextResponse.json({ error: 'projectId is required' }, { status: 400 });
  }

  // Validate environment variables before proceeding
  const hasSupabaseUrl = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
  const hasSupabaseKey = !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  
  if (!hasSupabaseUrl || !hasSupabaseKey) {
    const missing = [];
    if (!hasSupabaseUrl) missing.push('NEXT_PUBLIC_SUPABASE_URL');
    if (!hasSupabaseKey) missing.push('NEXT_PUBLIC_SUPABASE_ANON_KEY');
    
    console.error('[project-documents][POST] Missing environment variables:', {
      projectId,
      missing,
      hasSupabaseUrl,
      hasSupabaseKey,
    });
    
    return NextResponse.json(
      { error: 'Server configuration error. Please contact support.' },
      { status: 500 }
    );
  }

  try {
    const body = await req.json();
    const { objectKey, filename, mimeType, size, userId } = body;

    if (!objectKey || typeof objectKey !== 'string') {
      return NextResponse.json({ error: 'objectKey is required' }, { status: 400 });
    }
    if (!filename || typeof filename !== 'string') {
      return NextResponse.json({ error: 'filename is required' }, { status: 400 });
    }
    if (!mimeType || typeof mimeType !== 'string') {
      return NextResponse.json({ error: 'mimeType is required' }, { status: 400 });
    }
    if (!size || typeof size !== 'number' || size <= 0) {
      return NextResponse.json({ error: 'size must be a positive number' }, { status: 400 });
    }

    const userIdClean = typeof userId === 'string' ? userId.trim() : null;

    let buffer: Buffer;
    try {
      buffer = await downloadFileFromS3(objectKey);
    } catch (downloadError) {
      console.error('[project-documents][POST] S3 download error:', {
        projectId,
        objectKey,
        error: downloadError instanceof Error ? {
          message: downloadError.message,
          stack: downloadError.stack,
        } : downloadError,
      });
      return NextResponse.json(
        { error: 'Не удалось скачать файл из хранилища.' },
        { status: 500 },
      );
    }

    if (buffer.length === 0) {
      return NextResponse.json({ error: 'Пустой файл не может быть обработан.' }, { status: 400 });
    }

    let extraction;
    try {
      extraction = await extractTextFromDocument(buffer, mimeType, filename);
    } catch (extractionError) {
      console.error('[project-documents][POST] Document extraction error:', {
        projectId,
        filename,
        mimeType,
        fileSize: size,
        error: extractionError instanceof Error ? {
          message: extractionError.message,
          stack: extractionError.stack,
        } : extractionError,
      });
      return NextResponse.json(
        { error: 'Ошибка при извлечении текста из документа.' },
        { status: 500 },
      );
    }

    if (!extraction.text) {
      return NextResponse.json(
        { error: 'Не удалось извлечь текст из документа. Попробуйте другой файл или формат.' },
        { status: 422 },
      );
    }

    const supabase = await getSupabase();

    if (!supabase) {
      console.error('[project-documents][POST] Failed to create Supabase client:', {
        projectId,
        filename,
        hasSupabaseUrl,
        hasSupabaseKey,
      });
      return NextResponse.json(
        { error: 'Не удалось подключиться к базе данных.' },
        { status: 500 },
      );
    }

    const now = new Date().toISOString();

    const newDocument = {
      id: uuidv4(),
      project_id: projectId,
      name: filename,
      mime_type: mimeType,
      size,
      text: extraction.text,
      truncated: extraction.truncated,
      raw_text_length: extraction.rawTextLength,
      strategy: extraction.strategy,
      uploaded_at: now,
      checksum: null,
      created_at: now,
      object_key: objectKey,
    };

    const { data, error } = await supabase
      .from('project_documents')
      .insert([newDocument])
      .select('*')
      .single();

    if (error || !data) {
      console.error('[project-documents][POST] Supabase insert error:', {
        projectId,
        filename,
        error: error ? {
          message: error.message,
          code: error.code,
          details: error.details,
          hint: error.hint,
        } : null,
        hasData: !!data,
      });
      return NextResponse.json(
        { error: 'Не удалось сохранить документ.' },
        { status: 500 },
      );
    }

    // Touch project updated_at
    try {
      let projectUpdate = supabase
        .from('projects')
        .update({ updated_at: now })
        .eq('id', projectId);
      if (userIdClean) {
        projectUpdate = projectUpdate.eq('user_id', userIdClean);
      }
      await projectUpdate;
    } catch (updateError) {
      console.warn('[project-documents][POST] Failed to update project timestamp:', {
        projectId,
        userId: userIdClean,
        error: updateError instanceof Error ? {
          message: updateError.message,
          stack: updateError.stack,
        } : updateError,
      });
    }

    try {
      const mappedDocument = mapProjectDocument(data);
      return NextResponse.json(
        { document: mappedDocument },
        { status: 201 },
      );
    } catch (mappingError) {
      console.error('[project-documents][POST] Document mapping error:', {
        projectId,
        filename,
        error: mappingError instanceof Error ? {
          message: mappingError.message,
          stack: mappingError.stack,
        } : mappingError,
      });
      return NextResponse.json(
        { error: 'Ошибка при обработке данных документа.' },
        { status: 500 },
      );
    }
  } catch (error) {
    console.error('[project-documents][POST] Unexpected error:', {
      projectId,
      error: error instanceof Error ? {
        message: error.message,
        stack: error.stack,
        name: error.name,
      } : error,
      hasSupabaseUrl,
      hasSupabaseKey,
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

