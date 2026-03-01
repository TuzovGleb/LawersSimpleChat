import type { WordBoundingBox, RecognizedWord, SpriteGroup } from './types';

const MAX_CONCURRENT_OCR = 5;

/**
 * Sends a single sprite to the OCR API and maps results back to word indices.
 */
async function recognizeSprite(
  sprite: SpriteGroup,
  words: WordBoundingBox[],
): Promise<RecognizedWord[]> {
  const response = await fetch('/api/anonymizer/ocr', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      spriteDataUri: sprite.dataUri,
      wordCount: sprite.wordMappings.length,
    }),
  });

  if (!response.ok) {
    console.error('[ocr-service] OCR request failed:', response.status);
    return sprite.wordMappings.map((m) => ({
      bbox: words[m.originalIndex],
      text: '???',
    }));
  }

  const data = await response.json() as {
    words: { index: number; text: string }[];
  };

  const textByPosition = new Map<number, string>();
  for (const w of data.words) {
    textByPosition.set(w.index, w.text);
  }

  return sprite.wordMappings.map((m) => ({
    bbox: words[m.originalIndex],
    text: textByPosition.get(m.spritePosition) ?? '???',
  }));
}

/**
 * Recognizes all sprites with concurrency limiting.
 * Returns RecognizedWord[] sorted by original word index.
 */
export async function recognizeAllSprites(
  sprites: SpriteGroup[],
  words: WordBoundingBox[],
  onProgress?: (completed: number, total: number) => void,
): Promise<RecognizedWord[]> {
  const allResults: RecognizedWord[] = [];
  let completed = 0;

  const queue = [...sprites];
  const running: Promise<void>[] = [];

  async function processNext(): Promise<void> {
    while (queue.length > 0) {
      const sprite = queue.shift()!;
      const results = await recognizeSprite(sprite, words);
      allResults.push(...results);
      completed++;
      onProgress?.(completed, sprites.length);
    }
  }

  const workerCount = Math.min(MAX_CONCURRENT_OCR, sprites.length);
  for (let i = 0; i < workerCount; i++) {
    running.push(processNext());
  }

  await Promise.all(running);

  return allResults.sort((a, b) => a.bbox.index - b.bbox.index);
}
