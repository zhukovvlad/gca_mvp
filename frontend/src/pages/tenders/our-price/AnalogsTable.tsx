import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleTrigger } from "@/components/ui/collapsible";
import { compareDecimalStrings } from "@/lib/decimal";
import { cn } from "@/lib/utils";
import type { OurPriceAnalog } from "@/types/domain";

import { LineWhere } from "./LineWhere";
import { formatMoney, formatUnitPrice, pick, type VatView } from "./money";
import {
  ANALOG_FIRST,
  ANALOG_MAKE_TARGET,
  ANALOG_THIS_CONTRACTOR,
  analogOutlierNote,
  ANALOGS_EMPTY,
  ANALOGS_LESS,
  ANALOGS_NOMINAL_TITLE,
  analogsMore,
  periodRu,
} from "./ourPriceCopy";
import { TargetMark, type TargetMarkKind } from "./TargetCards";

export interface AnalogsTableProps {
  analogs: OurPriceAnalog[];
  vat: VatView;
  /** «Сделать целью» — только `admin` и только у сравнимой строки. */
  canChoose: boolean;
  /** Аналог, на который цель указывает сейчас (закреплённый или по стратегии): у него метка «цель». */
  targetAnalogId: number | null;
  /** Закреплённый аналог (цель — именно он): кнопки «сделать целью» у него нет. */
  pinnedAnalogId: number | null;
  /** Аналог, на который указывает «с этим подрядчиком»: у него одноимённая метка. */
  contractorAnalogId: number | null;
  /** Цель сохраняется: кнопки выбора погашены. */
  busy: boolean;
  onChoose: (analogId: number) => void;
}

const DASH = "—";

/** Метки цели аналога по порядку важности: низкая, свежая, с подрядчиком. */
function marksOf(a: OurPriceAnalog, contractorAnalogId: number | null): TargetMarkKind[] {
  const marks: TargetMarkKind[] = [];
  if (a.is_lowest) marks.push("lowest");
  if (a.is_freshest) marks.push("freshest");
  if (a.analog_id === contractorAnalogId) marks.push("contractor");
  return marks;
}

const MARK_RANK: Record<TargetMarkKind, number> = { lowest: 0, freshest: 1, contractor: 2, target: 3 };

/**
 * Не больше пяти аналогов: сначала с метками цели (по важности метки), затем по цене от низкой;
 * равные остаются в порядке сервера. Цены сравниваются строками, без арифметики.
 */
function ordered(analogs: OurPriceAnalog[], contractorAnalogId: number | null, targetAnalogId: number | null) {
  const rank = (a: OurPriceAnalog) => {
    const marks = marksOf(a, contractorAnalogId).map((m) => MARK_RANK[m]);
    if (a.analog_id === targetAnalogId) marks.push(MARK_RANK.target);
    return marks.length === 0 ? null : Math.min(...marks);
  };
  return analogs
    .map((a, index) => ({ a, index, rank: rank(a) }))
    .sort((x, y) => {
      if (x.rank !== y.rank) {
        if (x.rank === null) return 1;
        if (y.rank === null) return -1;
        return x.rank - y.rank;
      }
      return compareDecimalStrings(x.a.price.net, y.a.price.net) || x.index - y.index;
    })
    .map((item) => item.a);
}

/**
 * Справка аналогов панели работы (спека упрощения каталога §2.11): карточки с «где строка» договора,
 * не больше пяти, остальные за «ещё N аналога из M». Выброс выделен и подписан кратностью, цель
 * закрепляется только у аналога остатка (`selectable`) — у любого, в том числе с меткой стратегии.
 */
export function AnalogsTable({
  analogs,
  vat,
  canChoose,
  targetAnalogId,
  pinnedAnalogId,
  contractorAnalogId,
  busy,
  onChoose,
}: AnalogsTableProps) {
  const [open, setOpen] = useState(false);
  if (analogs.length === 0) {
    return (
      <p data-testid="analogs-empty" className="text-sm text-fg-secondary">
        {ANALOGS_EMPTY}
      </p>
    );
  }
  const list = ordered(analogs, contractorAnalogId, targetAnalogId);
  const shown = open ? list : list.slice(0, ANALOG_FIRST);
  const hidden = list.length - ANALOG_FIRST;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div data-testid="analogs-table" className="space-y-3">
        {shown.map((a) => {
          const price = pick(a.price, vat);
          const nominal = pick(a.nominal, vat);
          const outlier = a.outlier_ratio !== null;
          const isTarget = a.analog_id === targetAnalogId;
          const marks = marksOf(a, contractorAnalogId);
          return (
            <div
              key={a.analog_id}
              data-testid={`analog-${a.analog_id}`}
              data-outlier={outlier ? "true" : undefined}
              className={cn(
                "space-y-1.5 border-t border-border pt-3 text-sm",
                outlier && "rounded-md bg-warning-soft px-2",
                isTarget && "border-l-[3px] border-l-action pl-3"
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <b>{a.object_title}</b>
                  <span className="text-xs text-fg-secondary">
                    {" "}
                    · дог. {a.number} · {a.contractor_title}
                  </span>
                  {a.with_this_contractor && (
                    <Badge
                      variant="secondary"
                      className="ml-1.5 h-auto bg-mark-contractor-soft py-0.5 text-mark-contractor"
                    >
                      {ANALOG_THIS_CONTRACTOR}
                    </Badge>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  <span
                    data-testid={`analog-price-${a.analog_id}`}
                    className="font-mono font-semibold tabular-nums"
                    title={
                      nominal !== null && price !== nominal
                        ? `${ANALOGS_NOMINAL_TITLE}: ${formatMoney(nominal, "")}`
                        : undefined
                    }
                  >
                    {price === null ? DASH : formatUnitPrice(price)}
                  </span>{" "}
                  <span data-testid={`analog-date-${a.analog_id}`} className="font-mono text-xs text-fg-secondary tabular-nums">
                    {periodRu(a.period)}
                  </span>
                </div>
              </div>
              {(marks.length > 0 || isTarget || a.outlier_ratio !== null) && (
                <div className="flex flex-wrap gap-1.5">
                  {marks.map((m) => (
                    <TargetMark key={m} kind={m} />
                  ))}
                  {a.outlier_ratio !== null && (
                    <Badge variant="outline" className="h-auto whitespace-normal py-0.5 text-left text-warning-text">
                      {analogOutlierNote(a.outlier_ratio)}
                    </Badge>
                  )}
                  {isTarget && <TargetMark kind="target" />}
                </div>
              )}
              <p
                data-testid={`analog-title-${a.analog_id}`}
                className="break-words text-fg-secondary"
                title={a.title}
              >
                {a.title}
              </p>
              <LineWhere article={a.article} chapterPath={a.chapter_path} scope="contract" />
              {canChoose && a.selectable && a.analog_id !== pinnedAnalogId && (
                <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => onChoose(a.analog_id)}>
                  <span aria-hidden="true">◎</span>
                  {ANALOG_MAKE_TARGET}
                </Button>
              )}
            </div>
          );
        })}
      </div>
      {hidden > 0 && (
        <CollapsibleTrigger
          render={<Button type="button" size="sm" variant="outline" className="mt-3 w-full border-dashed" />}
        >
          {open ? ANALOGS_LESS : analogsMore(hidden, list.length)}
        </CollapsibleTrigger>
      )}
    </Collapsible>
  );
}
