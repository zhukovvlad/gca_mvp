import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useIsMutating } from "@tanstack/react-query";

import { useCurrentUser } from "@/hooks/useAuth";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { kpDecisionMutationKey, useOurPriceLine } from "@/services/queries";
import type { Decimal, OurPriceLine, OurPriceLineView, RungLevel, RungScope } from "@/types/domain";

import { comparedKpPrice, comparedUnit, formatMoney, formatSigned, formatUnitPrice, pick, type VatView } from "./money";
import {
  ALL_CLASSES_LABEL,
  contractNumber,
  contractsShort,
  CONTRACT_LOWER_BOUND,
  CONTRACT_WITH_CONTRACTOR,
  GROUP_OUTSIDE,
  GROUP_THIS_LINE,
  groupLinesCaption,
  groupMembership,
  lineStateLabel,
  manualRungTag,
  priceBasisLabel,
  RECALC_LABEL,
  ratioNote,
  RUNG_AUTO_TAG,
  RUNG_HINT,
  RUNG_LEVEL_LABEL,
  RUNG_RESET_LABEL,
  RUNG_SCOPE_LABEL,
  SAVING_LABEL,
  SHEET_DESCRIPTION,
  SHEET_ERROR_LABEL,
  SHEET_FIELD,
  SHEET_SECTION,
  SHEET_TITLE,
  UNIT_WARN_TAIL,
  UNIT_WARN_TITLE,
  unitWarnContracts,
  unitWarnKp,
  variantCaption,
  VAT_DROPPED_NOTE,
} from "./ourPriceCopy";
import { ExportBlock } from "./ExportBlock";
import { LineStateText } from "./LineStateText";
import { RecalcOverlay } from "./RecalcOverlay";
import { RungSaveBar } from "./RungSaveBar";

export interface LineSheetProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  /** Строка, чья панель открыта; `null` — панель закрыта. */
  positionItemId: number | null;
  seriesId: number | null;
  vat: VatView;
  /** Ставка КП участника: подпись «с НДС 22 %». `null` — база НДС неизвестна. */
  vatRate: Decimal | null;
  /** Подпись «ждёт решения администратора» — ссылка на карточку контекста (только `admin`). */
  canResolve?: boolean;
  onClose: () => void;
}

type Rung = { level: RungLevel; scope: RungScope };

const DASH = "—";

/** Шаг по ступеням лестницы от нажатой стрелки. */
const ARROW_STEP: Partial<Record<string, 1 | -1>> = {
  ArrowRight: 1,
  ArrowDown: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
};

const sameRung = (a: Rung | null, b: Rung | null) => a !== null && b !== null && a.level === b.level && a.scope === b.scope;

/**
 * Панель строки КП (спека 4а §2.12, дизайн 3.5): цены, лестница ступеней, договоры выбранной
 * ступени, выпавшие из-за базы НДС, плашка про единицы учёта и последним разделом «Для
 * выгрузки» (спека 4б §2.14). Выбор ступени — предпросмотр в самой панели: цифры страницы
 * разбора он не трогает (4а §2.5); сохранить его, вернуть авто, вписать «Договорились» и
 * комментарий может только `admin`, остальные видят те же значения без полей. Сохранение идёт
 * под серым слоем: старые цифры видны, пока не придут новые.
 */
