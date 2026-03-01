import type { AnonymizedDocument, AnonymizationProgress, ClassifiedWord } from './types';
import { detectWords } from './word-detector';
import { buildSprites } from './sprite-builder';
import { recognizeAllSprites } from './ocr-service';
import { classifyWords } from './pii-classifier';
import { AnonymizationMap } from './anonymization-map';

/**
 * Main anonymization orchestrator.
 * Runs the full pipeline: word detection -> sprite OCR -> PII classification -> anonymization.
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
    return {
      anonymousText: '',
      originalText: '',
      map: new AnonymizationMap().toJSON(),
    };
  }

  report('building_sprites', 0.2, `Найдено ${words.length} слов. Подготовка изображений...`);
  const sprites = buildSprites(image, words);

  report('ocr', 0.3, `Распознавание текста (${sprites.length} групп)...`);
  const recognized = await recognizeAllSprites(sprites, words, (done, total) => {
    const progress = 0.3 + (done / total) * 0.3;
    report('ocr', progress, `Распознавание: ${done}/${total} групп`);
  });

  report('classifying', 0.6, 'Классификация персональных данных...');
  const classified = await classifyWords(recognized, (done, total) => {
    const progress = 0.6 + (done / total) * 0.2;
    report('classifying', progress, `Классификация: ${done}/${total} слов`);
  });

  report('anonymizing', 0.8, 'Создание анонимной версии...');
  const { anonymousText, originalText, map } = buildAnonymizedTexts(classified);

  report('done', 1, `Готово. Анонимизировано ${map.size} элементов.`);

  return {
    anonymousText,
    originalText,
    map: map.toJSON(),
  };
}

function buildAnonymizedTexts(classified: ClassifiedWord[]): {
  anonymousText: string;
  originalText: string;
  map: AnonymizationMap;
} {
  const map = new AnonymizationMap();
  const originalParts: string[] = [];
  const anonymousParts: string[] = [];

  let prevLineY: number | null = null;

  for (const word of classified) {
    const isNewLine = prevLineY !== null &&
      Math.abs(word.bbox.y - prevLineY) > word.bbox.height * 0.5;

    if (isNewLine) {
      originalParts.push('\n');
      anonymousParts.push('\n');
    } else if (originalParts.length > 0 && !originalParts[originalParts.length - 1].endsWith('\n')) {
      originalParts.push(' ');
      anonymousParts.push(' ');
    }

    prevLineY = word.bbox.y;

    originalParts.push(word.text);

    if (word.classification === 'pii' && word.piiType) {
      const placeholder = map.addMapping(word.text, word.piiType);
      anonymousParts.push(placeholder);
    } else {
      anonymousParts.push(word.text);
    }
  }

  return {
    originalText: originalParts.join(''),
    anonymousText: anonymousParts.join(''),
    map,
  };
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`Failed to load image: ${file.name}`));
    };
    img.src = url;
  });
}
