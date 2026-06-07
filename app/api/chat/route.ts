import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import type { UTMData } from '@/lib/types';

// Этот route — тонкий прокси к Python-бэкенду (FastAPI + LangGraph).
// Вся логика чата (промпт, сборка контекста, web search, запись в Supabase)
// живёт в backend/. Здесь только: проверка сессии Supabase, форвард запроса с
// общим секретом и passthrough SSE-стрима обратно во фронтенд.
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache',
  Connection: 'keep-alive',
  'X-Accel-Buffering': 'no',
} as const;

function extractUtm(url: URL): UTMData {
  const utm: UTMData = {};
  url.searchParams.forEach((value, key) => {
    if (key.startsWith('utm_')) {
      utm[key as keyof UTMData] = value;
    }
  });
  return utm;
}

export async function POST(req: NextRequest) {
  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    console.error('[chat][proxy] BACKEND_URL is not configured');
    return NextResponse.json(
      { error: 'Chat backend is not configured', details: 'Missing BACKEND_URL' },
      { status: 503 },
    );
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  // Проверяем сессию Supabase и доверяем только серверному userId.
  let userId: string | undefined;
  try {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    userId = user.id;
  } catch (error) {
    console.error('[chat][proxy] Auth check failed:', error);
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const utm = extractUtm(new URL(req.url));
  const forwardBody = { ...body, userId, utm };

  try {
    const upstream = await fetch(`${backendUrl.replace(/\/$/, '')}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Backend-Secret': process.env.BACKEND_SHARED_SECRET ?? '',
      },
      body: JSON.stringify(forwardBody),
      // Долгие ответы (reasoning-модели): не закрываем соединение раньше времени.
      signal: AbortSignal.timeout(2100000),
    });

    if (!upstream.ok || !upstream.body) {
      const details = await upstream.text().catch(() => '');
      console.error('[chat][proxy] Backend error:', upstream.status, details);
      return NextResponse.json(
        { error: 'Backend error', details: details || `HTTP ${upstream.status}` },
        { status: 502 },
      );
    }

    return new Response(upstream.body, { headers: SSE_HEADERS });
  } catch (error) {
    const details = error instanceof Error ? error.message : 'Unknown error';
    console.error('[chat][proxy] Failed to reach backend:', details);
    return NextResponse.json({ error: 'Failed to reach chat backend', details }, { status: 502 });
  }
}
