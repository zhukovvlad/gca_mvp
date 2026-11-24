import { Fragment, useState } from "react";

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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { formatDate } from "@/lib/format";
import { useClearMark } from "@/services/queries";
import type { ExcludedAnalog, LineIds, MarkView, WorkRung } from "@/types/domain";

import { groupByStep, stepKey } from "./excludedModel";

import { LineWhere } from "./LineWhere";
import { formatUnitPrice, pick, type VatView } from "./money";
import {
  EXCLUDED_HINT,
  excludedPairLabel,
  excludedTitle,
  excludedWouldBe,
  MARK_CANCEL,
  MARK_CLEAR,
  MARK_CLEAR_CONFIRM_OK,
  MARK_CLEAR_CONFIRM_TITLE,
  MARK_KIND_LABEL,
  markClearConfirmText,
  periodRu,
} from "./ourPriceCopy";

const DASH = "—";

interface ExcludedAnalogsProps {
  items: ExcludedAnalog[];
  /** Ступень, на которой подбор остановился; `null` — аналогов нет (причина «исключены вручную»). */
  workRung: WorkRung | null;
  vat: VatView;
  /** «Снять» — только `admin`. */
  canEdit: boolean;
  ids: LineIds;
  /** Семья строки КП: называется в подтверждении снятия «ошибки в цене». */
  family?: string | null;
}

/**
 * «Исключены (N)» (спека «Не аналог» §8 п. 2): свёрнутый список строк договоров, которые без отметок
 * стояли бы в аналогах, с отметками по отдельности и их «Снять». У строки без ступени список раскрыт
 * сразу: это всё, что есть у строки. Нет исключённых — блока нет.
 */
export function ExcludedAnalogs({ items, workRung, vat, canEdit, ids, family = null }: ExcludedAnalogsProps) {
  const clear = useClearMark();
  const [pendingClear, setPendingClear] = useState<ExcludedAnalog | null>(null);
  if (items.length === 0) return null;
  const groups = groupByStep(items);
  const workKey = workRung === null ? null : stepKey({ rung: workRung.no, zone_scope: workRung.zone_scope });

  const clearButton = (item: ExcludedAnalog, mark: MarkView) => (
    <Button
      type="button"
      size="sm"
      variant="link"
      className="h-auto justify-start p-0 text-xs"
      disabled={clear.isPending}
      onClick={() => {
        if (mark.kind === "price_error") setPendingClear(item);
        else clear.mutate({ ...ids, kind: "tender", positionItemId: item.analog_id });
      }}
    >
      {MARK_CLEAR[mark.kind]}
    </Button>
  );

  const card = (item: ExcludedAnalog) => {
    const price = pick(item.price, vat);
    return (
      <div
        key={item.analog_id}
        data-testid={`excluded-${item.analog_id}`}
        className="space-y-1.5 border-t border-border pt-3 text-sm opacity-75"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <b>{item.object_title}</b>
            <span className="text-xs text-fg-secondary">
              {" "}
              · дог. {item.number} · {item.contractor_title}
            </span>
          </div>
          <div className="shrink-0 text-right">
            <span data-testid={`excluded-price-${item.analog_id}`} className="font-mono font-semibold tabular-nums">
              {price === null ? DASH : formatUnitPrice(price)}
            </span>{" "}
            <span className="font-mono text-xs text-fg-secondary tabular-nums">{periodRu(item.period)}</span>
          </div>
        </div>
        <p className="break-words text-fg-secondary" title={item.title}>
          {item.title}
        </p>
        <LineWhere article={item.article} chapterPath={item.chapter_path} scope="contract" />
        {workKey !== null && stepKey(item.step) !== workKey && (
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className="h-auto whitespace-normal py-0.5 text-left">
              {excludedWouldBe(item.step)}
            </Badge>
          </div>
        )}
        {item.marks.map((mark) => (
          <div key={mark.kind} data-testid={`excluded-mark-${item.analog_id}`} className="space-y-1">
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="secondary" className="h-auto whitespace-normal py-0.5 text-left">
                {MARK_KIND_LABEL[mark.kind]}
              </Badge>
            </div>
            <p className="break-words text-xs text-fg-secondary">
              «{mark.reason}» · {mark.set_by.email} · {formatDate(mark.set_at)}
            </p>
            {canEdit && clearButton(item, mark)}
          </div>
        ))}
      </div>
    );
  };

  return (
    <Collapsible
      defaultOpen={workRung === null}
      data-testid="excluded-list"
      className="space-y-3 rounded-lg border border-dashed border-border bg-surface-sunken px-3 py-2.5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <CollapsibleTrigger render={<Button type="button" size="sm" variant="ghost" className="-ml-2 font-semibold" />}>
          {excludedTitle(items.length)}
        </CollapsibleTrigger>
        {workRung !== null && <span className="text-xs text-fg-secondary">{EXCLUDED_HINT}</span>}
      </div>
      <CollapsibleContent>
        <div className="space-y-3">
          {groups.map((group) => (
            <Fragment key={stepKey(group.step)}>
              {groups.length > 1 && (
                <p data-testid="excluded-pair" className="text-xs text-fg-tertiary">
                  {excludedPairLabel(group.step)}
                </p>
              )}
              {group.items.map(card)}
            </Fragment>
          ))}
        </div>
      </CollapsibleContent>
      <AlertDialog
        open={pendingClear !== null}
        onOpenChange={(next) => {
          if (!next) setPendingClear(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{MARK_CLEAR_CONFIRM_TITLE}</AlertDialogTitle>
            <AlertDialogDescription data-testid="excluded-clear-confirm">
              {pendingClear !== null &&
                markClearConfirmText(
                  { title: pendingClear.title, objectTitle: pendingClear.object_title },
                  family,
                  pendingClear.marks.some((m) => m.kind === "tender") ? ids.tenderId : null
                )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">{MARK_CANCEL}</Button>} />
            <Button
              type="button"
              onClick={() => {
                if (pendingClear !== null) {
                  clear.mutate({ ...ids, kind: "price_error", positionItemId: pendingClear.analog_id });
                }
                setPendingClear(null);
              }}
            >
              {MARK_CLEAR_CONFIRM_OK}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Collapsible>
  );
}
