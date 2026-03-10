import { NextRequest, NextResponse } from 'next/server';
import { getSupabase } from '@/lib/supabase';
import { deleteFileFromS3 } from '@/lib/s3-client';

export const runtime = 'nodejs';

function getProjectAndDocumentIds(req: NextRequest) {
  const segments = req.nextUrl.pathname.split('/').filter(Boolean);
  const projectsIndex = segments.lastIndexOf('projects');
  const documentIndex = segments.lastIndexOf('documents');
  const projectId =
    projectsIndex >= 0 && segments.length > projectsIndex + 1 ? segments[projectsIndex + 1] : null;
  const documentId =
    documentIndex >= 0 && segments.length > documentIndex + 1 ? segments[documentIndex + 1] : null;
  return { projectId, documentId };
}

export async function DELETE(req: NextRequest) {
  const { projectId, documentId } = getProjectAndDocumentIds(req);
  if (!projectId || !documentId) {
    return NextResponse.json({ error: 'projectId и documentId обязательны.' }, { status: 400 });
  }
  const body = await req.json().catch(() => ({}));
  const userId = typeof body?.userId === 'string' ? body.userId.trim() : null;

  if (!projectId || !documentId) {
    return NextResponse.json({ error: 'projectId и documentId обязательны.' }, { status: 400 });
  }

  try {
    const supabase = await getSupabase();
    if (userId) {
      const { data: project, error: projectError } = await supabase
        .from('projects')
        .select('id')
        .eq('id', projectId)
        .eq('user_id', userId)
        .maybeSingle();

      if (projectError || !project) {
        return NextResponse.json({ error: 'Проект не найден или нет доступа.' }, { status: 404 });
      }
    }

    // Fetch object_key before deleting from DB
    const { data: docData } = await supabase
      .from('project_documents')
      .select('object_key')
      .eq('id', documentId)
      .eq('project_id', projectId)
      .maybeSingle();

    const { error } = await supabase
      .from('project_documents')
      .delete()
      .eq('id', documentId)
      .eq('project_id', projectId);

    if (error) {
      console.error('[project-document][DELETE] Supabase error:', error);
      return NextResponse.json({ error: 'Не удалось удалить документ.' }, { status: 500 });
    }

    if (docData?.object_key) {
      try {
        await deleteFileFromS3(docData.object_key);
      } catch (s3Error) {
        console.warn('[project-document][DELETE] Failed to delete file from S3:', {
          objectKey: docData.object_key,
          error: s3Error instanceof Error ? s3Error.message : s3Error,
        });
      }
    }

    const now = new Date().toISOString();
    await supabase
      .from('projects')
      .update({ updated_at: now })
      .eq('id', projectId)
      .limit(1);

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[project-document][DELETE] Unexpected error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

