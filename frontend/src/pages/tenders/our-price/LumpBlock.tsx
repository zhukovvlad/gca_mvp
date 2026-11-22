import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useSetLumpSum } from "@/services/queries";
import type { LumpVerdict, OurPriceLine } from "@/types/domain";

import type { VatView } from "./money";
import {
  LUMP_BLOCK_HINT,
  LUMP_BLOCK_TITLE,
  LUMP_CHOICE_LABEL,
  lumpOverrideNote,
  lumpRuleChoiceLabel,
} from "./ourPriceCopy";

interface LumpBlockProps {
  line: OurPriceLine;
  /** Поправлять может только `admin`; остальные видят подпись «поправлено вручную». */
  canEdit: boolean;
  /** Вид цен экрана: блок цен не печатает, оставлен для единой сигнатуры панели. */
  vat?: VatView;
}

type Choice = "rule" | LumpVerdict;

/**
 * Поправка «комплект или ставка» к строке КП (спека комплектов §8 п. 3): три положения,
 * выбор уходит сразу, без «Сохранить»; группа занята, пока идёт запрос и перечитывание
 * экранов. У строки без `lump` блока нет; у не-админа — только подпись поправки.
 */
export function LumpBlock({ line, canEdit }: LumpBlockProps) {
  const set = useSetLumpSum();
  const lump = line.lump;
  if (lump === null) return null;
  const note = lumpOverrideNote(lump);
  if (!canEdit && note === null) return null;
  const current: Choice = lump.override?.verdict ?? "rule";
  return (
    <div data-testid="lump-block" className="space-y-1.5">
      <p className="text-[11px] font-medium uppercase tracking-wide text-fg-tertiary">
        {LUMP_BLOCK_TITLE}
        {canEdit && " · только администратор"}
      </p>
      {canEdit && (
        <ToggleGroup
          variant="outline"
          size="sm"
          spacing={0}
          aria-label={LUMP_BLOCK_TITLE}
          disabled={set.isPending}
          value={[current]}
          onValueChange={(value) => {
            // Нажатое положение не снимается: у строки всегда одно из трёх.
            const next = value[0] as Choice | undefined;
            if (next === undefined || next === current) return;
            set.mutate({ id: line.position_item_id, verdict: next === "rule" ? null : next });
          }}
        >
          <ToggleGroupItem
            value="rule"
            data-testid="lump-choice-rule"
            className="aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          >
            {lumpRuleChoiceLabel(lump)}
          </ToggleGroupItem>
          <ToggleGroupItem
            value="lump"
            data-testid="lump-choice-lump"
            className="aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          >
            {LUMP_CHOICE_LABEL.lump}
          </ToggleGroupItem>
          <ToggleGroupItem
            value="rate"
            data-testid="lump-choice-rate"
            className="aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          >
            {LUMP_CHOICE_LABEL.rate}
          </ToggleGroupItem>
        </ToggleGroup>
      )}
      {note !== null && (
        <p data-testid="lump-override-note" className="text-xs text-fg-secondary">
          {note}
        </p>
      )}
      {canEdit && <p className="text-xs text-fg-secondary">{LUMP_BLOCK_HINT}</p>}
    </div>
  );
}
