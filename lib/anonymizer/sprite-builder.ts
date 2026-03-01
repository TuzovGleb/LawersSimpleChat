import type { WordBoundingBox, SpriteGroup } from './types';

const DEFAULT_GROUP_SIZE = 5;
const SPRITE_PADDING = 16;
const LABEL_HEIGHT = 18;
const WORD_GAP = 12;

/**
 * Builds sprite images from word crops, shuffled and grouped randomly
 * to prevent LLM from inferring document structure.
 */
export function buildSprites(
  image: HTMLImageElement,
  words: WordBoundingBox[],
  groupSize: number = DEFAULT_GROUP_SIZE,
): SpriteGroup[] {
  const shuffledIndices = fisherYatesShuffle(words.map((_, i) => i));
  const groups = chunkArray(shuffledIndices, groupSize);
  const srcCanvas = createSourceCanvas(image);

  return groups.map((group) => buildSingleSprite(srcCanvas, words, group));
}

function createSourceCanvas(image: HTMLImageElement): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(image, 0, 0);
  return canvas;
}

function buildSingleSprite(
  srcCanvas: HTMLCanvasElement,
  words: WordBoundingBox[],
  groupIndices: number[],
): SpriteGroup {
  const crops = groupIndices.map((idx) => words[idx]);

  const totalHeight =
    crops.reduce((sum, crop) => sum + crop.height + LABEL_HEIGHT + WORD_GAP, 0) +
    SPRITE_PADDING * 2 -
    WORD_GAP;
  const maxWidth =
    Math.max(...crops.map((c) => c.width)) + SPRITE_PADDING * 2;

  const spriteCanvas = document.createElement('canvas');
  spriteCanvas.width = maxWidth;
  spriteCanvas.height = totalHeight;
  const ctx = spriteCanvas.getContext('2d')!;

  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, spriteCanvas.width, spriteCanvas.height);

  let yOffset = SPRITE_PADDING;

  const wordMappings: SpriteGroup['wordMappings'] = [];

  crops.forEach((crop, localIdx) => {
    const spritePosition = localIdx + 1;

    ctx.fillStyle = '#333333';
    ctx.font = 'bold 14px monospace';
    ctx.fillText(`[${spritePosition}]`, SPRITE_PADDING, yOffset + 14);
    yOffset += LABEL_HEIGHT;

    const srcCtx = srcCanvas.getContext('2d')!;
    const cropData = srcCtx.getImageData(crop.x, crop.y, crop.width, crop.height);
    ctx.putImageData(cropData, SPRITE_PADDING, yOffset);

    yOffset += crop.height + WORD_GAP;

    wordMappings.push({
      originalIndex: groupIndices[localIdx],
      spritePosition,
    });
  });

  return {
    dataUri: spriteCanvas.toDataURL('image/png'),
    wordMappings,
  };
}

function fisherYatesShuffle<T>(arr: T[]): T[] {
  const result = [...arr];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

function chunkArray<T>(arr: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < arr.length; i += size) {
    chunks.push(arr.slice(i, i + size));
  }
  return chunks;
}
