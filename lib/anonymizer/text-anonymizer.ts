import type { PIIType, AnonymizationMapData } from './types';
import { AnonymizationMap } from './anonymization-map';
import { SAFE_WORDS } from './safe-words';

export interface AnonymizedText {
  anonymizedText: string;
  originalText: string;
  map: AnonymizationMapData;
}

export type TextAnonymizationProgress = (
  stage: 'regex' | 'llm' | 'done',
  progress: number,
  message: string,
) => void;

// ── Full-text PII regex patterns ─────────────────────────────────────────────
// Applied to the full text in order (most specific first).
// Lookbehind patterns match only the PII value, preserving the keyword context.

interface TextPIIPattern {
  pattern: RegExp;
  type: PIIType;
}

const TEXT_PII_PATTERNS: TextPIIPattern[] = [
  // Cadastral number: XX:XX:XXXXXXX:XXX (e.g. 33:21:020104:584)
  { pattern: /\d{2}:\d{2}:\d{6,7}:\d{1,6}/g, type: 'кадастр' },
  // SNILS: NNN-NNN-NNN NN
  { pattern: /\b\d{3}-\d{3}-\d{3}\s?\d{2}\b/g, type: 'документ' },
  // Full textual date: "26 января 2022 года" / "26 января 2022"
  {
    pattern: /\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}(?:\s+год[а]?)?\b/gi,
    type: 'дата',
  },
  // Numeric date: DD.MM.YYYY / DD/MM/YYYY / DD-MM-YYYY
  { pattern: /\b\d{2}[./\-]\d{2}[./\-]\d{4}\b/g, type: 'дата' },
  // Phone: Russian +7 or 8 prefix
  { pattern: /(?:\+7|8)[\s()\-]?\d{3}[\s()\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}/g, type: 'телефон' },
  // Email
  { pattern: /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g, type: 'email' },
  // INN with keyword prefix
  { pattern: /(?<=\bИНН\s*:?\s*)\d{10,12}\b/gi, type: 'документ' },
  // Address: city/town name — keeps "город" keyword, replaces only the name
  { pattern: /(?<=\b(?:город|г\.)\s+)[А-ЯЁ][а-яё\-]+/g, type: 'адрес' },
  // Address: street name — keeps street-type keyword
  {
    pattern: /(?<=\b(?:улица|ул\.|проспект|пр-т|переулок|пер\.|бульвар|б-р|набережная|наб\.|шоссе|площадь|пл\.)\s+)[А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)*/g,
    type: 'адрес',
  },
  // Address: house number (e.g. дом 41, д. 3а)
  { pattern: /(?<=\b(?:дом|д\.)\s+)\d+[а-яА-Я]?/gi, type: 'адрес' },
  // Address: apartment number
  { pattern: /(?<=\b(?:квартир[аы]|кв\.)\s+)\d+/gi, type: 'адрес' },
  // Address: corpus/building number
  { pattern: /(?<=\b(?:корпус|корп\.)\s+)[\dа-яА-Я]+/gi, type: 'адрес' },
  // Address: settlement/village name
  {
    pattern: /(?<=\b(?:посёлок|поселок|пос\.|деревня|дер\.|село|сел\.)\s+)[А-ЯЁ][а-яё\-]+/g,
    type: 'адрес',
  },
  // Address: district name
  { pattern: /(?<=\b(?:район|р-н)\s+)[А-ЯЁ][а-яё\-]+/g, type: 'адрес' },
];

// ── LLM classification ────────────────────────────────────────────────────────

const MAX_CONCURRENT_LLM = 10;

