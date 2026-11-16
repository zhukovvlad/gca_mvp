import { useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import type { AxiosError } from "axios";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { roundDecimalPercent } from "@/lib/format";
import { linkRefusalText } from "@/pages/families/labels";
import {
  apiErrorContext,
  apiErrorCode,
  apiErrorDetail,
  linkInvalidReason,
  useCreateReferenceLink,
  type PutReferenceTargetInput,
} from "@/services/queries";
import type { FamilyLinkBody, ReferenceConversionBody, ReferenceOrigin, RefRow } from "@/types/domain";

import { formatUnitPrice, pick, priceUnitLabel, type VatView } from "./money";
import { needsConversion } from "./needsConversion";
import {
  analogOutlierNote,
  REF_DIALOG,
  REF_OUTLIER_CONFIRM,
  REF_REMEMBER,
  refDeviation,
} from "./ourPriceCopy";
import { linkKindsFor, useRememberLink } from "./rememberLink";
import { RememberLinkFields } from "./RememberLinkFields";

/** Строка, которую делают целью, и путь, которым она выбрана. `ratio` — отношение, которого потребовал сервер. */
export interface ReferenceTarget {
  row: RefRow;
  origin: ReferenceOrigin;
  ratio?: string;
}

export interface ReferenceTargetDialogProps {
  target: ReferenceTarget | null;
  /** Свежая версия строки из последнего ответа панели или поиска: после `409` цена подменяется ею. */
  freshRow: (analogId: number) => RefRow | null;
  tenderId: number;
  roundId: number;
  offerId: number;
  positionItemId: number;
  kpUnit: string | null;
  /** Семья строки КП; `null` - «Запомнить для семей» недоступно. */
  kpFamilyId: number | null;
  vat: VatView;
  mutation: UseMutationResult<void, AxiosError, PutReferenceTargetInput>;
  onClose: () => void;
}

const FACTOR_RE = /^\d+(?:[.,]\d{1,6})?$/;

/**
 * Диалог справочной цели (спека справочных аналогов §2.13, §2.18). Единицы различны: пересчёт по связи
 * (только просмотр `N` и основания) либо ручной ввод; без основания кнопка неактивна. `409 conversion_stale`
 * подменяет `N` и основание пришедшими, цену подтягивает перезапрос панели, и диалог ждёт нового нажатия.
 * Единицы равны, а строка помечена выбросом, — диалог только подтверждает выбор. Деньги и `N` идут строками.
 */
export function ReferenceTargetDialog(props: ReferenceTargetDialogProps) {
  const { target, onClose } = props;
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent data-testid="reference-dialog" className="sm:max-w-lg">
        {/* Тело живёт, пока открыта одна строка: набранное и подмена `N` пропадают вместе с ним. */}
        {target !== null && <DialogBody key={target.row.analog_id} {...props} target={target} />}
      </DialogContent>
    </Dialog>
  );
}

