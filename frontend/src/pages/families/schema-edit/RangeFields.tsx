import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { FamilySchemaParameter, SchemaEditValueMove, SchemaRange } from "@/types/domain";

import { pluralRu } from "../labels";
import { ValueChip } from "./ValueChip";
import { rangeInnerCount, sameRange } from "./drafts";

interface RangeFieldsProps {
  range: SchemaRange;
  onChange: (range: SchemaRange) => void;
  /** Сохранённый параметр: его подписи показываются, пока границы не менялись. */
  saved: FamilySchemaParameter | undefined;
  /** Переносы прежних значений из сводки предпросмотра: «что станет с прежними значениями». */
  moves: SchemaEditValueMove[];
}

/**
 * Значения-диапазоны (спека 3в §2.6, §2.11): границы и шаг, превью числа диапазонов, правило
 * отнесения числа, «только из списка» включено и снять его нельзя, таблица переносов при
 * перестройке. Подписи диапазонов строит система: до сохранения показывается их число, а
 * подписи — только у сохранённых границ.
 */
export function RangeFields({ range, onChange, saved, moves }: RangeFieldsProps) {
  const inner = rangeInnerCount(range);
  const savedLabels =
    saved !== undefined && saved.value_kind === "range" && sameRange(saved.range, range)
      ? saved.values.filter((value) => value.merged_into_id === null)
      : [];
  const grid = (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Label htmlFor="schema-edit-range-start">от</Label>
      <Input
        id="schema-edit-range-start"
        aria-label="Нижняя граница"
        inputMode="decimal"
        className="w-20 font-mono"
        value={range.start}
        onChange={(e) => onChange({ ...range, start: e.target.value })}
      />
      <Label htmlFor="schema-edit-range-stop">до</Label>
      <Input
        id="schema-edit-range-stop"
        aria-label="Верхняя граница"
        inputMode="decimal"
        className="w-20 font-mono"
        value={range.stop}
        onChange={(e) => onChange({ ...range, stop: e.target.value })}
      />
      <Label htmlFor="schema-edit-range-step">шаг</Label>
      <Input
        id="schema-edit-range-step"
        aria-label="Шаг"
        inputMode="decimal"
        className="w-20 font-mono"
        value={range.step}
        onChange={(e) => onChange({ ...range, step: e.target.value })}
      />
    </div>
  );

  return (
    <div className="grid gap-2">
      {grid}
      <p className="text-xs text-fg-tertiary">
        {inner === null
          ? "Укажите границы и шаг: нижняя граница меньше верхней, шаг больше нуля, число шагов целое."
          : `Получится ${inner} ${pluralRu(inner, "диапазон", "диапазона", "диапазонов")} от ${range.start.trim()} до ${range.stop.trim()} и 2 крайних — «до ${range.start.trim()}» и «${range.stop.trim()} и более»; подписи строит система, дыр и перекрытий нет.`}
      </p>
      {savedLabels.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {savedLabels.map((value) => (
            <ValueChip key={value.id} text={value.value} rows={value.rows} />
          ))}
        </div>
      )}
      <p className="text-xs text-fg-tertiary">
        Модель найдёт число в наименовании и отнесёт его к диапазону: нижняя граница включается, верхняя —
        нет. Числа нет — «не уточнено».
      </p>
      <div className="flex items-center gap-2 text-sm text-fg-tertiary">
        <Checkbox id="schema-edit-range-closed" checked disabled />
        <Label htmlFor="schema-edit-range-closed" className="font-normal">
          Только из списка — для диапазонов включено всегда
        </Label>
      </div>
      {moves.length > 0 && (
        <div className="grid gap-1" aria-label="Что станет с прежними значениями">
          <span className="text-xs font-medium">Что станет с прежними значениями</span>
          <ul className="grid gap-0.5 text-xs">
            {moves.map((move) => (
              <li key={`${move.from_value}→${move.to_value}`}>
                <span className="font-mono">{move.from_value}</span>
                {" → "}
                <span>{move.to_value}</span>
                <span className="text-fg-tertiary">
                  {" · "}
                  {move.contexts} {pluralRu(move.contexts, "контекст", "контекста", "контекстов")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
