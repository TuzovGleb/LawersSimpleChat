// Пресеты вставляются в композер как обычный текст пользователя с пропусками
// вида «[номер дела]» — юрист заполняет их перед отправкой. Здесь всё, что
// нужно, чтобы по этим пропускам ходить: композер сам подсвечивает их выделением,
// а не рисует поверх textarea фейковый слой.
//
// Формат намеренно узкий: одна строка, без вложенных скобок. Это отсекает и
// markdown-ссылки `[текст](url)`, и случайные «[1]» в цитатах, которые юрист
// может вставить сам.
const PLACEHOLDER_PATTERN = /\[[^[\]\n]{2,80}\]/g;

export interface PlaceholderRange {
  start: number;
  end: number;
}

/** Все незаполненные пропуски текста, слева направо. */
export function findPlaceholders(text: string): PlaceholderRange[] {
  const ranges: PlaceholderRange[] = [];
  // matchAll на свежем literal: reuse одного regex с /g течёт lastIndex.
  for (const match of text.matchAll(PLACEHOLDER_PATTERN)) {
    if (match.index === undefined) {
      continue;
    }
    ranges.push({ start: match.index, end: match.index + match[0].length });
  }
  return ranges;
}

export function countPlaceholders(text: string): number {
  return findPlaceholders(text).length;
}

/**
 * Следующий пропуск после каретки (или предыдущий, при direction "backward").
 *
 * Возвращает null, когда в нужную сторону пропусков больше нет — тогда вызывающий
 * код обязан отдать Tab браузеру. Без этого «прыжок по пропускам» превратился бы
 * в ловушку фокуса: с клавиатуры было бы не выйти из поля ввода, пока все скобки
 * не заполнены.
 */
export function findAdjacentPlaceholder(
  text: string,
  caret: number,
  direction: "forward" | "backward",
): PlaceholderRange | null {
  const ranges = findPlaceholders(text);
  if (direction === "forward") {
    // Пропуск, внутри которого стоит каретка, считается пройденным: Tab из него
    // ведёт к следующему, а не топчется на месте.
    return ranges.find((range) => range.start > caret) ?? null;
  }
  const previous = ranges.filter((range) => range.end < caret);
  return previous.length > 0 ? previous[previous.length - 1] : null;
}

/**
 * Следующий пропуск по кругу: с последнего Tab возвращает к первому, Shift+Tab
 * с первого — к последнему.
 *
 * В отличие от findAdjacentPlaceholder, отсюда null приходит только когда
 * пропусков нет вовсе. Замыкание намеренное: пока шаблон не заполнен, Tab не
 * должен уводить фокус из поля. Чтобы это не превратилось в ловушку для
 * клавиатуры, у вызывающего кода обязан быть выход — Escape.
 */
export function findCyclicPlaceholder(
  text: string,
  caret: number,
  direction: "forward" | "backward",
): PlaceholderRange | null {
  const ranges = findPlaceholders(text);
  if (ranges.length === 0) {
    return null;
  }
  if (direction === "forward") {
    return ranges.find((range) => range.start > caret) ?? ranges[0];
  }
  const previous = ranges.filter((range) => range.end < caret);
  return previous.length > 0 ? previous[previous.length - 1] : ranges[ranges.length - 1];
}

/** Первый пропуск текста — на него встаёт каретка сразу после вставки пресета. */
export function findFirstPlaceholder(text: string): PlaceholderRange | null {
  return findPlaceholders(text)[0] ?? null;
}

/** «3 поля» — родительный падеж для подсказки под композером. */
export function pluralizeFields(count: number): string {
  const mod100 = count % 100;
  if (mod100 >= 11 && mod100 <= 14) {
    return `${count} полей`;
  }
  switch (count % 10) {
    case 1:
      return `${count} поле`;
    case 2:
    case 3:
    case 4:
      return `${count} поля`;
    default:
      return `${count} полей`;
  }
}
