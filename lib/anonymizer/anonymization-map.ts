import type { PIIType, AnonymizationMapData, AnonymizationMapEntry } from './types';

const PLACEHOLDER_LABELS: Record<PIIType, string> = {
  'фио': 'ФИО',
  'дата': 'ДАТА',
  'адрес': 'АДРЕС',
  'телефон': 'ТЕЛЕФОН',
  'email': 'EMAIL',
  'документ': 'ДОКУМЕНТ',
  'кадастр': 'КАДАСТР',
  'pii': 'PII',
};

/**
 * Bidirectional mapping between original PII values and anonymized placeholders.
 * Placeholders use the format [TYPE_N], e.g. [ФИО_1], [ДАТА_2].
 */
export class AnonymizationMap {
  private originalToPlaceholder = new Map<string, string>();
  private placeholderToOriginal = new Map<string, string>();
  private counters: Record<PIIType, number>;

  constructor(data?: AnonymizationMapData) {
    this.counters = { 'фио': 0, 'дата': 0, 'адрес': 0, 'телефон': 0, 'email': 0, 'документ': 0, 'кадастр': 0, 'pii': 0 };

    if (data) {
      this.counters = { ...this.counters, ...data.counters };
      for (const entry of data.entries) {
        this.originalToPlaceholder.set(entry.original, entry.placeholder);
        this.placeholderToOriginal.set(entry.placeholder, entry.original);
      }
    }
  }

  /**
   * Adds a PII value and returns its placeholder.
   * If the same original value was already added, returns the existing placeholder.
   */
  addMapping(original: string, type: PIIType): string {
    const existing = this.originalToPlaceholder.get(original);
    if (existing) return existing;

    this.counters[type]++;
    const label = PLACEHOLDER_LABELS[type];
    const placeholder = `[${label}_${this.counters[type]}]`;

    this.originalToPlaceholder.set(original, placeholder);
    this.placeholderToOriginal.set(placeholder, original);

    return placeholder;
  }

  /** Replaces all known PII originals in text with their placeholders. */
  anonymize(text: string): string {
    let result = text;
    const sortedEntries = [...this.originalToPlaceholder.entries()]
      .sort((a, b) => b[0].length - a[0].length);

    for (const [original, placeholder] of sortedEntries) {
      result = result.split(original).join(placeholder);
    }
    return result;
  }

  /** Replaces all placeholders in text with their original PII values. */
  deanonymize(text: string): string {
    let result = text;
    for (const [placeholder, original] of this.placeholderToOriginal) {
      result = result.split(placeholder).join(original);
    }
    return result;
  }

  get size(): number {
    return this.originalToPlaceholder.size;
  }

  toJSON(): AnonymizationMapData {
    const entries: AnonymizationMapEntry[] = [];
    for (const [original, placeholder] of this.originalToPlaceholder) {
      const typeMatch = placeholder.match(/^\[(.+)_\d+\]$/);
      const label = typeMatch?.[1] ?? 'PII';
      const piiType = (Object.entries(PLACEHOLDER_LABELS).find(([, v]) => v === label)?.[0] ?? 'pii') as PIIType;
      entries.push({ placeholder, original, piiType });
    }
    return { entries, counters: { ...this.counters } };
  }

  static fromJSON(data: AnonymizationMapData): AnonymizationMap {
    return new AnonymizationMap(data);
  }

  /** Merges another map into this one, keeping existing mappings. */
  merge(other: AnonymizationMap): void {
    const otherData = other.toJSON();
    for (const entry of otherData.entries) {
      if (!this.originalToPlaceholder.has(entry.original)) {
        this.addMapping(entry.original, entry.piiType);
      }
    }
  }
}
