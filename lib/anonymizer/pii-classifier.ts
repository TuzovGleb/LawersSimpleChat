import type { RecognizedWord, ClassifiedWord, PIIType } from './types';

const MAX_CONCURRENT_CLASSIFY = 10;

// --- Definite PII patterns ---

const PII_PATTERNS: { pattern: RegExp; type: PIIType }[] = [
  // Russian phone numbers
  { pattern: /^(\+7|8)[\s()\-]?\d{3}[\s()\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$/, type: 'телефон' },
  { pattern: /^\+?\d[\d\s\-()]{9,15}$/, type: 'телефон' },
  // Email
  { pattern: /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/, type: 'email' },
  // Dates (DD.MM.YYYY, DD/MM/YYYY, DD-MM-YYYY)
  { pattern: /^\d{2}[.\/\-]\d{2}[.\/\-]\d{4}$/, type: 'дата' },
  { pattern: /^\d{4}[.\/\-]\d{2}[.\/\-]\d{2}$/, type: 'дата' },
  // Russian passport (series + number: 4 digits space 6 digits)
  { pattern: /^\d{2}\s?\d{2}\s?\d{6}$/, type: 'документ' },
  // INN (10 or 12 digits)
  { pattern: /^\d{10}$/, type: 'документ' },
  { pattern: /^\d{12}$/, type: 'документ' },
  // SNILS (NNN-NNN-NNN NN)
  { pattern: /^\d{3}-\d{3}-\d{3}\s?\d{2}$/, type: 'документ' },
  // ОГРН (13 digits)
  { pattern: /^\d{13}$/, type: 'документ' },
  // КПП (9 digits)
  { pattern: /^\d{9}$/, type: 'документ' },
];

// --- Definite NOT PII patterns ---

const LEGAL_TERMS = new Set([
  'статья', 'статьи', 'статье', 'статьей', 'статью', 'статей',
  'пункт', 'пункта', 'пункте', 'пунктом', 'пункту',
  'часть', 'части', 'частью',
  'глава', 'главы', 'главе', 'главой',
  'раздел', 'раздела', 'разделе', 'разделом',
  'закон', 'закона', 'законе', 'законом', 'законы',
  'кодекс', 'кодекса', 'кодексе', 'кодексом',
  'суд', 'суда', 'суде', 'судом', 'суду',
  'дело', 'дела', 'делу', 'делом', 'деле',
  'решение', 'решения', 'решению', 'решением',
  'приговор', 'приговора', 'приговору', 'приговором',
  'обвинение', 'обвинения', 'обвинению', 'обвинением',
  'защита', 'защиты', 'защите', 'защитой',
  'истец', 'истца', 'истцу', 'истцом',
  'ответчик', 'ответчика', 'ответчику', 'ответчиком',
  'подсудимый', 'подсудимого', 'подсудимому', 'подсудимым',
  'потерпевший', 'потерпевшего', 'потерпевшему', 'потерпевшим',
  'свидетель', 'свидетеля', 'свидетелю', 'свидетелем',
  'доказательство', 'доказательства', 'доказательству',
  'протокол', 'протокола', 'протоколе', 'протоколом',
  'заявление', 'заявления', 'заявлению', 'заявлением',
  'ходатайство', 'ходатайства', 'ходатайству',
  'постановление', 'постановления', 'постановлению',
  'определение', 'определения', 'определению',
  'апелляция', 'апелляции', 'апелляцией',
  'кассация', 'кассации', 'кассацией',
  'иск', 'иска', 'иску', 'иском',
  'право', 'права', 'правом', 'праву',
  'обязанность', 'обязанности', 'обязанностью',
  'ответственность', 'ответственности',
  'наказание', 'наказания', 'наказанию',
  'штраф', 'штрафа', 'штрафу', 'штрафом',
  'срок', 'срока', 'сроку', 'сроком',
  'документ', 'документа', 'документу', 'документом', 'документы',
  'копия', 'копии', 'копию', 'копией',
  'оригинал', 'оригинала', 'оригиналу',
  'рассмотрение', 'рассмотрения', 'рассмотрению',
  'заседание', 'заседания', 'заседанию',
  'слушание', 'слушания', 'слушанию',
]);

const ABBREVIATIONS = new Set([
  'ст', 'п', 'ч', 'г', 'гг', 'др', 'т.д', 'т.п', 'т.е', 'и.о', 'н.э',
  'ук', 'упк', 'гк', 'гпк', 'кас', 'коап', 'тк', 'жк', 'нк', 'ск', 'бк',
  'рф', 'рсфср', 'ссср',
  'фз', 'фкз',
  'вс', 'кс', 'ас',
  'мвд', 'фсб', 'фсин', 'фссп',
  'ооо', 'ип', 'ао', 'зао', 'оао', 'пао',
  'гос', 'фед', 'рег',
  'руб', 'коп', 'тыс', 'млн', 'млрд',
  'кв', 'км', 'м',
  'ул', 'пр', 'пер', 'наб', 'пл',
  'стр', 'корп', 'лит', 'оф',
  'обл', 'край', 'авт',
  'гор', 'пос', 'дер', 'сел',
]);

const CONJUNCTIONS_PREPOSITIONS = new Set([
  'и', 'в', 'во', 'на', 'по', 'с', 'со', 'к', 'за', 'от', 'до', 'из', 'у', 'о', 'об',
  'а', 'но', 'или', 'ни', 'не', 'ни', 'да', 'же', 'ли', 'бы',
  'что', 'как', 'так', 'при', 'для', 'без', 'под', 'над', 'между',
  'через', 'после', 'перед', 'около', 'среди', 'вместо', 'кроме',
  'если', 'когда', 'чтобы', 'потому', 'поэтому', 'поскольку',
  'также', 'тоже', 'однако', 'хотя', 'либо',
  'этот', 'этого', 'этому', 'этом', 'эта', 'это', 'эти', 'этих',
  'тот', 'того', 'тому', 'том', 'та', 'те', 'тех',
  'свой', 'своя', 'свое', 'свои', 'своего', 'своей', 'своему',
  'его', 'ее', 'её', 'их',
  'быть', 'был', 'была', 'было', 'были', 'будет', 'является',
  'может', 'должен', 'должна', 'должно', 'следует',
  'данный', 'данная', 'данного', 'данной', 'данному',
  'указанный', 'указанного', 'указанной', 'указанному',
  'настоящий', 'настоящего', 'настоящей', 'настоящему',
  'вышеуказанный', 'нижеуказанный', 'нижеследующий',
  'все', 'всех', 'всем', 'каждый', 'любой',
  'один', 'два', 'три', 'четыре', 'пять',
  'первый', 'второй', 'третий',
  'год', 'года', 'году', 'годом', 'лет',
  'день', 'дня', 'дню', 'днем',
  'месяц', 'месяца', 'месяцу',
  'число', 'числа',
  'время', 'времени',
  'место', 'места',
  'лицо', 'лица', 'лицу', 'лицом',
  'сторона', 'стороны', 'стороне', 'сторону',
  'основание', 'основания', 'основании',
  'соответствие', 'соответствии',
  'порядок', 'порядка', 'порядке',
  'случай', 'случая', 'случае',
  'отношение', 'отношения', 'отношении',
]);

function isDefinitelyNotPII(word: string): boolean {
  const lower = word.toLowerCase().replace(/[.,;:!?()«»"'\-]/g, '');

  if (lower.length <= 1) return true;
  if (/^[.,;:!?()«»"'\-\[\]{}\/\\|@#$%^&*+=~`]+$/.test(word)) return true;
  if (LEGAL_TERMS.has(lower)) return true;
  if (ABBREVIATIONS.has(lower.replace(/\./g, ''))) return true;
  if (CONJUNCTIONS_PREPOSITIONS.has(lower)) return true;
  if (/^\d{1,4}$/.test(lower)) return true;
  if (/^№?\d{1,4}$/.test(lower)) return true;

  return false;
}

function matchPIIPattern(word: string): PIIType | null {
  const cleaned = word.replace(/[.,;:!?()«»"']/g, '').trim();
  for (const { pattern, type } of PII_PATTERNS) {
    if (pattern.test(cleaned)) {
      return type;
    }
  }
  return null;
}

async function classifyWithLLM(words: string[]): Promise<Map<string, { classification: 'pii' | 'not_pii'; piiType?: PIIType }>> {
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

  const workerCount = Math.min(MAX_CONCURRENT_CLASSIFY, Math.ceil(words.length / 20));
  for (let i = 0; i < workerCount; i++) {
    running.push(processNext());
  }

  await Promise.all(running);
  return results;
}

/**
 * Classifies all recognized words as PII or not PII.
 * Uses browser-side regex for definite cases and LLM for ambiguous ones.
 */
export async function classifyWords(
  words: RecognizedWord[],
  onProgress?: (completed: number, total: number) => void,
): Promise<ClassifiedWord[]> {
  const results: ClassifiedWord[] = [];
  const ambiguousWords: { word: RecognizedWord; text: string }[] = [];

  for (const word of words) {
    const trimmed = word.text.trim();
    if (!trimmed || trimmed === '???') {
      results.push({ ...word, classification: 'not_pii' });
      continue;
    }

    if (isDefinitelyNotPII(trimmed)) {
      results.push({ ...word, classification: 'not_pii' });
      continue;
    }

    const piiType = matchPIIPattern(trimmed);
    if (piiType) {
      results.push({ ...word, classification: 'pii', piiType });
      continue;
    }

    ambiguousWords.push({ word, text: trimmed });
  }

  if (ambiguousWords.length > 0) {
    const uniqueTexts = [...new Set(ambiguousWords.map((a) => a.text))];
    const llmResults = await classifyWithLLM(uniqueTexts);

    let completed = 0;
    for (const { word, text } of ambiguousWords) {
      const result = llmResults.get(text);
      results.push({
        ...word,
        classification: result?.classification ?? 'pii',
        piiType: result?.piiType,
      });
      completed++;
      onProgress?.(completed, ambiguousWords.length);
    }
  }

  return results.sort((a, b) => a.bbox.index - b.bbox.index);
}
