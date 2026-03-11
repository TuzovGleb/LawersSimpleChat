import type { WordBoundingBox } from './types';

const MIN_WORD_WIDTH = 6;
const MIN_WORD_HEIGHT = 6;
const MAX_WORD_WIDTH_RATIO = 0.9;
const MAX_WORD_HEIGHT_RATIO = 0.3;
const ADAPTIVE_BLOCK_SIZE = 15;
const ADAPTIVE_C = 10;
const HORIZONTAL_DILATE_RADIUS = 3;
const LINE_MERGE_THRESHOLD_RATIO = 0.5;

/**
 * Detects word-level bounding boxes in a document image using pure canvas operations.
 * Algorithm: grayscale -> adaptive threshold -> dilate -> connected components -> bounding boxes.
 */
export async function detectWords(image: HTMLImageElement): Promise<WordBoundingBox[]> {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  ctx.drawImage(image, 0, 0);

  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const { width, height } = imageData;

  const gray = toGrayscale(imageData);
  const binary = adaptiveThreshold(gray, width, height);
  const dilated = dilateHorizontal(binary, width, height, HORIZONTAL_DILATE_RADIUS);
  const labels = connectedComponents(dilated, width, height);
  const boxes = extractBoundingBoxes(labels, width, height);
  const filtered = filterBoxes(boxes, width, height);
  return sortAndIndex(filtered);
}

function toGrayscale(imageData: ImageData): Uint8Array {
  const { data, width, height } = imageData;
  const gray = new Uint8Array(width * height);
  for (let i = 0; i < gray.length; i++) {
    const offset = i * 4;
    gray[i] = Math.round(0.299 * data[offset] + 0.587 * data[offset + 1] + 0.114 * data[offset + 2]);
  }
  return gray;
}

/**
 * Adaptive mean threshold: pixel is foreground (1) if it's darker than the local mean minus C.
 * Uses integral image for O(1) per-pixel block mean computation.
 */
function adaptiveThreshold(gray: Uint8Array, width: number, height: number): Uint8Array {
  const integral = new Float64Array((width + 1) * (height + 1));
  const iw = width + 1;

  for (let y = 1; y <= height; y++) {
    let rowSum = 0;
    for (let x = 1; x <= width; x++) {
      rowSum += gray[(y - 1) * width + (x - 1)];
      integral[y * iw + x] = rowSum + integral[(y - 1) * iw + x];
    }
  }

  const binary = new Uint8Array(width * height);
  const halfBlock = Math.floor(ADAPTIVE_BLOCK_SIZE / 2);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const y1 = Math.max(0, y - halfBlock);
      const y2 = Math.min(height - 1, y + halfBlock);
      const x1 = Math.max(0, x - halfBlock);
      const x2 = Math.min(width - 1, x + halfBlock);

      const area = (y2 - y1 + 1) * (x2 - x1 + 1);
      const sum =
        integral[(y2 + 1) * iw + (x2 + 1)] -
        integral[y1 * iw + (x2 + 1)] -
        integral[(y2 + 1) * iw + x1] +
        integral[y1 * iw + x1];
      const mean = sum / area;

      binary[y * width + x] = gray[y * width + x] < mean - ADAPTIVE_C ? 1 : 0;
    }
  }

  return binary;
}

/** Horizontal morphological dilation to merge nearby characters into words. */
function dilateHorizontal(binary: Uint8Array, width: number, height: number, radius: number): Uint8Array {
  const result = new Uint8Array(binary.length);

  for (let y = 0; y < height; y++) {
    const rowOffset = y * width;
    for (let x = 0; x < width; x++) {
      if (binary[rowOffset + x] === 1) {
        const xStart = Math.max(0, x - radius);
        const xEnd = Math.min(width - 1, x + radius);
        for (let dx = xStart; dx <= xEnd; dx++) {
          result[rowOffset + dx] = 1;
        }
      }
    }
  }

  return result;
}

interface RawBox {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** Two-pass connected component labeling with union-find. */
function connectedComponents(binary: Uint8Array, width: number, height: number): Int32Array {
  const labels = new Int32Array(width * height);
  const parent: number[] = [0];
  let nextLabel = 1;

  function find(x: number): number {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  }

  function union(a: number, b: number): void {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) {
      parent[Math.max(ra, rb)] = Math.min(ra, rb);
    }
  }

  // First pass
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = y * width + x;
      if (binary[idx] === 0) continue;

      const above = y > 0 ? labels[(y - 1) * width + x] : 0;
      const left = x > 0 ? labels[idx - 1] : 0;

      if (above === 0 && left === 0) {
        labels[idx] = nextLabel;
        parent.push(nextLabel);
        nextLabel++;
      } else if (above !== 0 && left === 0) {
        labels[idx] = above;
      } else if (above === 0 && left !== 0) {
        labels[idx] = left;
      } else {
        labels[idx] = Math.min(above, left);
        if (above !== left) {
          union(above, left);
        }
      }
    }
  }

  // Second pass - flatten labels
  for (let i = 0; i < labels.length; i++) {
    if (labels[i] !== 0) {
      labels[i] = find(labels[i]);
    }
  }

  return labels;
}

function extractBoundingBoxes(labels: Int32Array, width: number, height: number): Map<number, RawBox> {
  const boxes = new Map<number, RawBox>();

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const label = labels[y * width + x];
      if (label === 0) continue;

      const box = boxes.get(label);
      if (box) {
        box.minX = Math.min(box.minX, x);
        box.minY = Math.min(box.minY, y);
        box.maxX = Math.max(box.maxX, x);
        box.maxY = Math.max(box.maxY, y);
      } else {
        boxes.set(label, { minX: x, minY: y, maxX: x, maxY: y });
      }
    }
  }

  return boxes;
}

function filterBoxes(boxes: Map<number, RawBox>, imgWidth: number, imgHeight: number): Omit<WordBoundingBox, 'index'>[] {
  const maxW = imgWidth * MAX_WORD_WIDTH_RATIO;
  const maxH = imgHeight * MAX_WORD_HEIGHT_RATIO;
  const result: Omit<WordBoundingBox, 'index'>[] = [];

  for (const box of boxes.values()) {
    const w = box.maxX - box.minX + 1;
    const h = box.maxY - box.minY + 1;
    if (w >= MIN_WORD_WIDTH && h >= MIN_WORD_HEIGHT && w <= maxW && h <= maxH) {
      result.push({ x: box.minX, y: box.minY, width: w, height: h });
    }
  }

  return result;
}

/** Sort bounding boxes in reading order: group into lines, then left-to-right within each line. */
function sortAndIndex(boxes: Omit<WordBoundingBox, 'index'>[]): WordBoundingBox[] {
  if (boxes.length === 0) return [];

  const sorted = [...boxes].sort((a, b) => a.y - b.y);

  const lines: Omit<WordBoundingBox, 'index'>[][] = [];
  let currentLine: Omit<WordBoundingBox, 'index'>[] = [sorted[0]];

  for (let i = 1; i < sorted.length; i++) {
    const prev = currentLine[0];
    const cur = sorted[i];
    const lineHeight = prev.height;
    if (cur.y - prev.y < lineHeight * LINE_MERGE_THRESHOLD_RATIO) {
      currentLine.push(cur);
    } else {
      lines.push(currentLine);
      currentLine = [cur];
    }
  }
  lines.push(currentLine);

  const result: WordBoundingBox[] = [];
  let index = 0;
  for (const line of lines) {
    line.sort((a, b) => a.x - b.x);
    for (const box of line) {
      result.push({ ...box, index });
      index++;
    }
  }

  return result;
}