function DialogBody({
  target,
  freshRow,
  tenderId,
  roundId,
  offerId,
  positionItemId,
  kpUnit,
  kpFamilyId,
  vat,
  mutation,
  onClose,
}: ReferenceTargetDialogProps & { target: ReferenceTarget }) {
  const row = freshRow(target.row.analog_id) ?? target.row;
  const convert = needsConversion(row, kpUnit);
  const link = row.conversion?.method === "link" ? row.conversion : null;
  const [stale, setStale] = useState<{ factor: string; factorShown: string; basis: string } | null>(null);
  const [factorDraft, setFactorDraft] = useState("");
  const [basisDraft, setBasisDraft] = useState("");
  const [demandedRatio, setDemandedRatio] = useState<string | null>(target.ratio ?? null);
  const [manualError, setManualError] = useState<string | null>(null);
  const [linkRefusal, setLinkRefusal] = useState<string | null>(null);
  const [linkNote, setLinkNote] = useState<string | null>(null);
  const createLink = useCreateReferenceLink(tenderId, roundId, offerId, positionItemId);
  const kinds = linkKindsFor(kpUnit, row.unit);
  const remember = useRememberLink(kinds, kpFamilyId, row.family_id);
  // Связь можно завести, когда её нет (в группе блока Г она есть по построению), семьи обеих строк известны и различны.
  const canRemember =
    convert &&
    link === null &&
    linkNote === null &&
    target.origin !== "link" &&
    kpFamilyId !== null &&
    row.family_id !== null &&
    kpFamilyId !== row.family_id &&
    kinds.length > 0;
  const rememberOn = canRemember && remember.on;
  const paramKindOn = rememberOn && !remember.isConstant;

  const shown = stale ?? (link === null ? null : { factor: link.factor, factorShown: link.factor_shown, basis: link.basis });
  const outlierRatio = row.outlier_ratio ?? demandedRatio;
  const price = pick(row.price, vat);
  const inKp = pick(row.kp_unit_price, vat);
  const deviation = roundDecimalPercent(row.deviation_pct, 0);

  const factor = factorDraft.trim();
  const basis = basisDraft.trim();
  const manualValid = FACTOR_RE.test(factor) && Number.parseFloat(factor.replace(",", ".")) > 0 && basis !== "";
  const ready =
    !convert ||
    (paramKindOn ? remember.paramsReady : link !== null ? shown !== null : manualValid);

  const conversionBody = (): ReferenceConversionBody => {
    if (!convert) return null;
    if (link !== null && shown !== null) {
      return { method: "link", link_id: link.link_id, expected_factor: shown.factor, expected_basis: shown.basis };
    }
    return { method: "manual", factor: factor.replace(",", "."), basis };
  };

  const putTarget = (conversion: ReferenceConversionBody) => {
    mutation.mutate(
      {
        positionItemId,
        analogId: row.analog_id,
        origin: target.origin,
        conversion,
        confirmOutlier: outlierRatio !== null,
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          const code = apiErrorCode(error);
          if (code === "conversion_stale") {
            const current = apiErrorContext<{ factor: string; factor_shown: string; basis: string }>(error);
            if (current !== undefined) {
              setStale({ factor: current.factor, factorShown: current.factor_shown, basis: current.basis });
            }
          } else if (code === "outlier_confirmation_required") {
            setDemandedRatio(apiErrorContext<{ ratio: string }>(error)?.ratio ?? "1");
          } else if (code === "manual_conversion_invalid") {
            setManualError(apiErrorDetail(error) ?? REF_DIALOG.invalidManual);
          }
        },
      }
    );
  };

  const linkBody = (): FamilyLinkBody | null => {
    if (kpFamilyId === null || row.family_id === null) return null;
    const pair = { family_a_id: kpFamilyId, family_b_id: row.family_id };
    if (remember.isConstant) {
      return {
        ...pair,
        kind: "constant",
        param_a_id: null,
        param_b_id: null,
        factor_from_family_id: row.family_id,
        factor: factor.replace(",", "."),
        basis,
      };
    }
    return {
      ...pair,
      kind: remember.kind,
      param_a_id: remember.paramA === 0 ? null : remember.paramA,
      param_b_id: remember.paramB === 0 ? null : remember.paramB,
      factor_from_family_id: null,
      factor: null,
      basis: null,
    };
  };

  const submit = async () => {
    setManualError(null);
    setLinkRefusal(null);
    const body = rememberOn ? linkBody() : null;
    if (body === null) {
      putTarget(conversionBody());
      return;
    }
    // Связь и пересчёт по ней - от сервера: цель пишется с `link_id`, `N` и основанием перечитанной строки.
    let fresh: RefRow | null;
    try {
      fresh = await createLink.mutateAsync({ body, analogId: row.analog_id });
    } catch (error) {
      setLinkRefusal(linkRefusalText(apiErrorCode(error), linkInvalidReason(error)));
      return;
    }
    if (fresh?.conversion?.method === "link") {
      putTarget({
        method: "link",
        link_id: fresh.conversion.link_id,
        expected_factor: fresh.conversion.factor,
        expected_basis: fresh.conversion.basis,
      });
      return;
    }
    remember.setOn(false);
    setLinkNote(fresh?.no_conversion_reason ?? REF_REMEMBER.noFactor);
  };

  const staleShown = stale !== null && mutation.isError && apiErrorCode(mutation.error) === "conversion_stale";
  return (
    <>
      <DialogHeader>
        <DialogTitle>{convert ? REF_DIALOG.title : REF_OUTLIER_CONFIRM.title}</DialogTitle>
        <DialogDescription>{convert ? REF_DIALOG.description : ""}</DialogDescription>
      </DialogHeader>
      <div className="space-y-3 text-sm">
        <div data-testid="reference-dialog-row" className="space-y-1">
          <p className="text-xs text-fg-secondary">
            {row.contract.object_title} · дог. {row.contract.number} · {row.contract.year}
          </p>
          <p className="break-words">{row.title}</p>
          <p className="font-mono tabular-nums">
            {price === null ? "—" : `${formatUnitPrice(price)} ${priceUnitLabel(row.unit)}`}
          </p>
        </div>
        {convert && (
          <div className="space-y-2">
            {link !== null && shown !== null ? (
              <div data-testid="reference-dialog-link" className="space-y-1">
                <p className="text-xs text-fg-secondary">{REF_DIALOG.linkNote}</p>
                <p>
                  <span className="text-fg-secondary">1 {row.unit ?? "ед."} = </span>
                  <span data-testid="reference-dialog-factor" className="font-mono tabular-nums">
                    {shown.factorShown}
                  </span>
                  <span className="text-fg-secondary"> {kpUnit ?? "ед."}</span>
                </p>
                <p data-testid="reference-dialog-basis">
                  <span className="text-fg-secondary">{REF_DIALOG.basisLabel}: </span>
                  {shown.basis}
                </p>
              </div>
            ) : (
              <div data-testid="reference-dialog-manual" className="space-y-2">
                {!paramKindOn && <p className="text-xs text-fg-secondary">{REF_DIALOG.manualNote}</p>}
                {linkNote !== null && (
                  <p role="status" data-testid="reference-dialog-link-note" className="text-sm text-fg-secondary">
                    {linkNote}
                  </p>
                )}
                {canRemember && <RememberLinkFields state={remember} kinds={kinds} disabled={createLink.isPending} />}
                {linkRefusal !== null && (
                  <p role="alert" data-testid="reference-dialog-link-refusal" className="text-sm text-danger-text">
                    {linkRefusal}
                  </p>
                )}
                {!paramKindOn && (
                  <>
                    <div className="grid gap-1.5">
                      <Label htmlFor="reference-factor">
                        1 {row.unit ?? "ед."} = N {kpUnit ?? "ед."}
                      </Label>
                      <Input
                        id="reference-factor"
                        inputMode="decimal"
                        aria-label={REF_DIALOG.nameLabel}
                        value={factorDraft}
                        onChange={(e) => setFactorDraft(e.target.value)}
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label htmlFor="reference-basis">{REF_DIALOG.basisLabel}</Label>
                      <Input
                        id="reference-basis"
                        placeholder={REF_DIALOG.basisPlaceholder}
                        value={basisDraft}
                        onChange={(e) => setBasisDraft(e.target.value)}
                      />
                    </div>
                  </>
                )}
                {manualError !== null && (
                  <p role="alert" data-testid="reference-dialog-manual-error" className="text-sm text-danger-text">
                    {manualError}
                  </p>
                )}
              </div>
            )}
            {link !== null && inKp !== null && (
              <p data-testid="reference-dialog-kp-price" className="font-mono tabular-nums">
                <span className="font-sans text-fg-secondary">{REF_DIALOG.priceInKp}: </span>
                {formatUnitPrice(inKp)} {priceUnitLabel(kpUnit)}
                {deviation !== null && <span className="ml-2 text-danger-text">{refDeviation(deviation.text)}</span>}
              </p>
            )}
            {staleShown && (
              <p role="alert" data-testid="reference-dialog-stale" className="rounded-md bg-warning-soft px-2 py-1 text-warning-text">
                {REF_DIALOG.stale}
              </p>
            )}
          </div>
        )}
        {outlierRatio !== null && (
          <p
            role="note"
            data-testid="reference-dialog-outlier"
            className="rounded-md border border-warning-border bg-warning-soft px-2 py-1 text-warning-text"
          >
            {analogOutlierNote(outlierRatio)}
          </p>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          {REF_DIALOG.cancel}
        </Button>
        <Button
          type="button"
          data-testid="reference-dialog-submit"
          disabled={!ready || mutation.isPending || createLink.isPending}
          onClick={() => void submit()}
        >
          {mutation.isPending || createLink.isPending ? REF_DIALOG.saving : outlierRatio !== null && !convert ? REF_OUTLIER_CONFIRM.confirm : REF_DIALOG.submit}
        </Button>
      </DialogFooter>
    </>
  );
}
