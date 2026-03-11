import { NextRequest, NextResponse } from 'next/server';
import { createOpenRouterClient, isOpenRouterAvailable } from '@/lib/openrouter-client';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

async function getAIClient() {
  if (isOpenRouterAvailable()) {
    const client = createOpenRouterClient();
    if (client) return { client, isOpenRouter: true };
  }
  if (process.env.OPENAI_API_KEY) {
    const OpenAI = (await import('openai')).default;
    return { client: new OpenAI({ apiKey: process.env.OPENAI_API_KEY }), isOpenRouter: false };
  }
  return null;
}

export async function POST(req: NextRequest) {
  try {
    const { spriteDataUri, wordCount } = await req.json() as {
      spriteDataUri: string;
      wordCount: number;
    };

    if (!spriteDataUri || !wordCount) {
      return NextResponse.json({ error: 'spriteDataUri and wordCount are required' }, { status: 400 });
    }

    const ai = await getAIClient();
    if (!ai) {
      return NextResponse.json({ error: 'No AI provider configured' }, { status: 500 });
    }

    const model = ai.isOpenRouter
      ? (process.env.OPENAI_VISION_MODEL ?? 'openai/gpt-4o-mini')
      : (process.env.OPENAI_VISION_MODEL ?? 'gpt-4o-mini');

    const completion = await ai.client.chat.completions.create({
      model,
      temperature: 0,
      max_tokens: 500,
      messages: [
        {
          role: 'system',
          content: `You are a precise OCR assistant. You will see an image containing ${wordCount} numbered word crops labeled [1] through [${wordCount}]. Each crop shows a single word from a document. Transcribe each word exactly as it appears. Respond ONLY with valid JSON in the format: {"words":[{"index":1,"text":"word1"},{"index":2,"text":"word2"},...]}`,
        },
        {
          role: 'user',
          content: [
            {
              type: 'text',
              text: `Transcribe the ${wordCount} numbered word crops in this image. Return JSON only.`,
            },
            {
              type: 'image_url',
              image_url: { url: spriteDataUri },
            },
          ],
        },
      ],
    });

    const raw = completion.choices[0]?.message?.content ?? '';

    const jsonMatch = raw.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return NextResponse.json({ error: 'Failed to parse OCR response', raw }, { status: 502 });
    }

    const parsed = JSON.parse(jsonMatch[0]) as {
      words: { index: number; text: string }[];
    };

    return NextResponse.json({ words: parsed.words });
  } catch (error) {
    console.error('[anonymizer/ocr] Error:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: 'OCR failed', details: message }, { status: 500 });
  }
}
