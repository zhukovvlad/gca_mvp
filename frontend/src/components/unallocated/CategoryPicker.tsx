import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import type { ProjectPassportCategoryOption } from "@/types/domain";

/**
 * Выбор статьи — `Command` внутри `Popover`, по `category_options`, с
 * НЕОБЯЗАТЕЛЬНЫМ полем «Заметка» (спека этапного разноса §2.7, §2.4; план,
 * задача 10). Выделен из панели-верстака паспорта (`UnallocatedPanel.tsx`) —
 * там `noteField: false`, и поведение по-сметного маршрута не меняется ничем
 * наблюдаемым: по-сметный маршрут заметку с экрана не принимает.
 *
 * Триггер НЕ несёт `role="combobox"` (в отличие от `EntityCombobox`): на
 * одной панели таких кнопок много, по одной на раздел, и общая
 * accessible-роль сделала бы их неотличимыми друг от друга для `getByRole`.
 * `data-testid` различает их ТОЛЬКО в тестах — для скринридера различает
 * именно `aria-label` ниже, с номером и названием раздела (finding I-3
 * ревью панели паспорта).
 */
export interface CategoryPickerProps {
  /** Хвост testid/aria — `testId(section)` вызывающей стороны. */
  testKey: string;
  /** Раздел, на который действует кнопка — идёт в `aria-label`. */
  label: string;
  options: ProjectPassportCategoryOption[];
  disabled?: boolean;
  /**
   * `false` — поля «Заметка» нет вовсе, выбор шлёт `onPick(option, null)`.
   * Иначе — РАЗЛИЧНЫЕ существующие заметки офер-смет раунда (`null` —
   * «без заметки» — тоже отдельное значение): 0/1 — поле предзаполнено (или
   * пусто) и пункты доступны сразу; ≥2 — пункты недоступны, пока пользователь
   * не выберет одну из них явно (кнопкой, включая «без заметки»).
   */
  noteField: false | { existingNotes: (string | null)[] };
  onPick: (option: ProjectPassportCategoryOption, note: string | null) => void;
}

/** Различные значения списка заметок, `null` — законное отдельное значение,
 *  порядок первого появления (тот же приём, что у `Classification.notes`
 *  бэкенда — `services/round_unallocated.py`). */
function distinctNotes(notes: (string | null)[]): (string | null)[] {
  const seen: (string | null)[] = [];
  for (const note of notes) {
    if (!seen.includes(note)) seen.push(note);
  }
  return seen;
}

/** Подпись кнопки выбора пустого варианта среди различающихся заметок. */
const NO_NOTE_LABEL = "без заметки";

/** Ключ react для кнопки «без заметки» — `null` не годится буквально (не
 *  строка), а индекс массива был найденной ревью хрупкостью: перестановка
 *  различных заметок между рендерами (тот же набор, другой порядок обхода)
 *  переиспользовала бы DOM-узел чужой кнопки. Строка заведомо не совпадает
 *  ни с одной ПОЛЬЗОВАТЕЛЬСКОЙ заметкой — та приходит с сервера текстом без
 *  управляющих символов. */
const NO_NOTE_KEY = "\u0000no-note\u0000";