export function LineSheet({ positionItemId, onClose, ...rest }: LineSheetProps) {
  return (
    <Sheet
      open={positionItemId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {/* Ширина — тем же вариантным префиксом, что у базового Sheet: простой `sm:max-w-*` его не побеждает. */}
      <SheetContent
        data-testid="line-sheet"
        side="right"
        className="w-full overflow-y-auto data-[side=right]:sm:max-w-lg"
      >
        {positionItemId !== null && <LineSheetBody key={positionItemId} positionItemId={positionItemId} {...rest} />}
      </SheetContent>
    </Sheet>
  );
}

type BodyProps = Omit<LineSheetProps, "positionItemId" | "onClose"> & { positionItemId: number };

/** Живёт, пока открыта одна строка: выбор ступени пропадает вместе с телом при смене строки. */
function LineSheetBody({
  tenderId,
  roundId,
  offerId,
  positionItemId,
  seriesId,
  vat,
  vatRate,
  canResolve = false,
}: BodyProps) {
  const [rung, setRung] = useState<Rung | null>(null);
  const { data: currentUser } = useCurrentUser();
  const canEdit = currentUser?.role === "admin";
  const saving = useIsMutating({ mutationKey: kpDecisionMutationKey(tenderId, roundId, offerId) }) > 0;
  // Автоматическая ступень остаётся известной и при предпросмотре другой: это тот же запрос, пока выбора нет.
  const autoQuery = useOurPriceLine(tenderId, roundId, offerId, positionItemId, null, seriesId);
  const query = useOurPriceLine(tenderId, roundId, offerId, positionItemId, rung, seriesId);
  const data = query.data;
  // Строка на действующей ступени: у неё ручная ступень, лестница и метка «авто» / «ручная».
  const baseLine = autoQuery.data?.line ?? null;

  return (
    <>
      <SheetHeader>
        <SheetTitle>{data === undefined ? SHEET_TITLE : data.line.title}</SheetTitle>
        <SheetDescription>{data === undefined ? SHEET_DESCRIPTION : subtitle(data.line)}</SheetDescription>
      </SheetHeader>
      {data === undefined && query.isError && (
        <div
          role="alert"
          className="mx-4 rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
        >
          {SHEET_ERROR_LABEL}
        </div>
      )}
      {data === undefined && !query.isError && (
        <div className="space-y-2 px-4">
          <Skeleton className="h-4 w-[80%]" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-[60%]" />
        </div>
      )}
      {data !== undefined && (
        <RecalcOverlay active={query.isPlaceholderData || saving} label={saving ? SAVING_LABEL : RECALC_LABEL}>
          <PanelContent
            tenderId={tenderId}
            roundId={roundId}
            offerId={offerId}
            data={data}
            baseLine={baseLine}
            vat={vat}
            vatRate={vatRate}
            canResolve={canResolve}
            canEdit={canEdit}
            rung={rung}
            onRung={setRung}
          />
        </RecalcOverlay>
      )}
    </>
  );
}

function subtitle(line: OurPriceLine): string {
  return variantCaption(line) ?? SHEET_DESCRIPTION;
}

interface PanelContentProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  data: OurPriceLineView;
  /** Строка на действующей ступени; `null`, пока её ответ не пришёл. */
  baseLine: OurPriceLine | null;
  vat: VatView;
  vatRate: Decimal | null;
  canResolve: boolean;
  canEdit: boolean;
  rung: Rung | null;
  onRung: (rung: Rung | null) => void;
}

