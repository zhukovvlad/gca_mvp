import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { LineWhere } from "@/pages/tenders/our-price/LineWhere";
import { MarkDialog } from "@/pages/tenders/our-price/MarkDialog";
import { formatMoney, formatQuantity } from "@/pages/tenders/our-price/money";
import { MARK_DIALOG_TITLE, markFormLabel, markScopeText } from "@/pages/tenders/our-price/ourPriceCopy";
import { apiErrorCode, SUSPICION_CHANGED_CODE, useSetNotError, useSetPriceErrorFromList } from "@/services/queries";
import type { FactView, GroupPricesView, NeighborRow, SuspectView } from "@/types/domain";

import { PriceStrip } from "./PriceStrip";
import {
  ACTION_NOT_ERROR,
  ACTION_PRICE_ERROR,
  BLOCK_NEIGHBORS,
  BLOCK_PRICES,
  decisionByline,
  factorCaption,
  factorText,
  factSentence,
  KIND_OUTLIER,
  KIND_TWIN,
  LINE_OLD_ESTIMATE,
  NEIGHBOR_HEADERS,
  NEIGHBORS_NOTE,
  NEIGHBORS_NOTE_GROSS,
  NOT_ERROR_SUBMIT,
  NOT_ERROR_TITLE,
  notErrorScopeText,
  previousText,
  PREVIOUS_LABEL,
  PRICES_HEADERS,
  pricesNote,
  priceSuffix,
} from "./suspiciousCopy";
import { TwinPair } from "./TwinPair";

const NUM = "text-right font-mono whitespace-nowrap";

export function KindBadge({ kind }: { kind: FactView["kind"] }) {
  return (
    <span
      data-testid={`kind-${kind}`}
      className={cn(
        "justify-self-start rounded-full border px-2.5 py-px text-[11.5px] font-semibold whitespace-nowrap",
        kind === "outlier"
          ? "border-warning-border bg-warning-soft text-warning-text"
          : "border-mark-contractor bg-mark-contractor-soft text-mark-contractor"
      )}
    >
      {kind === "outlier" ? KIND_OUTLIER : KIND_TWIN}
    </span>
  );
}