export function CategoryPicker({
  testKey,
  label,
  options,
  disabled,
  noteField,
  onPick,
}: CategoryPickerProps) {
  const [open, setOpen] = useState(false);

  // Различные существующие заметки и признак неоднозначности пересчитываются
  // из пропсов на каждый рендер — источник истины один, локальное состояние
  // ниже хранит только то, что нельзя вывести из пропсов: текст поля и факт
  // явного выбора при неоднозначности.
  const distinct = noteField ? distinctNotes(noteField.existingNotes) : [];
  const ambiguous = distinct.length >= 2;

  const [noteText, setNoteText] = useState<string>(() =>
    noteField && !ambiguous ? (distinct[0] ?? "") : ""
  );
  // При 0/1 различных заметках выбор доступен сразу; при ≥2 — только после
  // явного нажатия одной из кнопок ниже (§2.7).
  const [resolved, setResolved] = useState<boolean>(() => !ambiguous);

  function resetNoteState() {
    setNoteText(noteField && !ambiguous ? (distinct[0] ?? "") : "");
    setResolved(!ambiguous);
  }

  // Состав различных заметок — по нему, а НЕ по идентичности массива пропсов,
  // отслеживается смена входных данных: перерендер приходит на каждое
  // обновление соседнего раздела, и привязка к массиву сбрасывала бы уже
  // сделанный выбор и набранный текст на ровном месте. `JSON.stringify`
  // однозначен на `(string | null)[]` по построению; список короткий — по
  // одному значению на offer-смету раунда.
  const notesKey = noteField ? JSON.stringify(distinct) : "";
  const [syncedNotesKey, setSyncedNotesKey] = useState(notesKey);
  if (notesKey !== syncedNotesKey) {
    // Данные пришли, пока поповер ОТКРЫТ (замечание внешнего ревью ветки
    // 02.09.2026). `distinct`/`ambiguous` считаются из пропсов на каждый
    // рендер, а `noteText`/`resolved` держались с момента открытия — два
    // производных от одного факта расходились в ОБЕ стороны: набор,
    // ставший неоднозначным, оставлял выбор разрешённым и отправлял прежнюю
    // заметку без явного разрешения конфликта; набор, ставший однозначным,
    // оставлял `resolved` в false, а кнопки-заметки — единственный способ его
    // переключить — при неоднозначности=false уже не рендерятся, то есть
    // пункты залипали недоступными без выхода. Сброс прямо в рендере — тот
    // самый штатный приём React «поправить состояние при смене пропсов»:
    // повторный рендер идёт до коммита, промежуточное состояние на экран не
    // попадает. `useEffect` дал бы кадр с рассогласованной разметкой.
    setSyncedNotesKey(notesKey);
    resetNoteState();
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    // Сброс и на открытии тоже, а не только по смене набора: брошенный
    // черновик обязан исчезнуть даже когда заметки в сметах не менялись.
    if (next) resetNoteState();
  }

  function pick(option: ProjectPassportCategoryOption) {
    const note = noteField ? (noteText.trim() === "" ? null : noteText) : null;
    onPick(option, note);
    setOpen(false);
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={`pick-category-${testKey}`}
            aria-label={`Отнести на статью: ${label}`}
            disabled={disabled}
          >
            Отнести на статью…
          </Button>
        }
      />
      <PopoverContent className="w-80 p-0" align="end">
        {/*
          Справочник — 362 строки (докстрока `ProjectPassportCategoryOption`);
          без поиска список неюзабелен. Источник — `category_options`, а НЕ
          `categories`: `build_tree` прячет вложенные узлы без строк, и без
          `category_options` половина справочника до аналитика не дошла бы.
        */}
        <Command>
          <CommandInput placeholder="Код или название статьи…" />
          <CommandList>
            <CommandEmpty>Ничего не найдено</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={option.id}
                  // Код и название вместе — встроенный фильтр `Command` ищет
                  // подстроку/подпоследовательность именно в `value`, и без
                  // названия здесь поиск по названию не работал бы вовсе.
                  value={`${option.code} ${option.title}`}
                  disabled={noteField ? !resolved : false}
                  onSelect={() => pick(option)}
                >
                  <span className="font-mono text-xs text-fg-tertiary">{option.code}</span>
                  <span className="truncate">{option.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>

        {noteField && (
          <div className="border-t border-border-subtle p-2">
            {ambiguous && (
              <>
                <p className="mb-1.5 text-2xs text-fg-tertiary">
                  Заметки в сметах различаются — выберите, какую оставить:
                </p>
                <div className="mb-1.5 flex flex-wrap gap-1">
                  {distinct.map((note) => (
                    <Button
                      key={note ?? NO_NOTE_KEY}
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setNoteText(note ?? "");
                        setResolved(true);
                      }}
                    >
                      {note === null ? NO_NOTE_LABEL : `«${note}»`}
                    </Button>
                  ))}
                </div>
              </>
            )}
            {/*
              `maxLength` держит тот же предел, что и схема раундового тела
              (`RoundOverridePut.note`, `Field(max_length=2000)`): без него
              длинная заметка уходила бы на сервер и возвращалась 422-тостом
              валидации — отказ, о котором поле не предупредило (находка
              финального ревью ветки).
            */}
            <Textarea
              aria-label="Заметка"
              maxLength={2000}
              value={noteText}
              onChange={(event) => setNoteText(event.target.value)}
            />
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
