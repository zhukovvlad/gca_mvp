import { useEffect, useRef, useState } from "react";

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
import { formatDate } from "@/lib/format";
import { formatMoney } from "@/pages/tenders/our-price/money";
import {
  MARK_CANCEL,
  MARK_CLEAR,
  MARK_CLEAR_CONFIRM_OK,
  MARK_CLEAR_CONFIRM_TITLE,
  MARK_KIND_LABEL,
  markClearConfirmText,
} from "@/pages/tenders/our-price/ourPriceCopy";
import { useClearNotError, useClearPriceErrorFromList } from "@/services/queries";
import type { ResolvedView } from "@/types/domain";

import {
  basisText,
  decisionByline,
  factorWords,
  factSentence,
  LINE_OLD_ESTIMATE,
  NOT_ERROR_CLEAR,
  priceNoteText,
  priceSuffix,
  RESOLVED_NOT_ERROR,
  RULE_NOT_CATCHING,
} from "./suspiciousCopy";

function ResolvedPrice({ item }: { item: ResolvedView }) {
  if (item.net !== null) {
    return (
      <div className="whitespace-nowrap">
        <b data-testid="line-price" className="font-mono text-[17px]">
          {formatMoney(item.net, "")}
        </b>{" "}
        <span className="text-2xs text-fg-secondary">{priceSuffix(item.unit)}</span>
      </div>
    );
  }
  return (
    <div data-testid="line-price-note" className="text-right text-xs text-fg-secondary">
      {item.price_note === "vat_unknown" && item.gross !== null && (
        <b className="font-mono text-[17px] whitespace-nowrap text-fg">{formatMoney(item.gross, "")} </b>
      )}
      {item.price_note !== null && priceNoteText(item.price_note, item)}
    </div>
  );
}

/** Пункт «Разобраны»: решение, его основание, причина, автор, дата и что правило говорит сейчас. */
export function ResolvedItem({ item, focused }: { item: ResolvedView; focused: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [confirming, setConfirming] = useState(false);
  const clearNotError = useClearNotError();
  const clearPriceError = useClearPriceErrorFromList();
  const familyTitle = item.facts.find((fact) => fact.kind === "outlier");

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "start" });
  }, [focused]);

  return (
    <div
      ref={ref}
      id={`suspicious-line-${item.position_item_id}`}
      data-testid={`resolved-${item.position_item_id}`}
      className="grid min-w-0 gap-1.5 rounded-[10px] border border-border-subtle border-l-4 border-l-fg-muted p-3 text-[13px]"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <b className="break-words">{item.title}</b>
          {item.unit !== null && <span className="text-2xs text-fg-secondary"> · {item.unit}</span>}
        </div>
        <ResolvedPrice item={item} />
      </div>
      <div className="text-2xs text-fg-secondary">
        {item.contract.object_title} · дог. {item.contract.number}
        {item.item_number !== null && ` · п. ${item.item_number}`}
        {!item.latest && ` · ${LINE_OLD_ESTIMATE}`}
      </div>

      {item.facts.length === 0 ? (
        <p data-testid="rule-now" className="text-xs text-fg-secondary">
          {RULE_NOT_CATCHING}
        </p>
      ) : (
        item.facts.map((fact, index) => (
          <p key={index} data-testid="rule-now" className="text-xs text-fg-secondary">
            Правило сейчас: {fact.kind === "outlier" ? `${factorWords(fact.ratio, fact.direction)} обычного. ` : ""}
            {factSentence(fact, { unit: item.unit, net: item.net })}
          </p>
        ))
      )}

      {item.price_error !== null && (
        <div data-testid="resolved-price-error" className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Badge variant="secondary">{MARK_KIND_LABEL.price_error}</Badge>
          <span className="text-2xs text-fg-secondary">
            «{item.price_error.reason}» · {item.price_error.set_by.email} · {formatDate(item.price_error.set_at)}
          </span>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            data-testid="clear-price-error"
            onClick={() => setConfirming(true)}
          >
            {MARK_CLEAR.price_error}
          </Button>
        </div>
      )}

      {item.not_error.length > 0 && (
        <div className="grid gap-1" data-testid="resolved-not-error">
          {item.not_error.map((decision, index) => (
            <div key={index} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Badge variant="outline">{RESOLVED_NOT_ERROR}</Badge>
              <span className="text-xs text-fg-secondary">Проверено: {basisText(decision.basis)}.</span>
              <span className="text-2xs text-fg-secondary">{decisionByline(decision)}</span>
            </div>
          ))}
          <div>
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              data-testid="clear-not-error"
              disabled={clearNotError.isPending}
              onClick={() => clearNotError.mutate(item.position_item_id)}
            >
              {NOT_ERROR_CLEAR}
            </Button>
          </div>
        </div>
      )}

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{MARK_CLEAR_CONFIRM_TITLE}</AlertDialogTitle>
            <AlertDialogDescription data-testid="clear-confirm-text">
              {markClearConfirmText(
                { title: item.title, objectTitle: item.contract.object_title },
                familyTitle?.kind === "outlier" ? familyTitle.family_title : null,
                null
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">{MARK_CANCEL}</Button>} />
            <Button
              type="button"
              data-testid="clear-confirm-ok"
              disabled={clearPriceError.isPending}
              onClick={() =>
                clearPriceError.mutate(item.position_item_id, { onSuccess: () => setConfirming(false) })
              }
            >
              {MARK_CLEAR_CONFIRM_OK}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
