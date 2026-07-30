"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  COMMAND_GROUPS,
  COMMAND_PRESETS,
  featuredPresets,
  searchPresets,
  type CommandPreset,
} from "@/lib/command-presets";
import { Paperclip, Search, Zap } from "lucide-react";

// Одна плашка «нужны материалы» на всех поверхностях: карточка в пустом чате и
// строка в списке команд говорят про документы одним и тем же голосом. Когда
// материалы уже приложены, плашка не нужна — требование выполнено, и молчание
// об этом честнее, чем зелёная галочка.
function DocumentsBadge({ hasDocuments }: { hasDocuments: boolean }) {
  if (hasDocuments) {
    return null;
  }
  return (
    <span
      className="inline-flex flex-shrink-0 items-center gap-1 rounded-full bg-[var(--bg-soft)] px-[7px] py-[3px] text-[10.5px] font-semibold uppercase tracking-[0.04em] text-[#4B5563]"
    >
      <Paperclip className="h-[10px] w-[10px]" />
      Нужен документ
    </span>
  );
}

function PresetIcon({
  preset,
  size = 36,
}: {
  preset: CommandPreset;
  size?: number;
}) {
  const Icon = preset.icon;
  return (
    <div
      className="grid flex-shrink-0 place-items-center rounded-[var(--r-btn)] bg-[var(--brand-accent-bg)] text-[var(--brand-accent)]"
      style={{ width: size, height: size }}
    >
      {/* Плитка 36px со штрихом 18px — та же пропорция и тот же вес штриха,
          что у значка документа в панели предпросмотра. */}
      <Icon style={{ width: Math.round(size / 2), height: Math.round(size / 2) }} />
    </div>
  );
}

/**
 * Пустое состояние чата: шесть команд, с которых чаще всего начинают дело.
 * Геометрия карточки взята из макета один в один — она уже живёт на лендинге.
 */
