export interface WordBoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
  /** Index in the detection order (top-to-bottom, left-to-right) */
  index: number;
}

export interface RecognizedWord {
  bbox: WordBoundingBox;
  text: string;
}

export type PIIType = 'фио' | 'дата' | 'адрес' | 'телефон' | 'email' | 'документ' | 'кадастр' | 'pii';

export interface SpriteGroup {
  /** Base64 data URI of the sprite image */
  dataUri: string;
  /** Original word indices mapped to their sprite-local position (1-based) */
  wordMappings: { originalIndex: number; spritePosition: number }[];
}

export interface AnonymizedDocument {
  anonymousText: string;
  originalText: string;
  map: AnonymizationMapData;
}

export interface AnonymizationMapEntry {
  placeholder: string;
  original: string;
  piiType: PIIType;
}

export interface AnonymizationMapData {
  entries: AnonymizationMapEntry[];
  counters: Record<PIIType, number>;
}

export type AnonymizationStage =
  | 'loading'
  | 'detecting'
  | 'building_sprites'
  | 'ocr'
  | 'classifying'
  | 'done';

export interface AnonymizationProgress {
  stage: AnonymizationStage;
  /** 0 to 1 */
  progress: number;
  message: string;
}
