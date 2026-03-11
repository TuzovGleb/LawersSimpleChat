import type { AnonymizedDocument, AnonymizationProgress, RecognizedWord } from './types';
import { detectWords } from './word-detector';
import { buildSprites } from './sprite-builder';
import { recognizeAllSprites } from './ocr-service';
import { anonymizeText } from './text-anonymizer';

/**
 * Anonymizes an image document.
 * Pipeline: word detection → sprite OCR → assemble full text → text anonymization.
 */
export async function anonymizeDocument(
  imageFile: File,
  onProgress?: (progress: AnonymizationProgress) => void,
): Promise<AnonymizedDocument> {
  const report = (stage: AnonymizationProgress['stage'], progress: number, message: string) =>
    onProgress?.({ stage, progress, message });

  report('loading', 0, 'Загрузка изображения...');
  const image = await loadImage(imageFile);

  report('detecting', 0.1, 'Поиск слов на изображении...');
  const words = await detectWords(image);

  if (words.length === 0) {
    return { anonymousText: '', originalText: '', map: { entries: [], counters: {} as never } };
  }

  report('building_sprites', 0.2, `Найдено ${words.length} слов. Подготовка изображений...`);
  const sprites = buildSprites(image, words);

  report('ocr', 0.3, `Распознавание текста (${sprites.length} групп)...`);
  const recognized = await recognizeAllSprites(sprites, words, (done, total) => {
    report('ocr', 0.3 + (done / total) * 0.2, `Распознавание: ${done}/${total} групп`);
  });

  const fullText = assembleText(recognized);

  console.group('[Anonymizer] OCR — собранный текст');
  console.log(fullText);
  console.groupEnd();

  report('classifying', 0.5, 'Анонимизация текста...');
  const { anonymizedText, originalText, map } = await anonymizeText(fullText, (stage, progress, message) => {
    report('classifying', 0.5 + progress * 0.4, message);
  });

  report('done', 1, `Готово. Анонимизировано ${map.entries.length} элементов.`);

  return { anonymousText: anonymizedText, originalText, map };
}

/**
 * Anonymizes a plain text document (e.g. from a .txt or .docx file).
 * Skips the image OCR pipeline entirely.
 */
export async function anonymizeTextDocument(
  text: string,
  onProgress?: (progress: AnonymizationProgress) => void,
): Promise<AnonymizedDocument> {
  const report = (stage: AnonymizationProgress['stage'], progress: number, message: string) =>
    onProgress?.({ stage, progress, message });

  report('classifying', 0, 'Анонимизация текста...');

  const { anonymizedText, originalText, map } = await anonymizeText(text, (stage, progress, message) => {
    report('classifying', progress, message);
  });

  report('done', 1, `Готово. Анонимизировано ${map.entries.length} элементов.`);

  return { anonymousText: anonymizedText, originalText, map };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Assembles OCR word results into a single text string, preserving line breaks
 * based on vertical position of bounding boxes.
 */
function assembleText(words: RecognizedWord[]): string {
  const parts: string[] = [];
  let prevLineY: number | null = null;

  for (const word of words) {
    if (word.text === '???' || !word.text.trim()) continue;

    const isNewLine =
      prevLineY !== null && Math.abs(word.bbox.y - prevLineY) > word.bbox.height * 0.5;

    if (isNewLine) {
      parts.push('\n');
    } else if (parts.length > 0) {
      parts.push(' ');
    }

    prevLineY = word.bbox.y;
    parts.push(word.text);
  }

  return parts.join('');
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`Failed to load image: ${file.name}`)); };
    img.src = url;
  });
}