export const PresetCardGrid = memo(function PresetCardGrid({
  hasDocuments,
  disabled,
  onPick,
  onOpenAll,
}: {
  hasDocuments: boolean;
  disabled: boolean;
  onPick: (preset: CommandPreset) => void;
  onOpenAll: () => void;
}) {
  const presets = useMemo(() => featuredPresets(), []);
  if (presets.length === 0) {
    return null;
  }

  return (
    <div className="mt-7">
      {/* Сетка меряется по своей колонке (860px рядом с сайдбаром), а не по
          вьюпорту: вьюпортные брейкпоинты дали бы три карточки там, где места
          на две. Минимум 200px — чтобы на планшете в портрете, где сайдбар уже
          занял 280px, колонок осталось две, а не одна. */}
      <div className="grid auto-rows-fr gap-3 grid-cols-[repeat(auto-fill,minmax(min(200px,100%),1fr))]">
        {presets.map((preset) => (
          <button
            key={preset.id}
            type="button"
            disabled={disabled}
            onClick={() => onPick(preset)}
            className={cn(
              "group flex flex-col gap-2.5 rounded-xl border border-[var(--border-strong)] bg-white p-4 text-left transition-[border-color,transform,box-shadow] duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              disabled
                ? "cursor-not-allowed opacity-55"
                : "hover:-translate-y-0.5 hover:border-[var(--brand-accent)] hover:shadow-[var(--shadow-md)] motion-reduce:transition-none motion-reduce:hover:translate-y-0",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <PresetIcon preset={preset} />
              {preset.requiresDocuments ? (
                <DocumentsBadge hasDocuments={hasDocuments} />
              ) : null}
            </div>
            <h4
              className="m-0 leading-[1.25]"
              style={{
                fontFamily: "var(--font-serif-family)",
                fontSize: 16,
                fontWeight: 500,
                color: "var(--text-primary)",
              }}
            >
              {preset.title}
            </h4>
            <p
              className="m-0 leading-[1.4]"
              style={{ fontSize: 12.5, color: "var(--text-secondary)" }}
            >
              {preset.description}
            </p>
          </button>
        ))}
      </div>
      <div className="mt-4 text-center">
        <button
          type="button"
          onClick={onOpenAll}
          disabled={disabled}
          className={cn(
            // 44px на телефоне: это единственный вход в полный каталог из
            // пустого состояния, промах по нему подставляет чужую команду.
            "inline-flex min-h-11 items-center justify-center rounded-lg px-4 text-sm font-medium text-[var(--brand-accent)] underline-offset-4",
            "hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            "disabled:cursor-not-allowed disabled:opacity-55 md:min-h-0 md:px-2 md:py-1",
          )}
        >
          Все {COMMAND_PRESETS.length} команд →
        </button>
      </div>
    </div>
  );
});

/**
 * Кнопка, открывающая полный каталог. На телефоне — иконка в одном ряду с
 * скрепкой и отправкой: отдельная строка съела бы 52px высоты композера ровно
 * там, где их нет — при открытой клавиатуре.
 */
export const CommandsChip = memo(function CommandsChip({
  disabled,
  compact = false,
  className,
  onClick,
}: {
  disabled: boolean;
  compact?: boolean;
  className?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title="Готовые команды"
      className={cn(
        "inline-flex items-center justify-center gap-1.5 border border-transparent bg-[var(--brand-accent-bg)] text-[13px] font-medium text-[var(--brand-accent)] transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        compact ? "size-11 rounded-[var(--r-btn)]" : "rounded-full px-3 py-[7px]",
        disabled
          ? "cursor-not-allowed opacity-55"
          : "hover:border-[var(--brand-accent)]",
        className,
      )}
    >
      <Zap className="h-4 w-4 md:h-3.5 md:w-3.5" />
      {compact ? <span className="sr-only">Команды</span> : "Команды"}
    </button>
  );
});

/**
 * Полный каталог. Список, а не сетка карточек: восемнадцать команд глазами
 * читаются построчно, и поиск сверху — главный способ до них добраться.
 */
export const CommandPickerDialog = memo(function CommandPickerDialog({
  open,
  hasDocuments,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  hasDocuments: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (preset: CommandPreset) => void;
}) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // Выбор команды уводит фокус в поле ввода — Radix не должен вернуть его на
  // кнопку «Команды» и сбить каретку с первого пропуска.
  const pickedRef = useRef(false);

  const results = useMemo(() => searchPresets(query), [query]);

  // Группы в порядке каталога; пустые после фильтрации не рисуем.
  const sections = useMemo(() => {
    return COMMAND_GROUPS.map((group) => ({
      group,
      presets: results.filter((preset) => preset.group === group.id),
    })).filter((section) => section.presets.length > 0);
  }, [results]);

  // Плоский порядок строк — по нему ходят стрелки, и он обязан совпадать с
  // порядком отрисовки, иначе Enter отправит не ту команду, что подсвечена.
  const flat = useMemo(() => sections.flatMap((section) => section.presets), [sections]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const pick = useCallback(
    (preset: CommandPreset) => {
      pickedRef.current = true;
      onOpenChange(false);
      onPick(preset);
    },
    [onOpenChange, onPick],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      // Обработчик висит на всём диалоге и ловит всплывшие нажатия. Реагируем
      // только на те, что пришли из поля поиска (или от самого диалога, когда
      // на телефоне фокус остался на нём): иначе Enter на «крестике» закрытия
      // подставлял бы подсвеченную команду вместо того, чтобы закрыть окно.
      if (event.target !== searchRef.current && event.target !== contentRef.current) {
        return;
      }
      if (flat.length === 0) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((index) => (index + 1) % flat.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((index) => (index - 1 + flat.length) % flat.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const preset = flat[activeIndex];
        if (preset) {
          pick(preset);
        }
      }
    },
    [activeIndex, flat, pick],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        ref={contentRef}
        onKeyDown={handleKeyDown}
        onOpenAutoFocus={(event) => {
          // Только на телефоне: там софт-клавиатура закрыла бы список, ради
          // которого диалог и открыли. На планшете с аппаратной клавиатурой
          // (тоже hover: none) без автофокуса поиск просто не работал бы.
          if (window.matchMedia("(hover: none) and (max-width: 767px)").matches) {
            event.preventDefault();
            contentRef.current?.focus();
          }
        }}
        onCloseAutoFocus={(event) => {
          if (pickedRef.current) {
            pickedRef.current = false;
            event.preventDefault();
          }
        }}
        // Высоту берём из того же источника, что и оболочка приложения: dvh на
        // iOS не реагирует на клавиатуру, и диалог уезжал бы под неё. На
        // телефоне он прижат к верху, с sm — снова по центру, как остальные.
        // sm:rounded-xl обязателен: у базового DialogContent есть свой
        // sm:rounded-lg, и на десктопе он перебил бы радиус карточки.
        className={cn(
          "flex w-[calc(100vw-2rem)] max-w-[720px] flex-col gap-0 overflow-hidden rounded-xl border-[var(--border-strong)] bg-[var(--bg-elevated)] p-0 shadow-[var(--shadow-lg)]",
          "top-2 max-h-[calc(var(--app-h,100dvh)-1rem)] translate-y-0",
          "motion-reduce:animate-none motion-reduce:duration-0",
          "sm:top-[50%] sm:max-h-[85dvh] sm:w-full sm:-translate-y-1/2 sm:rounded-xl",
        )}
      >
        <div
          className="flex-shrink-0 px-5 pb-3 pt-5 sm:px-6"
          style={{ borderBottom: "1px solid var(--border-soft)" }}
        >
          <DialogTitle
            className="pr-8 leading-[1.2] tracking-[-0.012em]"
            style={{
              fontFamily: "var(--font-serif-family)",
              fontSize: 20,
              fontWeight: 500,
              color: "var(--text-primary)",
            }}
          >
            Команды
          </DialogTitle>
          <DialogDescription className="mt-1 text-[13px] text-[var(--text-secondary)]">
            Готовый текст запроса подставится в поле ввода — дополните его своими данными.
          </DialogDescription>
          <div
            className="mt-3.5 flex items-center gap-2 rounded-lg px-3"
            style={{ border: "1px solid var(--border-strong)" }}
          >
            <Search className="h-4 w-4 flex-shrink-0" style={{ color: "var(--text-muted)" }} />
            <input
              ref={searchRef}
              // 16px — иначе iOS зумит страницу при фокусе в поле.
              className="w-full border-0 bg-transparent py-2.5 text-base outline-none placeholder:text-[var(--text-muted)] md:text-[15px]"
              style={{ color: "var(--text-primary)" }}
              placeholder="Поиск команды"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Поиск команды"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={flat.length > 0}
              aria-controls="command-preset-list"
              aria-activedescendant={flat[activeIndex] ? `preset-${flat[activeIndex].id}` : undefined}
            />
          </div>
          {/* Результат фильтрации молча меняется на экране — для скринридера
              его нужно проговорить. */}
          <p className="sr-only" role="status" aria-live="polite">
            {flat.length === 0 ? "Ничего не нашлось" : `Найдено команд: ${flat.length}`}
          </p>
        </div>

        {sections.length === 0 ? (
          <p
            className="px-3 py-10 text-center text-sm"
            style={{ color: "var(--text-secondary)" }}
          >
            Ничего не нашлось. Опишите задачу своими словами в поле ввода.
          </p>
        ) : (
          <div
            ref={listRef}
            id="command-preset-list"
            role="listbox"
            aria-label="Команды"
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2.5 pb-4 pt-1 sm:px-3"
          >
            {sections.map((section) => (
              <div
                key={section.group.id}
                role="group"
                aria-label={section.group.title}
                className="mb-1"
              >
                <div className="px-2.5 pb-1.5 pt-4" aria-hidden>
                  <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                    {section.group.title}
                  </span>
                </div>
                {section.presets.map((preset) => {
                  const index = flat.indexOf(preset);
                  const isActive = index === activeIndex;
                  return (
                    <button
                      key={preset.id}
                      id={`preset-${preset.id}`}
                      type="button"
                      role="option"
                      // Опции вне tab-порядка: фокус обязан оставаться на поле
                      // поиска, иначе Tab уводил бы его на строку, а Enter всё
                      // равно выбирал бы подсвеченную стрелками — другую.
                      tabIndex={-1}
                      aria-selected={isActive}
                      data-active={isActive}
                      onMouseMove={() => setActiveIndex(index)}
                      onClick={() => pick(preset)}
                      className={cn(
                        "flex w-full items-start gap-3 rounded-lg p-2.5 text-left transition-colors",
                        // Активная строка помечается не только фоном: тёплая
                        // подложка даёт всего 1.06:1 к белому, по ней одной
                        // выбор не читается.
                        isActive &&
                          "bg-[var(--bg-soft)] shadow-[inset_3px_0_0_var(--brand-accent)]",
                      )}
                    >
                      <PresetIcon preset={preset} size={34} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span
                            className="truncate"
                            style={{
                              fontFamily: "var(--font-serif-family)",
                              fontSize: 15,
                              fontWeight: 500,
                              color: "var(--text-primary)",
                            }}
                          >
                            {preset.title}
                          </span>
                          {preset.requiresDocuments ? (
                            <DocumentsBadge hasDocuments={hasDocuments} />
                          ) : null}
                        </span>
                        <span
                          className="mt-0.5 block leading-[1.4]"
                          style={{ fontSize: 12.5, color: "var(--text-secondary)" }}
                        >
                          {preset.description}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
});