async function classifyWithLLM(
  words: string[],
): Promise<Map<string, { classification: 'pii' | 'not_pii'; piiType?: PIIType }>> {
  const results = new Map<string, { classification: 'pii' | 'not_pii'; piiType?: PIIType }>();
  const queue = [...words];
  const running: Promise<void>[] = [];

  async function processNext(): Promise<void> {
    while (queue.length > 0) {
      const batch = queue.splice(0, Math.min(20, queue.length));
      try {
        const response = await fetch('/api/anonymizer/classify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ words: batch }),
        });
        if (!response.ok) {
          batch.forEach((w) => results.set(w, { classification: 'pii', piiType: 'pii' }));
          continue;
        }
        const data = await response.json() as {
          classifications: { word: string; classification: 'pii' | 'not_pii'; piiType?: PIIType }[];
        };
        for (const c of data.classifications) {
          results.set(c.word, { classification: c.classification, piiType: c.piiType });
        }
      } catch {
        batch.forEach((w) => results.set(w, { classification: 'pii', piiType: 'pii' }));
      }
    }
  }

  const workerCount = Math.min(MAX_CONCURRENT_LLM, Math.ceil(words.length / 20));
  for (let i = 0; i < workerCount; i++) running.push(processNext());
  await Promise.all(running);
  return results;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// Placeholder token pattern: [ФИО_1], [АДРЕС_2], etc.
const PLACEHOLDER_RE = /\[[А-ЯЁA-Z]+_\d+\]/g;

function isSafeToken(token: string): boolean {
  const lower = token.toLowerCase().replace(/[.,;:!?()«»"'\-]/g, '');
  if (lower.length <= 1) return true;
  if (/^\d+$/.test(lower)) return true;
  return SAFE_WORDS.has(lower);
}

function applyRegexPatterns(text: string, map: AnonymizationMap): string {
  let result = text;
  for (const { pattern, type } of TEXT_PII_PATTERNS) {
    pattern.lastIndex = 0;
    result = result.replace(pattern, (match) => map.addMapping(match, type));
  }
  return result;
}

async function classifyRemainingWords(text: string, map: AnonymizationMap): Promise<string> {
  // Strip placeholders before tokenizing so their label text isn't sent to LLM
  const cleanedForTokenizing = text.replace(PLACEHOLDER_RE, ' ');
  const tokenRe = /[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]*/g;

  const candidates = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = tokenRe.exec(cleanedForTokenizing)) !== null) {
    if (!isSafeToken(m[0])) candidates.add(m[0]);
  }

  if (candidates.size === 0) return text;

  console.groupCollapsed(`[TextAnonymizer] Отправка в LLM (${candidates.size} слов)`);
  console.log([...candidates].join(', '));
  console.groupEnd();

  const llmResults = await classifyWithLLM([...candidates]);

  let result = text;
  for (const [word, { classification, piiType }] of llmResults.entries()) {
    if (classification !== 'pii' || !piiType) continue;
    const placeholder = map.addMapping(word, piiType);
    // Use Unicode-aware word boundary: no adjacent Cyrillic/Latin letter
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const boundaryRe = new RegExp(`(?<![А-ЯЁа-яёA-Za-z])${escaped}(?![А-ЯЁа-яёA-Za-z])`, 'g');
    result = result.replace(boundaryRe, placeholder);
  }

  return result;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Anonymizes a plain text string.
 * Step 1: applies multiword regex patterns (addresses, dates, cadastral, phones, etc.).
 * Step 2: sends remaining candidate words to LLM for classification (names, etc.).
 */
export async function anonymizeText(
  text: string,
  onProgress?: TextAnonymizationProgress,
): Promise<AnonymizedText> {
  const map = new AnonymizationMap();
  const originalText = text;

  onProgress?.('regex', 0, 'Применение паттернов...');
  let anonymizedText = applyRegexPatterns(text, map);

  console.group('[TextAnonymizer] После regex-прохода');
  console.log(anonymizedText);
  console.groupEnd();

  onProgress?.('llm', 0.5, 'Классификация оставшихся данных...');
  anonymizedText = await classifyRemainingWords(anonymizedText, map);

  console.group('[TextAnonymizer] Итоговый результат');
  console.log('=== Анонимизированный текст ===');
  console.log(anonymizedText);
  console.log('=== Карта замен ===');
  console.table(map.toJSON().entries);
  console.groupEnd();

  onProgress?.('done', 1, `Готово. Анонимизировано ${map.size} элементов.`);

  return { anonymizedText, originalText, map: map.toJSON() };
}
