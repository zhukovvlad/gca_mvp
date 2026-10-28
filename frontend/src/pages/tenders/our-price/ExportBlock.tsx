import { useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  apiErrorDetail,
  isFieldRefusal,
  useDeleteAgreed,
  useDeleteComment,
  usePutAgreed,
  usePutComment,
} from "@/services/queries";
import type { Decimal, OurPriceLine, OurPriceLineView } from "@/types/domain";

import { formatMoney, formatUnitPrice, pick, priceUnitLabel, type VatView } from "./money";
import {
  AGREED_HINT,
  AGREED_PLACEHOLDER,
  agreedLabel,
  COMMENT_LABEL,
  COMMENT_NO_TEMPLATE_HINT,
  COMMENT_PLACEHOLDER,
  COMMENT_TAG,
  COMMENT_TAG_EMPTY,
  COMMENT_TO_TEMPLATE,
  EXPORT_OURS_LABEL,
  EXPORT_OURS_NOTE,
  SAVE_LABEL,
  VAT_NET_LABEL,
  vatShownLabel,
} from "./ourPriceCopy";

export interface ExportBlockProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  line: OurPriceLine;
  comment: OurPriceLineView["comment"];
  /** Правка решений — только `admin`; у остальных те же значения без полей и кнопок. */
  canEdit: boolean;
  vatView: VatView;
  /** Ставка КП; `null` — база НДС неизвестна, ввод только нетто. */
  vatRate: Decimal | null;
}

const DASH = "—";

/** Ввод как есть, но без пробелов и с точкой: проверку числа делает сервер. */
const normalizeInput = (text: string) => text.replace(/\s/g, "").replace(/,/g, ".");

/** Число для поля ввода: два знака, без разделителей разрядов. */
const inputText = (value: Decimal) => normalizeInput(formatMoney(value, ""));

function commentTag(comment: OurPriceLineView["comment"]): string | null {
  if (comment.kind === null) return null;
  if (comment.kind === "template") return COMMENT_TAG.template;
  return comment.text === "" ? COMMENT_TAG_EMPTY : COMMENT_TAG.custom;
}

/**
 * Блок «Для выгрузки» (спека 4б §2.14): наша расценка справкой, «Договорились» и комментарий
 * для подрядчика. Сохранение — явной кнопкой; отказ уровня поля стоит у поля, а не тостом.
 */
