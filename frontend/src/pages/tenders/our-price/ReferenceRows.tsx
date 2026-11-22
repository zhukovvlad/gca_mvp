import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { roundDecimalPercent } from "@/lib/format";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useSetLumpSum } from "@/services/queries";
import type { LumpVerdict, LumpView, RefRow } from "@/types/domain";

import { formatUnitPrice, pick, priceUnitLabel, type VatView } from "./money";
import {
  analogOutlierNote,
  LUMP_CHOICE_LABEL,
  LUMP_NOT_TARGET,
  LUMP_ROW_MENU,
  lumpRuleChoiceLabel,
  periodRu,
  REF_FIRST,
  REF_LESS,
  REF_MAKE_TARGET,
  REF_PARAM_VALUE,
  REF_THIS_CONTRACTOR,
  refConversionText,
  refDeviation,
  refFolded,
  refMore,
  refSections,
} from "./ourPriceCopy";

const DASH = "—";

export interface ReferenceRowsProps<R extends RefRow> {
  rows: R[];
  vat: VatView;
  /** Единица строки КП: в ней показана цена после пересчёта. */
  kpUnit: string | null;
  /** «Сделать целью» — только `admin`. */
  canChoose: boolean;
  /** Меню поправки «комплект или ставка» у строки с `lump` — только `admin` (спека комплектов §8 п. 5). */
  canLump?: boolean;
  /** Цель сохраняется: кнопки выбора погашены. */
  busy: boolean;
  /** Приставка `data-testid` строк: блок или поиск. */
  testPrefix: string;
  /** Показать первые пять, остальные за «ещё N». Поиск показывает всё. */
  fold?: boolean;
  /** Дополнительные пометки строки (зона, вид пересчёта у выдачи поиска). */
  extra?: (row: R) => ReactNode;
  onChoose: (row: R) => void;
}

const pathText = (path: RefRow["chapter_paths"][number]) => path.map((step) => step.title).join(" → ");

/**
 * Строки справочного блока или выдачи поиска (спека справочных аналогов §2.4, §2.11): договор, цена,
 * пересчёт и цена в единице строки КП, отклонение, пометка выброса, «×k» со списком разделов и
 * «сделать целью». Деньги берутся готовыми парами сервера, арифметики на клиенте нет.
 */
export function ReferenceRows<R extends RefRow>({
  rows,
  vat,
  kpUnit,
  canChoose,
  canLump = false,
  busy,
  testPrefix,
  fold = true,
  extra,
  onChoose,
}: ReferenceRowsProps<R>) {
  const [open, setOpen] = useState(false);
  const shown = fold && !open ? rows.slice(0, REF_FIRST) : rows;
  const hidden = rows.length - REF_FIRST;
  return (
    <div data-testid={`${testPrefix}-rows`} className="space-y-3">
      {shown.map((row) => (
        <ReferenceRowCard
          key={row.analog_id}
          row={row}
          vat={vat}
          kpUnit={kpUnit}
          canChoose={canChoose}
          lumpMenu={canLump}
          busy={busy}
          testId={`ref-row-${testPrefix}-${row.analog_id}`}
          extra={extra?.(row)}
          onChoose={() => onChoose(row)}
        />
      ))}
      {fold && hidden > 0 && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid={`${testPrefix}-more`}
          className="w-full border-dashed"
          onClick={() => setOpen(!open)}
        >
          {open ? REF_LESS : refMore(hidden)}
        </Button>
      )}
    </div>
  );
}

