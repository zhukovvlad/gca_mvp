import { Button } from "@/components/ui/button";
import { coefficientLevel } from "@/lib/inflation";
import { formatDate } from "@/lib/format";
import type { ComparisonInflation } from "@/types/domain";

interface InflationLevelsBarProps {
  inflation: ComparisonInflation;
  /** Кнопка правки — только `admin` (§2.10): `member` ряд читает, но не правит. */
  canEdit: boolean;
  onEdit: () => void;
}

/**
 * Полоса уровней ряда на `/compare` (спека §2.12, DoD 32).
 *
 * Без неё с экрана невозможно понять, из чего вышла поправка: коэффициент колонки
 * виден, а сам ряд нет, и вопрос «откуда 8,3 %» (§1 п. 4 `AGENTS.md`) остаётся без
 * ответа на той поверхности, где человек смотрит числа.
 *
 * **Показывается ПРИМЕЧАНИЕ ряда, а источники по годам — НЕТ.** Три источника рядом
 * с тремя процентами превращают ориентирующую строку в таблицу. Источник года
 * остаётся обязательным в схеме и печатается на листе выгрузки — там он и нужен,
 * потому что артефакт защиты перед банком это файл. Разделение простое: экран
 * ориентирует, файл защищает.
 *
 * **Дата последней правки ряда остаётся:** она несёт компромисс §2.10 — версий у
 * ряда нет, поэтому ссылка не гарантирует исторического результата, и читатель
 * обязан видеть, что ряд с тех пор правили.
 *
 * Полоса не отрисовывается вовсе, пока приведение не сосчитано, — а не скрывается
 * атрибутом `hidden`. В макете `display:flex` перебивал браузерное
 * `[hidden] { display:none }`, и полоса продолжала занимать место бордюром и
 * отступом: `innerText` пуст, глазами почти не видно, а `isHidden()` возвращал
 * `false`. Отсутствующий узел этой ловушки не имеет вовсе.
 *
 * **Своей коробки у полосы НЕТ** (макет, `.levels`): она стоит внутри акцентной
 * группы «Поправка на инфляцию» и продолжает её, а не спорит с ней. Приглушённая
 * подложка с рамкой, которая была здесь раньше, на зелёном читалась как чужая
 * вставка — вторая коробка внутри первой. Год с уровнем при этом получает СВОЮ
 * белую плашку: годов бывает несколько, и без плашек строка сливалась в перечень,
 * в котором не видно, где кончается один год и начинается следующий.
 */
export function InflationLevelsBar({ inflation, canEdit, onEdit }: InflationLevelsBarProps) {
  return (
    <div
      data-testid="inflation-levels"
      className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-2xs text-accent-text"
    >
      {/*
        Название ряда — в своём регистре. Макет ставит на это место капительный
        ярлык «Ряд по годам», но здесь стоит имя ряда, и капитель превратила бы
        «Росстат, ИПЦ, декабрь к декабрю» в крик.
      */}
      <span className="font-semibold">{inflation.series_name}</span>

      {inflation.used_years.length === 0 ? (
        // Пустой список — законное состояние (цель совпала с месяцем сметы, DoD 5),
        // и молчать о нём нельзя: читатель решил бы, что ряд не доехал до экрана.
        <span>коэффициенты за годы не потребовались</span>
      ) : (
        inflation.used_years.map((year) => {
          const level = coefficientLevel(year.coefficient);
          return (
            <span
              key={year.year}
              className="rounded-md border border-accent-border bg-surface px-1.5 py-px"
            >
              {year.year} <b className="font-semibold">{level.level}</b>
              {year.is_forecast && (
                <span className="ml-1.5 rounded-sm border border-warning-border bg-warning-soft px-1 text-warning-text">
                  прогноз
                </span>
              )}
            </span>
          );
        })
      )}

      {inflation.series_note && (
        <span className="text-fg-tertiary">{inflation.series_note}</span>
      )}

      {inflation.series_updated_at && (
        <span className="text-fg-tertiary">
          правлен {formatDate(inflation.series_updated_at)}
        </span>
      )}

      {canEdit && (
        <Button type="button" variant="ghost" size="sm" onClick={onEdit}>
          Изменить ряд
        </Button>
      )}
    </div>
  );
}
