import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { OurPriceAnalog } from "@/types/domain";

import { formatMoney, formatUnitPrice, pick, type VatView } from "./money";
import {
  ANALOG_FIRST,
  ANALOG_FRESHEST,
  ANALOG_IS_TARGET,
  ANALOG_LOWEST,
  ANALOG_MAKE_TARGET,
  ANALOG_THIS_CONTRACTOR,
  analogOutlierNote,
  ANALOGS_COLUMN,
  ANALOGS_EMPTY,
  ANALOGS_LESS,
  ANALOGS_NOMINAL_TITLE,
  analogsMore,
  periodRu,
} from "./ourPriceCopy";

export interface AnalogsTableProps {
  analogs: OurPriceAnalog[];
  vat: VatView;
  /** «Сделать целью» — только `admin` и только у сравнимой строки. */
  canChoose: boolean;
  /** Аналог, выбранный целью сейчас (действующий выбор); `null` — нет. */
  targetAnalogId: number | null;
  /** Цель сохраняется: кнопки выбора погашены. */
  busy: boolean;
  onChoose: (analogId: number) => void;
}

const DASH = "—";

/**
 * Справка аналогов панели работы (спека лестницы аналогов §2.11, §2.20): порядок сервера,
 * первые пять и «ещё N» по кнопке. Выброс выделен и подписан кратностью, цель выбирается
 * только у аналога остатка (`selectable`).
 */
export function AnalogsTable({ analogs, vat, canChoose, targetAnalogId, busy, onChoose }: AnalogsTableProps) {
  const [open, setOpen] = useState(false);
  if (analogs.length === 0) {
    return (
      <p data-testid="analogs-empty" className="text-sm text-fg-secondary">
        {ANALOGS_EMPTY}
      </p>
    );
  }
  const shown = open ? analogs : analogs.slice(0, ANALOG_FIRST);
  const hidden = analogs.length - ANALOG_FIRST;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Table data-testid="analogs-table" className="table-fixed text-xs">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[22%] whitespace-normal">{ANALOGS_COLUMN.contract}</TableHead>
            <TableHead className="whitespace-normal">{ANALOGS_COLUMN.line}</TableHead>
            <TableHead className="w-[14%] whitespace-normal">{ANALOGS_COLUMN.date}</TableHead>
            <TableHead className="w-[17%] whitespace-normal text-right">{ANALOGS_COLUMN.price}</TableHead>
            <TableHead className="w-[19%]" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((a) => {
            const price = pick(a.price, vat);
            const nominal = pick(a.nominal, vat);
            const outlier = a.outlier_ratio !== null;
            return (
              <TableRow
                key={a.analog_id}
                data-testid={`analog-${a.analog_id}`}
                data-outlier={outlier ? "true" : undefined}
                className={cn("align-top", outlier && "bg-warning-soft")}
              >
                <TableCell className="whitespace-normal break-words align-top">
                  {a.object_title}
                  <span className="block text-[0.6875rem] text-fg-secondary">
                    дог. {a.number} · {a.contractor_title}
                  </span>
                  {a.with_this_contractor && (
                    <Badge variant="secondary" className="mt-0.5 h-auto whitespace-normal py-0.5">
                      {ANALOG_THIS_CONTRACTOR}
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="whitespace-normal break-words align-top">
                  <span className="line-clamp-3" title={a.title}>
                    {a.title}
                  </span>
                  {a.section_titles.length > 0 && (
                    <span
                      className="line-clamp-2 text-[0.6875rem] text-fg-secondary"
                      title={a.section_titles.join(" / ")}
                    >
                      {a.section_titles.join(" / ")}
                    </span>
                  )}
                </TableCell>
                <TableCell className="whitespace-normal align-top">
                  <span data-testid={`analog-date-${a.analog_id}`} className="font-mono tabular-nums">
                    {periodRu(a.period)}
                  </span>
                </TableCell>
                <TableCell className="whitespace-normal text-right align-top">
                  <span
                    data-testid={`analog-price-${a.analog_id}`}
                    className="font-mono tabular-nums"
                    title={
                      nominal !== null && price !== nominal
                        ? `${ANALOGS_NOMINAL_TITLE}: ${formatMoney(nominal, "")}`
                        : undefined
                    }
                  >
                    {price === null ? DASH : formatUnitPrice(price)}
                  </span>
                </TableCell>
                <TableCell className="whitespace-normal align-top">
                  <span className="flex flex-col items-start gap-1">
                    {a.is_lowest && <Badge variant="secondary">{ANALOG_LOWEST}</Badge>}
                    {a.is_freshest && <Badge variant="secondary">{ANALOG_FRESHEST}</Badge>}
                    {a.outlier_ratio !== null && (
                      <Badge variant="outline" className="h-auto whitespace-normal py-0.5 text-left text-warning-text">
                        {analogOutlierNote(a.outlier_ratio)}
                      </Badge>
                    )}
                    {a.analog_id === targetAnalogId ? (
                      <Badge>{ANALOG_IS_TARGET}</Badge>
                    ) : (
                      canChoose &&
                      a.selectable && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          aria-label="сделать целью"
                          disabled={busy}
                          onClick={() => onChoose(a.analog_id)}
                        >
                          {ANALOG_MAKE_TARGET}
                        </Button>
                      )
                    )}
                  </span>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {hidden > 0 && (
        <CollapsibleTrigger render={<Button type="button" size="sm" variant="ghost" />}>
          {open ? ANALOGS_LESS : analogsMore(hidden)}
        </CollapsibleTrigger>
      )}
    </Collapsible>
  );
}
