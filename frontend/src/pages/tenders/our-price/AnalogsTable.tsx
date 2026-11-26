import { MoreHorizontal } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { compareDecimalStrings } from "@/lib/decimal";
import { cn } from "@/lib/utils";
import { PriceStrip } from "@/pages/contracts/PriceStrip";
import {
  ACTION_NOT_ERROR,
  ACTION_PRICE_ERROR,
  checkedLine,
  factorText,
  HINT_LINK,
  HINT_LINK_NOTE,
  HINT_TITLE,
  HINT_TWIN_TITLE,
  hintListHref,
  hintSentence,
  NOT_ERROR_SUBMIT,
  NOT_ERROR_TITLE,
  notErrorScopeText,
  STRIP_NOMINAL_NOTE,
  SUSPICION_CHANGED_NOTICE,
} from "@/pages/contracts/suspiciousCopy";
import { apiErrorCode, SUSPICION_CHANGED_CODE, useSetLumpSum, useSetMark, useSetNotError } from "@/services/queries";
import type { AnalogSuspicion, LineIds, MarkKind, OurPriceAnalog, ZoneScope } from "@/types/domain";

import { LineWhere } from "./LineWhere";
import { MarkDialog } from "./MarkDialog";
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
  analogZoneBadge,
  LUMP_ANALOG_ACTION,
  LUMP_ANALOG_CONFIRM_CANCEL,
  LUMP_ANALOG_CONFIRM_TITLE,
  lumpAnalogConfirm,
  MARK_ACTION,
  MARK_DIALOG_TITLE,
  MARK_MENU_LABEL,
  MARK_MENU_TRIGGER,
  markScopeText,
  periodRu,
  sideText,
} from "./ourPriceCopy";
import { releasedValueText } from "./paramsCopy";
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
  /** Отпускаемые параметры ступени 2/4 (`analog_columns`): у каждого аналога - значение по порядку; пусто - колонок нет. */
  columns: Array<{ name: string }>;
  /** Область подбора ступени: при `other` и `any` у каждого аналога метка его зоны. */
  zoneScope: ZoneScope;
  /** Меню аналога («это комплект», «не аналог в этом тендере», «ошибка в цене») — только `admin` (спека «Не аналог» §8 п. 1). */
  canLump?: boolean;
  /** Строка КП панели: от неё тендерная отметка берёт ключ формы. */
  ids: LineIds;
  /** Строка формы словами для окна тендерной отметки: «наименование, единица». */
  formLabel: string;
}

const DASH = "—";

interface AnalogHintProps {
  analog: OurPriceAnalog;
  suspicion: AnalogSuspicion;
  /** Только `admin`: ссылка в список и кнопки решений. */
  canAct: boolean;
  disabled: boolean;
  onPriceError: () => void;
  onTender: () => void;
  onNotError: () => void;
}

/**
 * Подсказка «подозрительная цена» у аналога (спека подозрительных строк §5 п. 4; макет, раздел 3).
 * Цифры — только из `suspicion` (номинальное нетто без НДС и индексации): режим панели их не меняет.
 */