export function ExportBlock({ tenderId, roundId, offerId, line, comment, canEdit, vatView, vatRate }: ExportBlockProps) {
  const positionItemId = line.position_item_id;
  const shownRate = vatView === "shown" ? vatRate : null;
  const basis = shownRate !== null ? "shown" : "net";
  const ours = line.ours === null ? null : pick(line.ours.value, vatView);
  const unit = priceUnitLabel(line.unit);

  const putAgreed = usePutAgreed(tenderId, roundId, offerId);
  const deleteAgreed = useDeleteAgreed(tenderId, roundId, offerId);
  const putComment = usePutComment(tenderId, roundId, offerId);
  const deleteComment = useDeleteComment(tenderId, roundId, offerId);

  const [agreedDraft, setAgreedDraft] = useState<string | null>(null);
  const [agreedError, setAgreedError] = useState<string | null>(null);
  const [commentDraft, setCommentDraft] = useState<string | null>(null);
  const [commentError, setCommentError] = useState<string | null>(null);

  const agreedPrice = line.agreed === null ? null : pick(line.agreed, basis);
  const agreedSaved = agreedPrice === null ? "" : inputText(agreedPrice);
  const agreedText = agreedDraft ?? agreedSaved;
  const agreedValue = normalizeInput(agreedText);
  const agreedBusy = putAgreed.isPending || deleteAgreed.isPending;
  // Пустое поле у строки без решения — не правка: удалять нечего.
  const agreedDirty =
    agreedDraft !== null && agreedDraft !== agreedSaved && !(agreedValue === "" && line.agreed === null);

  const commentSaved = comment.text ?? "";
  const commentText = commentDraft ?? commentSaved;
  const commentDirty = commentDraft !== null && commentDraft !== commentSaved;
  const commentBusy = putComment.isPending || deleteComment.isPending;
  const tag = commentTag(comment);

  const fieldError = (set: (message: string | null) => void) => (error: unknown) => {
    if (isFieldRefusal(error)) set(apiErrorDetail(error) ?? "");
  };

  const saveAgreed = () => {
    if (!agreedDirty || agreedBusy) return;
    const done = {
      onSuccess: () => setAgreedDraft(null),
      onError: fieldError(setAgreedError),
    };
    if (agreedValue === "") deleteAgreed.mutate({ positionItemId }, done);
    else putAgreed.mutate({ positionItemId, value: agreedValue, basis }, done);
  };

  const saveComment = () => {
    if (!commentDirty || commentBusy) return;
    putComment.mutate(
      { positionItemId, text: commentText },
      { onSuccess: () => setCommentDraft(null), onError: fieldError(setCommentError) }
    );
  };

  const toTemplate = () => {
    deleteComment.mutate(
      { positionItemId },
      { onSuccess: () => setCommentDraft(null), onError: fieldError(setCommentError) }
    );
  };

  const onAgreedKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    saveAgreed();
  };

  const agreedCaption = agreedLabel(line.unit, shownRate !== null ? vatShownLabel(shownRate) : VAT_NET_LABEL);

  return (
    <div data-testid="export-block" className="space-y-4 text-sm">
      <p>
        <span className="text-fg-secondary">{EXPORT_OURS_LABEL}</span>{" "}
        <span data-testid="export-ours" className="font-mono tabular-nums">
          {ours === null ? DASH : `${formatUnitPrice(ours)} ${unit}`}
          <span className="font-sans text-xs text-fg-secondary"> · {EXPORT_OURS_NOTE}</span>
        </span>
      </p>

      <div className="space-y-1">
        {canEdit ? (
          <>
            <Label htmlFor="agreed-input">{agreedCaption}</Label>
            <div className="flex items-start gap-2">
              <Input
                id="agreed-input"
                inputMode="decimal"
                autoComplete="off"
                value={agreedText}
                placeholder={AGREED_PLACEHOLDER}
                aria-invalid={agreedError !== null || undefined}
                onChange={(event) => {
                  setAgreedDraft(event.target.value);
                  setAgreedError(null);
                }}
                onKeyDown={onAgreedKey}
              />
              <Button type="button" data-testid="agreed-save" disabled={!agreedDirty || agreedBusy} onClick={saveAgreed}>
                {SAVE_LABEL}
              </Button>
            </div>
            {agreedError !== null && (
              <p role="alert" data-testid="agreed-error" className="text-xs text-danger-text">
                {agreedError}
              </p>
            )}
          </>
        ) : (
          <>
            <p className="text-fg-secondary">{agreedCaption}</p>
            <p data-testid="agreed-value" className="font-mono tabular-nums">
              {agreedPrice === null ? AGREED_PLACEHOLDER : formatUnitPrice(agreedPrice)}
            </p>
          </>
        )}
        <p className="text-xs text-fg-secondary">{AGREED_HINT}</p>
      </div>

      <div className="space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-baseline gap-2">
            {canEdit ? <Label htmlFor="comment-input">{COMMENT_LABEL}</Label> : <span>{COMMENT_LABEL}</span>}
            {tag !== null && (
              <span data-testid="comment-tag" className="text-xs text-fg-secondary">
                {tag}
              </span>
            )}
          </span>
          {canEdit && comment.kind === "custom" && (
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              title={comment.template_text ?? COMMENT_NO_TEMPLATE_HINT}
              disabled={commentBusy}
              onClick={toTemplate}
            >
              {COMMENT_TO_TEMPLATE}
            </Button>
          )}
        </div>
        {canEdit ? (
          <>
            <Textarea
              id="comment-input"
              value={commentText}
              placeholder={COMMENT_PLACEHOLDER}
              aria-invalid={commentError !== null || undefined}
              onChange={(event) => {
                setCommentDraft(event.target.value);
                setCommentError(null);
              }}
            />
            {commentError !== null && (
              <p role="alert" data-testid="comment-error" className="text-xs text-danger-text">
                {commentError}
              </p>
            )}
            <Button type="button" data-testid="comment-save" disabled={!commentDirty || commentBusy} onClick={saveComment}>
              {SAVE_LABEL}
            </Button>
          </>
        ) : (
          <p data-testid="comment-text" className="whitespace-pre-wrap">
            {commentSaved === "" ? COMMENT_PLACEHOLDER : commentSaved}
          </p>
        )}
      </div>
    </div>
  );
}