function ReferenceRowCard({
  row,
  vat,
  kpUnit,
  canChoose,
  lumpMenu,
  busy,
  testId,
  extra,
  onChoose,
}: {
  row: RefRow;
  vat: VatView;
  kpUnit: string | null;
  canChoose: boolean;
  lumpMenu: boolean;
  busy: boolean;
  testId: string;
  extra: ReactNode;
  onChoose: () => void;
}) {
  const price = pick(row.price, vat);
  const nominal = pick(row.nominal, vat);
  const inKp = pick(row.kp_unit_price, vat);
  const deviation = roundDecimalPercent(row.deviation_pct, 0);
  const conversion = refConversionText(row, kpUnit);
  const outlier = row.outlier_ratio !== null;
  const folded = row.ids.length > 1;
  const allPaths =
    row.chapter_paths.length > 1
      ? `${refSections(row.chapter_paths.length)} ${row.chapter_paths.map(pathText).join("; ")}`
      : row.chapter_paths.length === 1
        ? pathText(row.chapter_paths[0])
        : "";
  const contract = row.contract;
  const isLump = row.lump?.is_lump === true;
  return (
    <div
      data-testid={testId}
      data-outlier={outlier ? "true" : undefined}
      className={cn("space-y-1.5 border-t border-border pt-3 text-sm", outlier && "rounded-md bg-warning-soft px-2")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <b>{contract.object_title}</b>
          <span className="text-xs text-fg-secondary">
            {" "}
            · дог. {contract.number} · {contract.year} · {contract.rate_class.title} · {contract.contractor_title}
          </span>
          {row.with_this_contractor && (
            <Badge variant="secondary" className="ml-1.5 h-auto bg-mark-contractor-soft py-0.5 text-mark-contractor">
              {REF_THIS_CONTRACTOR}
            </Badge>
          )}
        </div>
        <div className="shrink-0 text-right">
          <span
            data-testid={`${testId}-price`}
            className="font-mono font-semibold tabular-nums"
            title={nominal !== null && nominal !== price ? `номинал: ${formatUnitPrice(nominal)}` : undefined}
          >
            {price === null ? DASH : `${formatUnitPrice(price)} ${priceUnitLabel(row.unit)}`}
          </span>{" "}
          <span className="font-mono text-xs text-fg-secondary tabular-nums">{periodRu(row.period)}</span>
        </div>
      </div>
      <p data-testid={`${testId}-title`} className="break-words text-fg-secondary" title={row.title}>
        {row.title}
        {folded && (
          <span data-testid={`${testId}-folded`} className="ml-1 text-fg-tertiary">
            {refFolded(row.ids.length)}
          </span>
        )}
      </p>
      {row.chapter_paths.length > 1 ? (
        <div data-testid={`${testId}-paths`} className="line-clamp-2 text-xs text-fg-tertiary" title={allPaths}>
          {allPaths}
        </div>
      ) : (
        row.chapter_paths.length === 1 && (
          <div data-testid={`${testId}-paths`} className="line-clamp-2 text-xs text-fg-tertiary" title={allPaths}>
            {allPaths}
          </div>
        )
      )}
      {(conversion !== null || row.param_value !== null) && (
        <div data-testid={`${testId}-conversion`} className="text-xs text-fg-secondary">
          {conversion !== null && <span>{conversion}</span>}
          {conversion !== null && row.conversion !== null && ` — ${row.conversion.basis}`}
          {row.param_value !== null && (
            <span className="ml-1">
              {conversion !== null && "· "}
              {REF_PARAM_VALUE}: {row.param_value}
            </span>
          )}
        </div>
      )}
      {(inKp !== null || deviation !== null || outlier) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {inKp !== null && row.conversion !== null && (
            <span data-testid={`${testId}-kp-price`} className="font-mono text-xs tabular-nums">
              {formatUnitPrice(inKp)} {priceUnitLabel(kpUnit)}
            </span>
          )}
          {deviation !== null && (
            <span
              data-testid={`${testId}-deviation`}
              className={cn("font-mono text-xs tabular-nums", deviation.sign > 0 && "text-danger-text")}
            >
              {refDeviation(deviation.text)}
            </span>
          )}
          {row.outlier_ratio !== null && (
            <Badge
              data-testid={`${testId}-outlier`}
              variant="outline"
              className="h-auto whitespace-normal py-0.5 text-left text-warning-text"
            >
              {analogOutlierNote(row.outlier_ratio)}
            </Badge>
          )}
        </div>
      )}
      {extra}
      {(canChoose || lumpMenu) && (
        <div className="flex flex-wrap items-center gap-2">
          {canChoose && (
            <span title={isLump ? LUMP_NOT_TARGET : undefined} data-testid={isLump ? `${testId}-not-target` : undefined}>
              <Button type="button" size="sm" variant="outline" disabled={busy || isLump} onClick={onChoose}>
                <span aria-hidden="true">◎</span>
                {REF_MAKE_TARGET}
              </Button>
            </span>
          )}
          {lumpMenu && row.lump !== null && <LumpRowMenu id={row.analog_id} lump={row.lump} testId={testId} />}
        </div>
      )}
    </div>
  );
}

type LumpChoice = "rule" | LumpVerdict;

/** Поправка «комплект или ставка» в строке выдачи (спека комплектов §8 п. 5): три положения, выбор уходит сразу. */
function LumpRowMenu({ id, lump, testId }: { id: number; lump: LumpView; testId: string }) {
  const set = useSetLumpSum();
  const current: LumpChoice = lump.override?.verdict ?? "rule";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button type="button" size="sm" variant="ghost" data-testid={`${testId}-lump-menu`} disabled={set.isPending} />}
      >
        {LUMP_ROW_MENU}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-auto min-w-56">
        <DropdownMenuGroup>
          <DropdownMenuLabel>{LUMP_ROW_MENU}</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={current}
            onValueChange={(value) => {
              const next = value as LumpChoice;
              if (next === current) return;
              set.mutate({ id, verdict: next === "rule" ? null : next });
            }}
          >
            <DropdownMenuRadioItem value="rule" data-testid={`${testId}-lump-rule`}>
              {lumpRuleChoiceLabel(lump)}
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="lump" data-testid={`${testId}-lump-lump`}>
              {LUMP_CHOICE_LABEL.lump}
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="rate" data-testid={`${testId}-lump-rate`}>
              {LUMP_CHOICE_LABEL.rate}
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