function AnalogHint({ analog, suspicion, canAct, disabled, onPriceError, onTender, onNotError }: AnalogHintProps) {
  const outlierFacts = suspicion.facts.filter((fact) => fact.kind === "outlier");
  const line = { unit: analog.unit, net: suspicion.net };
  return (
    <div
      data-testid={`analog-hint-${analog.analog_id}`}
      data-kind={suspicion.facts[0]?.kind}
      className={cn(
        "grid min-w-0 gap-1.5 rounded-[10px] border p-2.5",
        suspicion.facts[0]?.kind === "twin"
          ? "border-mark-contractor bg-mark-contractor-soft"
          : "border-warning-border bg-warning-soft"
      )}
    >
      {suspicion.facts.map((fact, index) => (
        <div key={index} className="flex min-w-0 items-start gap-2">
          {fact.kind === "outlier" && (
            <span
              data-testid="analog-hint-factor"
              className="shrink-0 text-[34px] leading-none font-semibold tracking-tight whitespace-nowrap text-warning tabular-nums"
            >
              {factorText(fact.ratio, fact.direction)}
            </span>
          )}
          <span
            className={cn(
              "min-w-0 text-xs break-words",
              fact.kind === "outlier" ? "text-warning-text" : "text-mark-contractor"
            )}
          >
            <b>{fact.kind === "outlier" ? HINT_TITLE : `${HINT_TITLE} — ${HINT_TWIN_TITLE}`}</b>
            {fact.kind === "outlier" ? " — " : ": "}
            {hintSentence(fact, line)}
          </span>
        </div>
      ))}
      {outlierFacts.length > 0 && suspicion.prices !== null && suspicion.net !== null && (
        <PriceStrip prices={suspicion.prices} net={suspicion.net} compact />
      )}
      <p className="text-2xs text-fg-secondary">{STRIP_NOMINAL_NOTE}</p>
      {canAct && (
        <>
          <div className="text-xs">
            <a
              href={hintListHref(analog.analog_id)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-action underline underline-offset-2"
            >
              {HINT_LINK}
            </a>{" "}
            <span className="text-2xs text-fg-tertiary">{HINT_LINK_NOTE}</span>
          </div>
          <div data-testid="analog-hint-actions" className="flex flex-wrap gap-1.5">
            <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={onPriceError}>
              {ACTION_PRICE_ERROR}
            </Button>
            <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={onTender}>
              {MARK_ACTION.tender}
            </Button>
            <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={onNotError}>
              {ACTION_NOT_ERROR}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

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
  columns,
  zoneScope,
  canLump = false,
  ids,
  formLabel,
}: AnalogsTableProps) {
  const [open, setOpen] = useState(false);
  const [pendingLump, setPendingLump] = useState<OurPriceAnalog | null>(null);
  const [pendingMark, setPendingMark] = useState<{ kind: MarkKind; analog: OurPriceAnalog } | null>(null);
  const [pendingNotError, setPendingNotError] = useState<OurPriceAnalog | null>(null);
  const setLump = useSetLumpSum();
  const setMark = useSetMark();
  const setNotError = useSetNotError();
  if (analogs.length === 0) {
    return (
      <p data-testid="analogs-empty" className="text-sm text-fg-secondary">
        {ANALOGS_EMPTY}
      </p>
    );
  }
  const list = ordered(analogs, contractorAnalogId, targetAnalogId);
  const showZone = zoneScope !== "own";
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
                  {canLump && (
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={
                          <Button
                            type="button"
                            size="icon-xs"
                            variant="ghost"
                            className="ml-1 align-middle"
                            data-testid={`analog-menu-${a.analog_id}`}
                            aria-label={MARK_MENU_TRIGGER}
                            disabled={setLump.isPending || setMark.isPending}
                          />
                        }
                      >
                        <MoreHorizontal aria-hidden="true" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-auto min-w-56">
                        <DropdownMenuGroup>
                          <DropdownMenuLabel>{MARK_MENU_LABEL}</DropdownMenuLabel>
                          <DropdownMenuItem data-testid={`analog-lump-${a.analog_id}`} onClick={() => setPendingLump(a)}>
                            {LUMP_ANALOG_ACTION}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            data-testid={`analog-mark-tender-${a.analog_id}`}
                            onClick={() => setPendingMark({ kind: "tender", analog: a })}
                          >
                            {MARK_ACTION.tender}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            data-testid={`analog-mark-price-error-${a.analog_id}`}
                            onClick={() => setPendingMark({ kind: "price_error", analog: a })}
                          >
                            {MARK_ACTION.price_error}
                          </DropdownMenuItem>
                        </DropdownMenuGroup>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </div>
              </div>
              {(marks.length > 0 || isTarget || a.outlier_ratio !== null || showZone) && (
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
                  {showZone && (
                    <Badge variant="secondary" data-testid={`analog-zone-${a.analog_id}`} className="h-auto py-0.5">
                      {analogZoneBadge(a.zone)}
                    </Badge>
                  )}
                </div>
              )}
              <p
                data-testid={`analog-title-${a.analog_id}`}
                className="break-words text-fg-secondary"
                title={a.title}
              >
                {a.title}
              </p>
              {a.suspicion !== null && (
                <AnalogHint
                  analog={a}
                  suspicion={a.suspicion}
                  canAct={canLump}
                  disabled={setLump.isPending || setMark.isPending || setNotError.isPending}
                  onPriceError={() => setPendingMark({ kind: "price_error", analog: a })}
                  onTender={() => setPendingMark({ kind: "tender", analog: a })}
                  onNotError={() => setPendingNotError(a)}
                />
              )}
              {a.suspicion === null &&
                a.not_error.map((decision, index) => (
                  <p key={index} data-testid="analog-checked" className="text-2xs break-words text-fg-tertiary">
                    {checkedLine(decision)}
                  </p>
                ))}
              {columns.length > 0 && (
                <dl data-testid={`analog-released-${a.analog_id}`} className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
                  {columns.map((column, i) => {
                    const released = a.released_values[i];
                    if (released === undefined) return null;
                    return (
                      <div key={column.name} className="flex gap-1">
                        <dt data-testid="analog-released-name" className="text-fg-secondary">
                          {column.name}
                        </dt>
                        <dd
                          data-testid="analog-released-value"
                          data-differs={released.differs ? "true" : undefined}
                          className={cn(released.differs && "rounded-sm bg-warning-soft px-1 text-warning-text")}
                        >
                          {releasedValueText(released)}
                          {released.side !== null && (
                            <>
                              <span aria-hidden="true">{released.side === "up" ? " ↑" : " ↓"}</span>
                              <span className="sr-only">{sideText(released.side)}</span>
                            </>
                          )}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
              )}
              <LineWhere article={a.article} chapterPath={a.chapter_path} scope="contract" />
              {canChoose && a.selectable && a.analog_id !== pinnedAnalogId && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => onChoose(a.analog_id)}>
                    <span aria-hidden="true">◎</span>
                    {ANALOG_MAKE_TARGET}
                  </Button>
                </div>
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
      {canLump && (
        <MarkDialog
          kind={pendingMark?.kind ?? "tender"}
          title={MARK_DIALOG_TITLE[pendingMark?.kind ?? "tender"]}
          scopeText={
            pendingMark === null
              ? ""
              : markScopeText(
                  pendingMark.kind,
                  { title: pendingMark.analog.title, objectTitle: pendingMark.analog.object_title },
                  formLabel,
                  ids.tenderId
                )
          }
          open={pendingMark !== null}
          onOpenChange={(next) => {
            if (!next) setPendingMark(null);
          }}
          busy={setMark.isPending}
          onSubmit={(reason) => {
            if (pendingMark === null) return;
            setMark.mutate(
              { ...ids, kind: pendingMark.kind, positionItemId: pendingMark.analog.analog_id, reason },
              { onSuccess: () => setPendingMark(null) }
            );
          }}
        />
      )}
      {canLump && (
        <MarkDialog
          kind="not_error"
          title={NOT_ERROR_TITLE}
          submitLabel={NOT_ERROR_SUBMIT}
          scopeText={
            pendingNotError?.suspicion
              ? notErrorScopeText(
                  { title: pendingNotError.title, objectTitle: pendingNotError.object_title },
                  pendingNotError.suspicion.facts,
                  pendingNotError.unit
                )
              : ""
          }
          open={pendingNotError !== null}
          onOpenChange={(next) => {
            if (!next) setPendingNotError(null);
          }}
          busy={setNotError.isPending}
          onSubmit={(reason) => {
            if (pendingNotError?.suspicion == null) return;
            setNotError.mutate(
              {
                positionItemId: pendingNotError.analog_id,
                reason,
                bases: pendingNotError.suspicion.facts.map((fact) => fact.basis),
              },
              {
                onSuccess: () => setPendingNotError(null),
                onError: (error) => {
                  if (apiErrorCode(error) !== SUSPICION_CHANGED_CODE) return;
                  setPendingNotError(null);
                  toast.error(SUSPICION_CHANGED_NOTICE);
                },
              }
            );
          }}
        />
      )}
      {canLump && (
        <AlertDialog
          open={pendingLump !== null}
          onOpenChange={(next) => {
            if (!next) setPendingLump(null);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{LUMP_ANALOG_CONFIRM_TITLE}</AlertDialogTitle>
              <AlertDialogDescription data-testid="analog-lump-confirm">
                {pendingLump !== null && lumpAnalogConfirm(pendingLump.title)}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel render={<Button variant="outline">{LUMP_ANALOG_CONFIRM_CANCEL}</Button>} />
              <Button
                data-testid="analog-lump-confirm-ok"
                onClick={() => {
                  if (pendingLump !== null) setLump.mutate({ id: pendingLump.analog_id, verdict: "lump" });
                  setPendingLump(null);
                }}
              >
                {LUMP_ANALOG_ACTION}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </Collapsible>
  );
}
