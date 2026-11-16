import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { compareDecimalStrings } from "@/lib/decimal";
import { formatDate, roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { MoneyPair, OurPriceAnalog, OurPriceLine } from "@/types/domain";

import { absDecimal, formatSigned, formatUnitPrice, pick, priceUnitLabel, type VatView } from "./money";
import {
  FIGURE_KP_ABOVE,
  FIGURE_KP_BELOW,
  FIGURE_KP_EQUAL,
  FIGURE_NONE,
  periodRu,
  REF_TARGET,
  refDeviation,
  refTargetConversionLine,
  refTargetConvertedFrom,
  refTargetInactive,
  refTargetLinkNow,
  TARGET_CARD,
  targetInactiveNote,
  targetSameContract,
} from "./ourPriceCopy";
import { selectedTarget, type TargetStrategy } from "./targetSelection";

export type TargetMarkKind = TargetStrategy | "target";

const MARK_ICON: Record<TargetMarkKind, string> = { lowest: "↓", freshest: "◷", contractor: "◆", target: "◎" };
const MARK_LABEL: Record<TargetMarkKind, string> = {
  lowest: TARGET_CARD.lowest,
  freshest: TARGET_CARD.freshest,
  contractor: TARGET_CARD.contractor,
  target: TARGET_CARD.target,
};
/** Заливка метки: цвета из `index.css` парой светлая / тёмная; «цель» — цвет действия. */
const MARK_CLASS: Record<TargetMarkKind, string> = {
  lowest: "bg-mark-lowest text-mark-on",
  freshest: "bg-mark-freshest text-mark-on",
  contractor: "bg-mark-contractor text-mark-on",
  target: "bg-action text-action-text",
};
const CARD_TOP: Record<TargetStrategy | "pinned", string> = {
  lowest: "border-t-mark-lowest",
  freshest: "border-t-mark-freshest",
  contractor: "border-t-mark-contractor",
  pinned: "border-t-action",
};

/** Цветная метка цели: один цвет на карточке цели и на аналоге, по нему видно, какой аналог за какой цифрой. */
export function TargetMark({ kind, className }: { kind: TargetMarkKind; className?: string }) {
  return (
    <Badge className={cn("h-auto gap-1 py-1 font-semibold", MARK_CLASS[kind], className)}>
      <span aria-hidden="true">{MARK_ICON[kind]}</span>
      {MARK_LABEL[kind]}
    </Badge>
  );
}

export interface TargetCardsProps {
  line: OurPriceLine;
  /** Справка панели: по `analog_id` карточка берёт объект, договор и дату цены. */
  analogs: OurPriceAnalog[];
  vat: VatView;
  /** Выбор цели — только `admin`, и только у сравнимой строки: сервер отвечает 422 на остальные. */
  canEdit: boolean;
  /** Цель сохраняется: карточки погашены. */
  busy?: boolean;
  /** Нажатие отдаёт вид стратегии; конкретный аналог карточка никогда не выбирает. */
  onChoose: (strategy: TargetStrategy) => void;
}

interface Spec {
  kind: TargetStrategy | "pinned";
  testId: string;
  label: string;
  price: MoneyPair | null;
  analog: OurPriceAnalog | undefined;
  period: string | null;
  contractId: number | null;
  /** `undefined` — разницы у карточки нет вовсе, `null` — разницы нет сейчас (печатается прочерк). */
  diff?: { total: MoneyPair | null; unit: MoneyPair | null };
}

function analogOf(analogs: OurPriceAnalog[], id: number | null): OurPriceAnalog | undefined {
  return id === null ? undefined : analogs.find((a) => a.analog_id === id);
}

function Diff({ testId, unit, vat, total, perUnit }: {
  testId: string;
  unit: string | null;
  vat: VatView;
  total: MoneyPair | null;
  perUnit: MoneyPair | null;
}) {
  const delta = pick(total, vat);
  const deltaUnit = pick(perUnit, vat);
  const sign = delta === null ? null : compareDecimalStrings(delta, "0");
  return (
    <span
      data-testid={testId}
      className={
        sign === 1
          ? "block text-xs text-warning-text"
          : sign === -1
            ? "block text-xs text-info-text"
            : "block text-xs text-fg-secondary"
      }
    >
      <span className="font-mono tabular-nums">
        {delta === null
          ? "—"
          : sign === 0
            ? FIGURE_KP_EQUAL
            : `${sign === 1 ? FIGURE_KP_ABOVE : FIGURE_KP_BELOW}${
                deltaUnit === null || unit === null ? ":" : ` на ${formatUnitPrice(absDecimal(deltaUnit))} за ${unit} ·`
              } ${formatSigned(delta)}`}
      </span>
    </span>
  );
}

/**
 * Карточки цели строки-работы (спека упрощения каталога §2.9, §2.11): по карточке на стратегию
 * («самая низкая», «самая свежая», «с этим подрядчиком»), даже если две указывают на один договор.
 * Нажатая карточка и есть цель: нажатие сохраняет ВИД стратегии, поэтому цель следует за данными;
 * закрепить конкретный аналог можно только «сделать целью» у него в справке — тогда появляется
 * карточка «выбранный аналог». Карточки «с этим подрядчиком» нет, если такого договора среди аналогов нет.
 */
export function TargetCards({ line, analogs, vat, canEdit, busy = false, onChoose }: TargetCardsProps) {
  const choice = line.choice;
  const active = choice !== null && choice.active ? choice : null;
  // Сброшенную справочную цель объясняет её карточка, а не плашка «самая низкая».
  const inactive = choice !== null && !choice.active && choice.kind !== "reference" ? choice : null;
  const selected = selectedTarget(line);
  const interactive = canEdit && line.state === "comparable";
  const { lowest, freshest, contractor } = line;

  const specs: Spec[] = [];
  if (lowest !== null) {
    specs.push({
      kind: "lowest",
      testId: "figure-lowest",
      label: TARGET_CARD.lowest,
      price: lowest.value,
      analog: analogOf(analogs, lowest.analog_id),
      period: null,
      contractId: lowest.contract_id,
      diff: { total: line.to_lowest, unit: line.to_lowest_unit },
    });
    specs.push({
      kind: "freshest",
      testId: "figure-freshest",
      label: TARGET_CARD.freshest,
      price: freshest?.value ?? null,
      analog: analogOf(analogs, freshest?.analog_id ?? null),
      period: freshest?.period ?? null,
      contractId: freshest?.contract_id ?? null,
      diff: { total: line.to_freshest, unit: line.to_freshest_unit },
    });
    if (contractor !== null) {
      specs.push({
        kind: "contractor",
        testId: "figure-contractor",
        label: TARGET_CARD.contractor,
        price: contractor.value,
        analog: analogOf(analogs, contractor.analog_id),
        period: contractor.period,
        contractId: contractor.contract_id,
        diff: { total: line.to_contractor, unit: line.to_contractor_unit },
      });
    }
    if (selected === "analog") {
      const pinned = analogOf(analogs, active?.analog_id ?? null);
      specs.push({
        kind: "pinned",
        testId: "figure-pinned",
        label: TARGET_CARD.pinned,
        price: pinned?.price ?? null,
        analog: pinned,
        period: null,
        contractId: pinned?.contract_id ?? null,
      });
    }
  }

  if (specs.length === 0 && inactive === null) return null;
  return (
    <div className="space-y-2">
      {inactive !== null && (
        <div
          role="note"
          data-testid="target-inactive"
          className="rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
        >
          {targetInactiveNote(inactive.reset_reason)}
        </div>
      )}
      {specs.length > 0 && (
        <div role="group" aria-label="Цель для выгрузки" className="grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-2">
          {specs.map((spec, index) => {
            const isSelected = spec.kind === "pinned" || spec.kind === selected;
            const earlier =
              spec.kind === "pinned"
                ? undefined
                : specs
                    .slice(0, index)
                    .find((other) => spec.contractId !== null && other.contractId === spec.contractId);
            const value = pick(spec.price, vat);
            const date = spec.period ?? spec.analog?.period ?? null;
            const where =
              spec.analog === undefined ? null : `${spec.analog.object_title} · дог. ${spec.analog.number}`;
            const footer = isSelected
              ? spec.kind === "lowest" && active === null
                ? TARGET_CARD.targetAuto
                : TARGET_CARD.target
              : interactive
                ? TARGET_CARD.choose
                : null;
            const strategy = spec.kind === "pinned" ? null : spec.kind;
            const body = (
              <>
                <span className="flex flex-wrap items-center gap-1.5">
                  {spec.kind === "pinned" ? (
                    <Badge className="h-auto bg-action py-1 font-semibold text-action-text">
                      <span aria-hidden="true">◎</span>
                      {spec.label}
                    </Badge>
                  ) : (
                    <TargetMark kind={spec.kind} />
                  )}
                  {earlier !== undefined && (
                    <span className="text-[0.6875rem] text-fg-tertiary">{targetSameContract(earlier.label)}</span>
                  )}
                </span>
                {value === null ? (
                  <span className="block text-fg-secondary">{FIGURE_NONE}</span>
                ) : (
                  <>
                    <span className="block font-mono text-xl font-semibold tabular-nums">
                      {formatUnitPrice(value)}{" "}
                      <span className="text-xs font-normal text-fg-secondary">{priceUnitLabel(line.unit)}</span>
                    </span>
                    {(where !== null || date !== null) && (
                      <span className="block text-xs text-fg-secondary">
                        {[where, date === null ? null : periodRu(date)].filter((p): p is string => p !== null).join(" · ")}
                      </span>
                    )}
                  </>
                )}
                {value !== null && spec.diff !== undefined && (
                  <Diff
                    testId={`${spec.testId}-diff`}
                    unit={line.unit}
                    vat={vat}
                    total={spec.diff.total}
                    perUnit={spec.diff.unit}
                  />
                )}
                {footer !== null && (
                  <span className={cn("block text-xs", isSelected ? "font-semibold" : "text-fg-secondary underline")}>
                    {footer}
                  </span>
                )}
              </>
            );
            return (
              <Card
                key={spec.kind}
                size="sm"
                data-testid={spec.testId}
                data-selected={String(isSelected)}
                className={cn(
                  "min-w-0 gap-0 border-t-4 py-0",
                  CARD_TOP[spec.kind],
                  isSelected ? "bg-surface-sunken ring-2 ring-action" : "bg-surface"
                )}
              >
                {interactive && strategy !== null ? (
                  <Button
                    type="button"
                    variant="ghost"
                    aria-pressed={isSelected}
                    disabled={busy}
                    onClick={isSelected ? undefined : () => onChoose(strategy)}
                    className="h-auto w-full flex-col items-stretch gap-1.5 whitespace-normal rounded-none p-3 text-left font-normal"
                  >
                    {body}
                  </Button>
                ) : (
                  <div className="space-y-1.5 p-3">{body}</div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

export interface ReferenceTargetCardProps {
  line: OurPriceLine;
  vat: VatView;
  /** «Вернуть самую низкую» — только `admin`. */
  canEdit: boolean;
  /** Выбор снимается: кнопка погашена. */
  busy?: boolean;
  onReset: () => void;
}

const pathText = (steps: { title: string }[]) => steps.map((step) => step.title).join(" → ");

/**
 * Карточка справочной цели строки (спека справочных аналогов §2.14): откуда выбрана, строка договора,
 * при пересчёте — «пересчёт из {ед}», основание, исходная и пересчитанная цена; подсказки на цифры не влияют.
 * Сброшенная цель называет причину сброса. Деньги — готовые пары сервера.
 */
export function ReferenceTargetCard({ line, vat, canEdit, busy = false, onReset }: ReferenceTargetCardProps) {
  const choice = line.choice;
  if (choice === null || choice.kind !== "reference") return null;
  if (!choice.active) {
    return (
      <div
        role="note"
        data-testid="reference-target-inactive"
        className="rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
      >
        {refTargetInactive(choice.reset_reason)}
      </div>
    );
  }
  const row = choice.reference ?? null;
  const conversion = choice.conversion ?? null;
  const hints = choice.hints;
  const origin = choice.origin ?? "search";
  const target = row === null ? null : pick(row.kp_unit_price ?? row.price, vat);
  const source = row === null ? null : pick(row.price, vat);
  const deviation = row === null ? null : roundDecimalPercent(row.deviation_pct, 0);
  const where =
    row === null
      ? ""
      : `${row.contract.object_title}, договор № ${row.contract.number}, ${row.contract.year} г. · «${row.title}»${
          row.chapter_paths.length > 0 ? ` · ${pathText(row.chapter_paths[0])}` : ""
        }`;
  return (
    <div
      data-testid="reference-target"
      className="space-y-2 rounded-lg border border-action bg-surface-sunken px-3 py-2.5 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <b>{REF_TARGET.title}</b>
        <Badge data-testid="reference-target-origin" variant="outline" className="h-auto py-0.5 text-warning-text">
          {REF_TARGET.origin[origin]}
        </Badge>
      </div>
      {row === null ? (
        <p className="text-fg-secondary">{REF_TARGET.rowGone}</p>
      ) : (
        <>
          <p data-testid="reference-target-where" className="line-clamp-2 text-xs text-fg-secondary" title={where}>
            {where}
          </p>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span data-testid="reference-target-price" className="font-mono text-lg font-semibold tabular-nums">
              {target === null ? FIGURE_NONE : `${formatUnitPrice(target)} ${priceUnitLabel(line.unit)}`}
            </span>
            {deviation !== null && (
              <span
                data-testid="reference-target-deviation"
                className={cn("font-mono text-xs tabular-nums", deviation.sign > 0 && "text-danger-text")}
              >
                {refDeviation(deviation.text)}
              </span>
            )}
          </div>
          {conversion !== null && (
            <div data-testid="reference-target-conversion" className="space-y-0.5 text-xs text-fg-secondary">
              <p>
                <Badge variant="secondary" className="mr-1.5 h-auto py-0.5">
                  {refTargetConvertedFrom(row.unit)}
                </Badge>
                {refTargetConversionLine(row.unit, line.unit, conversion.factor_shown, conversion.basis)}
              </p>
              <p className="font-mono tabular-nums">
                {REF_TARGET.sourcePrice}: {source === null ? FIGURE_NONE : `${formatUnitPrice(source)} ${priceUnitLabel(row.unit)}`}
                {" · "}
                {REF_TARGET.convertedPrice}: {target === null ? FIGURE_NONE : `${formatUnitPrice(target)} ${priceUnitLabel(line.unit)}`}
              </p>
            </div>
          )}
        </>
      )}
      {hints?.link_now != null && (
        <p data-testid="reference-hint-link-now" className="rounded-md bg-info-soft px-2 py-1 text-xs text-info-text">
          {refTargetLinkNow(row?.unit ?? null, line.unit, hints.link_now.factor_shown)}
        </p>
      )}
      {hints?.link_gone === true && (
        <p data-testid="reference-hint-link-gone" className="rounded-md bg-warning-soft px-2 py-1 text-xs text-warning-text">
          {REF_TARGET.linkGone}
        </p>
      )}
      {hints?.own_analogs === true && (
        <p data-testid="reference-hint-own-analogs" className="rounded-md bg-info-soft px-2 py-1 text-xs text-info-text">
          {REF_TARGET.ownAnalogs}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-fg-tertiary">
          Выбрал {choice.set_by.email}, {formatDate(choice.set_at)}
        </span>
        {canEdit && (
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onReset}>
            {REF_TARGET.resetToLowest}
          </Button>
        )}
      </div>
    </div>
  );
}
