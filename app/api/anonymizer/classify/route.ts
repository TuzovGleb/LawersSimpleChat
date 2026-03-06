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

const VALID_PII_TYPES = ['фио', 'дата', 'адрес', 'телефон', 'email', 'документ', 'кадастр', 'pii'] as const;

export async function POST(req: NextRequest) {
  try {
    const { words } = await req.json() as { words: string[] };

    if (!Array.isArray(words) || words.length === 0) {
      return NextResponse.json({ error: 'words array is required' }, { status: 400 });
    }

    const ai = await getAIClient();
    if (!ai) {
      return NextResponse.json({ error: 'No AI provider configured' }, { status: 500 });
    }

    const model = ai.isOpenRouter
      ? (process.env.OPENAI_EXTRACTION_MODEL ?? 'openai/gpt-4o-mini')
      : (process.env.OPENAI_EXTRACTION_MODEL ?? 'gpt-4o-mini');

    const wordList = words.slice(0, 50).map((w, i) => `${i + 1}. "${w}"`).join('\n');

    const completion = await ai.client.chat.completions.create({
      model,
      temperature: 0,
      max_tokens: 1000,
      messages: [
        {
          role: 'system',
          content: `You are a PII (Personally Identifiable Information) classifier for Russian legal documents. For each word, determine if it is PII or not.

PII types:
- фио: person's full name, first name, last name, or patronymic
- дата: date or month name (e.g. "января", "марта")
- адрес: address component — city name, street name, settlement, district name
- телефон: phone number
- email: email address
- документ: passport series/number, INN, SNILS, ОГРН, КПП
- кадастр: cadastral number (format XX:XX:XXXXXXX:XXX, e.g. 33:21:020104:584)
- pii: other personal identifier

Important rules:
- Russian city and settlement names (e.g. Вязники, Суздаль, Ковров) are адрес, NOT фио, even if they look like surnames.
- Street names derived from person names (e.g. Ленина, Гагарина) are адрес when used as street names.
- Cadastral numbers must be classified as кадастр, not телефон or документ.
- Legal terms, common words, conjunctions, and procedural language are NOT PII.

Respond ONLY with valid JSON: {"classifications":[{"word":"original_word","classification":"pii" or "not_pii","piiType":"фио|дата|адрес|телефон|email|документ|кадастр|pii"}]}. Only include piiType when classification is "pii".`,
        },
        {
          role: 'user',
          content: `Classify each word as PII or not PII:\n${wordList}`,
        },
      ],
    });

    const raw = completion.choices[0]?.message?.content ?? '';

    const jsonMatch = raw.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      const fallback = words.map((w) => ({ word: w, classification: 'pii' as const, piiType: 'pii' as const }));
      return NextResponse.json({ classifications: fallback });
    }

    const parsed = JSON.parse(jsonMatch[0]) as {
      classifications: { word: string; classification: string; piiType?: string }[];
    };

    const sanitized = parsed.classifications.map((c) => ({
      word: c.word,
      classification: c.classification === 'not_pii' ? 'not_pii' as const : 'pii' as const,
      piiType: c.classification === 'pii'
        ? (VALID_PII_TYPES.includes(c.piiType as any) ? c.piiType : 'pii')
        : undefined,
    }));

    return NextResponse.json({ classifications: sanitized });
  } catch (error) {
    console.error('[anonymizer/classify] Error:', error);
    const message = error instanceof Error ? error.message : 'Unknown error';
    return NextResponse.json({ error: 'Classification failed', details: message }, { status: 500 });
  }
}
