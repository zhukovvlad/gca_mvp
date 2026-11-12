import type { OurPricePartReference, OurPricePartsGroup } from "@/types/domain";

import { formatUnitPrice, pick, priceUnitLabel, type VatView } from "./money";
import {
  analogsInContracts,
  PARTS_LOWEST,
  PARTS_NO_ANALOGS,
  PARTS_NO_RANGE,
  PARTS_NOTE,
  PARTS_OTHER_CLASS,
  PARTS_SAME_CLASS,
  periodRu,
} from "./ourPriceCopy";

export interface PartsReferenceProps {
  groups: OurPricePartsGroup[];
  vat: VatView;
  /** Единица строки КП: «₽/м3»; без неё - просто «₽». */
  unit?: string | null;
}

const DASH = "—";

function Part({ part, vat, unit }: { part: OurPricePartReference; vat: VatView; unit: string }) {
  const min = pick(part.min, vat);
  const max = pick(part.max, vat);
  const lowest = part.lowest === null ? null : pick(part.lowest.value, vat);
  const range = part.min === null ? null : `${min === null ? DASH : formatUnitPrice(min)} – ${max === null ? DASH : formatUnitPrice(max)}`;
  return (
    <div data-testid={`part-${part.value_id}`} className="space-y-1 rounded-lg border border-border px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span>
          <b>{part.value}</b>{" "}
          <span className="text-xs text-fg-secondary">
            · {part.analogs === 0 ? PARTS_NO_ANALOGS : analogsInContracts(part.analogs, part.contracts)}
            {part.analogs > 0 && ` · ${part.other_class ? PARTS_OTHER_CLASS : PARTS_SAME_CLASS}`}
          </span>
        </span>
        {range !== null && (
          <span className="font-mono tabular-nums">
            <span className="font-semibold">{range}</span> <span className="text-xs text-fg-secondary">{unit}</span>
          </span>
        )}
      </div>
      {part.analogs > 0 && part.min === null && <p className="text-xs text-fg-secondary">{PARTS_NO_RANGE}</p>}
      {part.lowest !== null && (
        <p className="text-xs text-fg-secondary">
          {PARTS_LOWEST}{" "}
          <span className="font-mono tabular-nums">{lowest === null ? DASH : formatUnitPrice(lowest)}</span> —{" "}
          {part.lowest.object_title} · дог. {part.lowest.contract_number}
          {part.lowest.period !== null && ` · ${periodRu(part.lowest.period)}`}
        </p>
      )}
    </div>
  );
}

/**
 * «По частям, для справки» сборной строки (спека упрощения каталога §2.8, §2.11): для каждого
 * составного параметра — его части с числом аналогов, вилкой и самой низкой расценкой. Целью
 * части не становятся, поэтому кнопок здесь нет. Деньги — готовые пары сервера, без арифметики.
 */
export function PartsReference({ groups, vat, unit = null }: PartsReferenceProps) {
  if (groups.length === 0) return null;
  const unitLabel = priceUnitLabel(unit);
  return (
    <div data-testid="parts-reference" className="space-y-3 text-sm">
      {groups.map((group) => (
        <div key={group.parameter_id} data-testid={`parts-group-${group.parameter_id}`} className="space-y-2">
          <p className="text-xs font-medium text-fg-secondary">{group.parameter_name}</p>
          {group.parts.map((part) => (
            <Part key={part.value_id} part={part} vat={vat} unit={unitLabel} />
          ))}
        </div>
      ))}
      <p className="text-xs text-fg-secondary">{PARTS_NOTE}</p>
    </div>
  );
}
