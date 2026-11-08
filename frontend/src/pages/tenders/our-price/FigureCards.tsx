import { Card, CardContent } from "@/components/ui/card";
import { compareDecimalStrings } from "@/lib/decimal";
import type { MoneyPair, OurPriceAnalog, OurPriceLine } from "@/types/domain";

import { absDecimal, formatSigned, formatUnitPrice, pick, priceUnitLabel, type VatView } from "./money";
import {
  FIGURE_KP_ABOVE,
  FIGURE_KP_BELOW,
  FIGURE_KP_EQUAL,
  FIGURE_LABEL,
  FIGURE_NONE,
  periodRu,
} from "./ourPriceCopy";

export interface FigureCardsProps {
  line: OurPriceLine;
  /** Справка панели: по `analog_id` карточка берёт объект, договор и дату цены. */
  analogs: OurPriceAnalog[];
  vat: VatView;
}

interface CardProps {
  testId: string;
  label: string;
  unit: string | null;
  price: MoneyPair | null;
  analogId: number | null;
  period: string | null;
  diff: MoneyPair | null;
  /** Разница за единицу строки; `null` — печатается один итог. */
  diffUnit: MoneyPair | null;
  analogs: OurPriceAnalog[];
  vat: VatView;
}

function Figure({ testId, label, unit, price, analogId, period, diff, diffUnit, analogs, vat }: CardProps) {
  const analog = analogId === null ? undefined : analogs.find((a) => a.analog_id === analogId);
  const value = pick(price, vat);
  const delta = pick(diff, vat);
  const deltaUnit = pick(diffUnit, vat);
  const sign = delta === null ? null : compareDecimalStrings(delta, "0");
  const date = period ?? analog?.period ?? null;
  const where = analog === undefined ? null : `${analog.object_title} · дог. ${analog.number}`;
  return (
    <Card size="sm" data-testid={testId} className="min-w-0 bg-surface-sunken">
      <CardContent className="space-y-1 text-sm">
        <p className="text-xs text-fg-secondary">{label}</p>
        {value === null ? (
          <p className="text-fg-secondary">{FIGURE_NONE}</p>
        ) : (
          <>
            <p className="font-mono text-xl font-semibold tabular-nums">
              {formatUnitPrice(value)}{" "}
              <span className="text-xs font-normal text-fg-secondary">{priceUnitLabel(unit)}</span>
            </p>
            {(where !== null || date !== null) && (
              <p className="text-xs text-fg-secondary">
                {[where, date === null ? null : periodRu(date)].filter((p): p is string => p !== null).join(" · ")}
              </p>
            )}
            <p
              data-testid={`${testId}-diff`}
              className={
                sign === 1 ? "text-xs text-warning-text" : sign === -1 ? "text-xs text-info-text" : "text-xs text-fg-secondary"
              }
            >
              <span className="font-mono tabular-nums">
                {delta === null
                  ? "—"
                  : sign === 0
                    ? FIGURE_KP_EQUAL
                    : `${sign === 1 ? FIGURE_KP_ABOVE : FIGURE_KP_BELOW}${
                        deltaUnit === null || unit === null
                          ? ":"
                          : ` на ${formatUnitPrice(absDecimal(deltaUnit))} за ${unit} ·`
                      } ${formatSigned(delta)}`}
              </span>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Две карточки панели работы (спека лестницы аналогов §2.20): самая низкая и самая свежая
 * расценка, цена за единицу строки КП, объект и договор, дата цены и разница КП к ней
 * (`to_lowest`, `to_freshest` и разницы за единицу `*_unit` — готовые строки сервера). Без цифр (все аналоги — выбросы)
 * карточек нет.
 */
export function FigureCards({ line, analogs, vat }: FigureCardsProps) {
  if (line.lowest === null) return null;
  return (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
      <Figure
        testId="figure-lowest"
        label={FIGURE_LABEL.lowest}
        unit={line.unit}
        price={line.lowest.value}
        analogId={line.lowest.analog_id}
        period={null}
        diff={line.to_lowest}
        diffUnit={line.to_lowest_unit}
        analogs={analogs}
        vat={vat}
      />
      <Figure
        testId="figure-freshest"
        label={FIGURE_LABEL.freshest}
        unit={line.unit}
        price={line.freshest?.value ?? null}
        analogId={line.freshest?.analog_id ?? null}
        period={line.freshest?.period ?? null}
        diff={line.to_freshest}
        diffUnit={line.to_freshest_unit}
        analogs={analogs}
        vat={vat}
      />
    </div>
  );
}
