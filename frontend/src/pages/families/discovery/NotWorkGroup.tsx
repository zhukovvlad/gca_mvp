import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import type { NotWorkView } from "@/types/domain";

import { pluralRu } from "../labels";

/** Сколько наименований группа печатает сразу; остальные — «… и ещё K, отмечены», раскрываются кнопкой. */
const VISIBLE_NAMES = 20;

interface NotWorkGroupProps {
  group: NotWorkView;
  /** Снятые наименования (по тексту); непереданное наименование отмечено. */
  unchecked: ReadonlySet<string>;
  onToggle: (title: string, checked: boolean) => void;
  /** Галочка группы: отметить или снять все наименования разом. */
  onToggleAll: (checked: boolean) => void;
}

/**
 * Группа «Не работа» (экран 3 макета): строки без собственного предмета — примечания,
 * оговорки, заголовки. Раскрыта построчно, по наименованию: все отмечены, первые
 * {@link VISIBLE_NAMES} видны, остальные — под «Показать все» (как `GroupCard`). «Не работа»
 * получат только отмеченные наименования, и каждое — на ВСЕХ своих контекстах; снятые
 * остаются без семьи и уходят в перезапрос обычным порядком.
 */
export function NotWorkGroup({ group, unchecked, onToggle, onToggleAll }: NotWorkGroupProps) {
  const [showAll, setShowAll] = useState(false);
  const total = group.names.length;
  const checkedCount = group.names.filter((n) => !unchecked.has(n.title)).length;
  const visible = showAll ? group.names : group.names.slice(0, VISIBLE_NAMES);
  const hidden = total - visible.length;
  const hiddenChecked = group.names.slice(visible.length).filter((n) => !unchecked.has(n.title)).length;

  return (
    <div
      data-testid="not-work-group"
      className="flex flex-col gap-2 border-t border-border-subtle bg-warning-soft px-4 py-3.5"
    >
      <div className="flex items-start gap-3.5">
        <Checkbox
          className="mt-1"
          aria-label="Отметить все строки «Не работа»"
          checked={checkedCount === total}
          indeterminate={checkedCount > 0 && checkedCount < total}
          onCheckedChange={(checked) => onToggleAll(checked === true)}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[15px] font-semibold text-fg">
            Не работа
            <Badge variant="outline" className="border-warning-border bg-warning-soft text-warning-text">
              особая группа
            </Badge>
          </div>
          <p className="mt-0.5 text-[13px] text-fg-secondary">
            Строки без собственного предмета: примечания и оговорки, попавшие в смету отдельной
            позицией, заголовки. «Не работа» получат только отмеченные строки, снятые останутся
            без семьи и уйдут в перезапрос обычным порядком.
          </p>
        </div>
        <div className="flex-none text-right text-[13px] tabular-nums text-fg-secondary">
          <b className="text-fg">{checkedCount}</b> из {total} отмечены
        </div>
      </div>

      <div className="ml-[30px] overflow-hidden rounded-lg border border-border-subtle bg-surface">
        {visible.map((name) => {
          const isOff = unchecked.has(name.title);
          return (
            <div
              key={name.title}
              data-testid="not-work-row"
              className={cn(
                "flex items-start gap-3.5 border-b border-border-subtle px-4 py-3 text-[13px] last:border-b-0",
                isOff && "opacity-60"
              )}
            >
              <Checkbox
                className="mt-0.5"
                aria-label="Отметить строку"
                checked={!isOff}
                onCheckedChange={(checked) => onToggle(name.title, checked === true)}
              />
              <div className="min-w-0 flex-1 text-fg">
                {name.title}
                {name.contexts > 1 && (
                  <span className="ml-2 text-xs text-fg-tertiary">
                    {name.contexts} {pluralRu(name.contexts, "контекст", "контекста", "контекстов")}
                  </span>
                )}
              </div>
            </div>
          );
        })}
        {hidden > 0 && (
          <div className="flex items-center gap-3 px-4 py-2.5 text-[13px] text-fg-tertiary">
            <span>
              … и ещё {hidden}, {hiddenChecked === hidden ? "отмечены" : `отмечено ${hiddenChecked}`}
            </span>
            <Button variant="ghost" size="xs" onClick={() => setShowAll(true)}>
              Показать все
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