function PanelContent({
  tenderId,
  roundId,
  offerId,
  data,
  baseLine,
  vat,
  vatRate,
  canResolve,
  canEdit,
  rung,
  onRung,
}: PanelContentProps) {
  const { line } = data;
  const stateText = lineStateLabel(line);
  return (
    <div className="space-y-5 px-4 pb-6">
      {stateText !== null && (
        <p data-testid="sheet-state" className="text-sm text-fg-secondary">
          <LineStateText line={line} label={stateText} canResolve={canResolve} />
        </p>
      )}
      <UnitWarn line={line} vat={vat} />
      <Prices line={line} vat={vat} vatRate={vatRate} />
      {line.rungs.length > 0 && (
        <Ladder
          line={line}
          rung={rung}
          effective={baseLine}
          onRung={onRung}
          footer={
            baseLine === null ? null : (
              <RungSaveBar
                tenderId={tenderId}
                roundId={roundId}
                offerId={offerId}
                line={baseLine}
                previewRung={rung}
                canEdit={canEdit}
                onSaved={() => onRung(null)}
              />
            )
          }
        />
      )}
      {line.rungs.length === 0 && (baseLine ?? line).manual_rung !== null && (
        // Ступень сохранена, а лестницы нет (вариант сняли): без этой плашки причина и «Вернуть авто» пропали бы.
        <RungSaveBar
          tenderId={tenderId}
          roundId={roundId}
          offerId={offerId}
          line={baseLine ?? line}
          previewRung={null}
          canEdit={canEdit}
          onSaved={() => onRung(null)}
        />
      )}
      {data.group_lines.length > 0 && <Family line={line} groupLines={data.group_lines} vat={vat} />}
      {data.contracts.length > 0 && <Contracts contracts={data.contracts} vat={vat} />}
      {data.vat_dropped.length > 0 && (
        <Section title={SHEET_SECTION.vatDropped} testId="sheet-vat-dropped">
          <ul className="space-y-1 text-sm">
            {data.vat_dropped.map((c) => (
              <li key={c.contract_id} data-testid={`vat-dropped-${c.contract_id}`} className="text-fg-secondary">
                {c.object_title} · {c.year} · {contractNumber(c.number)} —{" "}
                <span className="text-warning-text">{VAT_DROPPED_NOTE}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title={SHEET_SECTION.export} testId="sheet-export">
        <ExportBlock
          tenderId={tenderId}
          roundId={roundId}
          offerId={offerId}
          line={baseLine ?? line}
          comment={data.comment}
          canEdit={canEdit}
          vatView={vat}
          vatRate={vatRate}
        />
      </Section>
    </div>
  );
}

function Section({ title, testId, children }: { title: string; testId?: string; children: ReactNode }) {
  return (
    <section data-testid={testId} className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">{title}</h4>
      {children}
    </section>
  );
}

function Prices({ line, vat, vatRate }: { line: OurPriceLine; vat: VatView; vatRate: Decimal | null }) {
  const unitPrice = comparedKpPrice(line, vat);
  const priceUnit = comparedUnit(line);
  const system = line.kind === "system";
  const total = pick(line.kp.total, vat);
  const toOurs = pick(line.to_ours, vat);
  const ours = line.ours === null ? null : pick(line.ours.value, vat);
  const unit = line.unit === null ? "" : ` ${line.unit}`;
  return (
    <Section title={SHEET_SECTION.prices} testId="sheet-prices">
      <p className="text-sm font-medium">{priceBasisLabel(vat, vatRate)}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-fg-secondary">{SHEET_FIELD.kpPrice}</dt>
        <dd data-testid="sheet-kp-price" className="font-mono tabular-nums">
          {unitPrice === null ? DASH : `${formatUnitPrice(unitPrice)} ${priceUnit}`}
        </dd>
        {system && (
          <>
            <dt className="text-fg-secondary">{SHEET_FIELD.kpTotal}</dt>
            <dd data-testid="sheet-kp-total" className="font-mono tabular-nums">
              {total === null ? DASH : formatMoney(total)}
            </dd>
          </>
        )}
        <dt className="text-fg-secondary">{SHEET_FIELD.quantity}</dt>
        <dd data-testid="sheet-quantity" className="font-mono tabular-nums">
          {line.quantity === null ? DASH : `${formatMoney(line.quantity, "")}${unit}`}
        </dd>
        <dt className="text-fg-secondary">{SHEET_FIELD.toOurs}</dt>
        <dd data-testid="sheet-to-ours" className="font-mono tabular-nums">
          {toOurs === null ? DASH : formatSigned(toOurs)}
        </dd>
        <dt className="text-fg-secondary">{SHEET_FIELD.ours}</dt>
        <dd data-testid="sheet-ours" className="font-mono tabular-nums">
          {ours === null ? DASH : `${formatUnitPrice(ours)} ${priceUnit}`}
          {line.ours?.from_all_classes === true && (
            <span className="ml-2 text-xs text-fg-secondary">{ALL_CLASSES_LABEL}</span>
          )}
        </dd>
        {line.secondary.length > 0 && (
          <>
            <dt className="text-fg-secondary">{SHEET_FIELD.secondary}</dt>
            <dd>{line.secondary.map((s) => `${s.name}: ${s.value}`).join("; ")}</dd>
          </>
        )}
      </dl>
    </Section>
  );
}

function Ladder({
  line,
  rung,
  effective,
  onRung,
  footer,
}: {
  line: OurPriceLine;
  rung: Rung | null;
  /** Строка на действующей ступени; `null`, пока её ответ не пришёл. */
  effective: OurPriceLine | null;
  onRung: (rung: Rung | null) => void;
  footer?: ReactNode;
}) {
  const selected: Rung | null = rung ?? line.rung;
  const autoRung = effective?.rung ?? null;
  const manual = effective?.manual_rung ?? null;
  // Действующая ступень — своя метка: «авто», либо «ручная» (у семьи — с пояснением).
  const effectiveTag = manual !== null && manual.active ? manualRungTag(manual.target) : RUNG_AUTO_TAG;
  const available = line.rungs.filter((r) => r.contracts > 0);
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const keyOf = (r: Rung) => `${r.level}-${r.scope}`;

  // Стрелки идут по доступным ступеням по кругу: вправо и вниз — вперёд, влево и вверх — назад.
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, from: Rung) => {
    const step = ARROW_STEP[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const index = available.findIndex((r) => sameRung(from, r));
    const next = available[(index + step + available.length) % available.length];
    onRung({ level: next.level, scope: next.scope });
    refs.current.get(keyOf(next))?.focus();
  };

  return (
    <Section title={SHEET_SECTION.ladder}>
      <div role="radiogroup" aria-label={SHEET_SECTION.ladder} className="space-y-1">
        {line.rungs.map((r) => {
          const checked = sameRung(selected, r);
          return (
            <Button
              key={keyOf(r)}
              ref={(node: HTMLButtonElement | null) => {
                if (node === null) refs.current.delete(keyOf(r));
                else refs.current.set(keyOf(r), node);
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked ? 0 : -1}
              onKeyDown={(event) => onKeyDown(event, r)}
              data-testid={`rung-${r.level}-${r.scope}`}
              variant={checked ? "default" : "outline"}
              disabled={r.contracts === 0}
              className="h-auto w-full justify-between whitespace-normal py-1.5 text-left"
              onClick={() => onRung({ level: r.level, scope: r.scope })}
            >
              <span>
                {RUNG_LEVEL_LABEL[r.level]} · {RUNG_SCOPE_LABEL[r.scope]}
                {sameRung(autoRung, r) && <span className="text-xs opacity-70"> · {effectiveTag}</span>}
              </span>
              <span className="font-mono text-xs">{contractsShort(r.contracts)}</span>
            </Button>
          );
        })}
      </div>
      <p className="text-xs text-fg-secondary">
        {RUNG_HINT}
        {rung !== null && (
          <Button
            type="button"
            variant="link"
            size="sm"
            data-testid="rung-reset"
            className="ml-1 h-auto p-0 text-xs"
            onClick={() => onRung(null)}
          >
            {RUNG_RESET_LABEL}
          </Button>
        )}
      </p>
      {footer}
    </Section>
  );
}

function Family({ line, groupLines, vat }: { line: OurPriceLine; groupLines: OurPriceLine[]; vat: VatView }) {
  return (
    <Section title={SHEET_SECTION.family} testId="sheet-family">
      {line.group !== null && (
        <p data-testid="group-caption" className="text-sm font-medium">
          {groupLinesCaption(line.group.lines)}
        </p>
      )}
      <ul className="space-y-1 text-sm">
        {groupLines.map((member) => {
          const total = pick(member.kp.total, vat);
          return (
            <li
              key={member.position_item_id}
              data-testid={`group-line-${member.position_item_id}`}
              className={cn(
                "flex items-start justify-between gap-3 rounded-md border border-border px-2 py-1",
                member.position_item_id === line.position_item_id && "border-info-border bg-info-soft"
              )}
            >
              <span>
                {member.title}
                <span className="block text-xs text-fg-secondary">
                  {member.position_item_id === line.position_item_id && `${GROUP_THIS_LINE} · `}
                  {member.group === null ? GROUP_OUTSIDE : groupMembership(member.group.lines)}
                </span>
              </span>
              <span className="font-mono tabular-nums">{total === null ? DASH : formatMoney(total, "")}</span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function Contracts({ contracts, vat }: { contracts: OurPriceLineView["contracts"]; vat: VatView }) {
  return (
    <Section title={SHEET_SECTION.contracts} testId="sheet-contracts">
      <ul className="space-y-1">
        {contracts.map((c) => {
          const rate = pick(c.rate, vat);
          return (
            <li
              key={c.contract_id}
              data-testid={`contract-${c.contract_id}`}
              data-lower-bound={String(c.is_lower_bound)}
              className={cn(
                "flex items-start justify-between gap-3 rounded-md border border-border px-2 py-1 text-sm",
                c.is_lower_bound && "border-info-border bg-info-soft",
                c.ratio !== null && "border-warning-border bg-warning-soft"
              )}
            >
              <span>
                {c.object_title} · {c.year} · {c.rate_class.title}
                <span className="block text-xs text-fg-secondary">
                  {contractNumber(c.number)} · {c.contractor_title}
                </span>
                {(c.is_lower_bound || c.with_this_contractor) && (
                  <span className="mt-0.5 flex flex-wrap gap-1">
                    {c.is_lower_bound && <Badge variant="outline">{CONTRACT_LOWER_BOUND}</Badge>}
                    {c.with_this_contractor && <Badge variant="secondary">{CONTRACT_WITH_CONTRACTOR}</Badge>}
                  </span>
                )}
                {c.ratio !== null && <span className="block text-xs text-warning-text">{ratioNote(c.ratio)}</span>}
              </span>
              <span className="font-mono tabular-nums">{rate === null ? DASH : formatUnitPrice(rate)}</span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function UnitWarn({ line, vat }: { line: OurPriceLine; vat: VatView }) {
  const mismatch = line.unit_mismatch;
  if (mismatch === null) return null;
  const median = line.range === null ? null : pick(line.range.median, vat);
  return (
    <div
      role="note"
      data-testid="unit-warn"
      className="space-y-1 rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
    >
      <p className="font-semibold">{UNIT_WARN_TITLE}</p>
      {mismatch.kp_ratio !== null && (
        <p>{unitWarnKp(mismatch.kp_ratio, median === null ? null : formatUnitPrice(median))}</p>
      )}
      {mismatch.contracts.length > 0 && <p>{unitWarnContracts(mismatch.contracts.map((c) => c.ratio))}</p>}
      <p>{UNIT_WARN_TAIL}</p>
    </div>
  );
}