/** Строка окна: сумма «как в смете», деньги не переносятся. */
function NeighborsTable({ rows }: { rows: NeighborRow[] }) {
  return (
    <Table className="min-w-[760px] text-xs">
      <TableHeader>
        <TableRow>
          <TableHead className="h-auto px-1.5 py-1 whitespace-normal">{NEIGHBOR_HEADERS.number}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 whitespace-normal">{NEIGHBOR_HEADERS.title}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 whitespace-normal">{NEIGHBOR_HEADERS.unit}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 text-right whitespace-normal">{NEIGHBOR_HEADERS.quantity}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 text-right whitespace-normal">{NEIGHBOR_HEADERS.price}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 text-right whitespace-normal">{NEIGHBOR_HEADERS.materials}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 text-right whitespace-normal">{NEIGHBOR_HEADERS.works}</TableHead>
          <TableHead className="h-auto px-1.5 py-1 text-right whitespace-normal">{NEIGHBOR_HEADERS.total}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow
            key={row.position_item_id}
            data-testid={`neighbor-${row.position_item_id}`}
            data-self={row.is_self || undefined}
            data-twin={row.is_twin || undefined}
            className={cn(row.is_self && "bg-warning-soft font-semibold", !row.is_self && row.is_twin && "bg-info-soft")}
          >
            <TableCell className="px-1.5 py-1 font-mono whitespace-nowrap">{row.item_number ?? "—"}</TableCell>
            <TableCell className="min-w-[220px] px-1.5 py-1 whitespace-normal">{row.title}</TableCell>
            <TableCell className="px-1.5 py-1">{row.unit ?? "—"}</TableCell>
            <TableCell className={cn("px-1.5 py-1", NUM)}>
              {row.quantity === null ? "—" : formatQuantity(row.quantity)}
            </TableCell>
            <TableCell className={cn("px-1.5 py-1", NUM)}>{formatMoney(row.unit_price, "")}</TableCell>
            <TableCell className={cn("px-1.5 py-1", NUM)}>{formatMoney(row.materials, "")}</TableCell>
            <TableCell className={cn("px-1.5 py-1", NUM)}>{formatMoney(row.works, "")}</TableCell>
            <TableCell className={cn("px-1.5 py-1", NUM)}>{formatMoney(row.total, "")}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function GroupPricesTable({ prices }: { prices: GroupPricesView }) {
  return (
    <>
      <Table className="min-w-[760px] text-xs">
        <TableHeader>
          <TableRow>
            <TableHead className="h-auto px-1.5 py-1">{PRICES_HEADERS.contract}</TableHead>
            <TableHead className="h-auto px-1.5 py-1 text-right">{PRICES_HEADERS.lines}</TableHead>
            <TableHead className="h-auto px-1.5 py-1 text-right">{PRICES_HEADERS.price}</TableHead>
            <TableHead className="h-auto px-1.5 py-1 text-right">{PRICES_HEADERS.range}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {prices.contracts.map((c) => (
            <TableRow
              key={c.contract_id}
              data-testid={`group-contract-${c.contract_id}`}
              data-own={c.is_own || undefined}
              className={cn(c.is_own && "bg-info-soft")}
            >
              <TableCell className="px-1.5 py-1 whitespace-normal">{c.object_title}</TableCell>
              <TableCell className={cn("px-1.5 py-1", NUM)}>{c.lines}</TableCell>
              <TableCell className={cn("px-1.5 py-1", NUM)}>{formatMoney(c.price, "")}</TableCell>
              <TableCell className={cn("px-1.5 py-1", NUM)}>
                {formatMoney(c.low, "")} — {formatMoney(c.high, "")}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p data-testid="group-note" className="text-2xs text-fg-tertiary">
        {pricesNote(prices.family_title, prices.unit, prices.usual)}
      </p>
    </>
  );
}

/** Раскрывающийся блок: заголовок-кнопка, содержимое в прокручиваемом контейнере. */
function Block({
  title,
  open,
  onOpenChange,
  testId,
  children,
}: {
  title: string;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} data-testid={testId} className="min-w-0 rounded-lg border border-border-subtle bg-surface-sunken">
      <CollapsibleTrigger className="flex w-full items-center gap-1 px-2 py-1 text-left text-[12.5px] text-fg-secondary">
        {open ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent className="grid min-w-0 gap-1.5 px-2 pb-2">{children}</CollapsibleContent>
    </Collapsible>
  );
}

export interface SuspectItemProps {
  item: SuspectView;
  /** Адрес указывает на эту строку: пункт прокручен в вид, оба блока раскрыты. */
  focused: boolean;
  /** Подозрение изменилось (`409 suspicion_changed`): список перечитан, сверху — сообщение. */
  onChanged: () => void;
}

/** Пункт «Не разобраны» (спека 2026-11-24-suspicious-lines-design.md, раздел 5 п. 1; макет, раздел 1). */
export function SuspectItem({ item, focused, onChanged }: SuspectItemProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [neighborsOpen, setNeighborsOpen] = useState(focused);
  const [pricesOpen, setPricesOpen] = useState(focused);
  const [dialog, setDialog] = useState<"price_error" | "not_error" | null>(null);
  const setNotError = useSetNotError();
  const setPriceError = useSetPriceErrorFromList();

  // Адрес сменился на эту строку — раскрыть блоки (перерасчёт при отрисовке, не в эффекте).
  const [seenFocus, setSeenFocus] = useState(focused);
  if (focused !== seenFocus) {
    setSeenFocus(focused);
    if (focused) {
      setNeighborsOpen(true);
      setPricesOpen(true);
    }
  }
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "start" });
  }, [focused]);

  const twinFact = item.facts.find((f) => f.kind === "twin");
  const kinds = [...new Set(item.facts.map((f) => f.kind))];
  const isTwinItem = kinds[0] === "twin";
  const selfRow = item.neighbors.find((row) => row.is_self);
  const gross = item.neighbors.some((row) => row.gross);
  const rowLabel = { title: item.title, objectTitle: item.contract.object_title };

  function closeIf(next: boolean) {
    if (!next) setDialog(null);
  }

  return (
    <div
      ref={ref}
      id={`suspicious-line-${item.position_item_id}`}
      data-testid={`suspect-${item.position_item_id}`}
      data-focused={focused || undefined}
      className={cn(
        "grid min-w-0 scroll-mt-4 gap-2 rounded-[10px] border border-border-subtle border-l-4 p-3 text-[13px]",
        isTwinItem ? "border-l-mark-contractor" : "border-l-warning"
      )}
    >
      {kinds.map((kind) => (
        <KindBadge key={kind} kind={kind} />
      ))}
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <b className="break-words">{item.title}</b>
          {item.unit !== null && <span className="text-2xs text-fg-secondary"> · {item.unit}</span>}
        </div>
        <div className="whitespace-nowrap">
          <b data-testid="line-price" className="font-mono text-[17px]">
            {formatMoney(item.net, "")}
          </b>{" "}
          <span className="text-2xs text-fg-secondary">{priceSuffix(item.unit)}</span>
        </div>
      </div>
      <div className="text-2xs text-fg-secondary">
        {item.contract.object_title} · дог. {item.contract.number}
        {item.item_number !== null && ` · п. ${item.item_number}`}
        {!item.latest && ` · ${LINE_OLD_ESTIMATE}`}
      </div>

      {item.facts.map((fact, index) =>
        fact.kind === "outlier" ? (
          <div
            key={`o-${index}`}
            data-testid="fact-outlier"
            className="grid min-w-0 gap-2 rounded-[10px] border border-warning-border bg-warning-soft p-2.5 sm:grid-cols-[150px_minmax(0,1fr)] sm:items-center"
          >
            <div className="flex items-center gap-2">
              <span data-testid="factor" className="text-[34px] leading-none font-semibold tracking-tight whitespace-nowrap text-warning tabular-nums">
                {factorText(fact.ratio, fact.direction)}
              </span>
              <span className="text-xs leading-tight text-warning-text">{factorCaption(fact.direction)}</span>
            </div>
            {item.prices !== null && item.net !== null ? (
              <PriceStrip prices={item.prices} net={item.net} />
            ) : null}
          </div>
        ) : null
      )}
      {twinFact?.kind === "twin" && selfRow !== undefined && <TwinPair self={selfRow} neighbor={twinFact.neighbor} />}
      {item.facts.map((fact, index) => (
        <p key={`s-${index}`} data-testid="fact-sentence" className="text-[12.5px] text-fg-secondary">
          {factSentence(fact, { unit: item.unit, net: item.net })}
        </p>
      ))}

      {item.previous.length > 0 && (
        <div data-testid="previous" className="rounded-lg border border-border-subtle bg-surface-sunken px-2.5 py-1.5 text-xs text-fg-secondary">
          <p className="font-medium">{PREVIOUS_LABEL}</p>
          <ul className="mt-0.5 space-y-0.5">
            {item.previous.map((decision, index) => (
              <li key={index}>
                {previousText(decision)}: {decisionByline(decision)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {item.neighbors.length > 0 && (
        <Block title={BLOCK_NEIGHBORS} open={neighborsOpen} onOpenChange={setNeighborsOpen} testId={`block-neighbors-${item.position_item_id}`}>
          <NeighborsTable rows={item.neighbors} />
          <p className="text-2xs text-fg-tertiary">{gross ? NEIGHBORS_NOTE_GROSS : NEIGHBORS_NOTE}</p>
          <LineWhere article={item.where.article} chapterPath={item.where.chapter_path} scope="contract" />
        </Block>
      )}
      {item.prices !== null && (
        <Block title={BLOCK_PRICES} open={pricesOpen} onOpenChange={setPricesOpen} testId={`block-prices-${item.position_item_id}`}>
          <GroupPricesTable prices={item.prices} />
        </Block>
      )}

      <div className="flex flex-wrap gap-1.5">
        <Button type="button" variant="outline" size="sm" data-testid="act-price-error" onClick={() => setDialog("price_error")}>
          {ACTION_PRICE_ERROR}
        </Button>
        <Button type="button" variant="outline" size="sm" data-testid="act-not-error" onClick={() => setDialog("not_error")}>
          {ACTION_NOT_ERROR}
        </Button>
      </div>

      <MarkDialog
        kind="price_error"
        title={MARK_DIALOG_TITLE.price_error}
        scopeText={markScopeText("price_error", rowLabel, markFormLabel(item.title, item.unit), 0)}
        open={dialog === "price_error"}
        onOpenChange={closeIf}
        busy={setPriceError.isPending}
        onSubmit={(reason) =>
          setPriceError.mutate({ positionItemId: item.position_item_id, reason }, { onSuccess: () => setDialog(null) })
        }
      />
      <MarkDialog
        kind="not_error"
        title={NOT_ERROR_TITLE}
        submitLabel={NOT_ERROR_SUBMIT}
        scopeText={notErrorScopeText(rowLabel, item.facts, item.unit)}
        open={dialog === "not_error"}
        onOpenChange={closeIf}
        busy={setNotError.isPending}
        onSubmit={(reason) =>
          setNotError.mutate(
            { positionItemId: item.position_item_id, reason, bases: item.facts.map((fact) => fact.basis) },
            {
              onSuccess: () => setDialog(null),
              onError: (error) => {
                if (apiErrorCode(error) !== SUSPICION_CHANGED_CODE) return;
                setDialog(null);
                onChanged();
              },
            }
          )
        }
      />
    </div>
  );
}
