import { useMemo, useState } from "react";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { Decimal, OurPriceLine } from "@/types/domain";

import {
  absDecimal,
  comparedKpPrice,
  comparedUnit,
  formatMoney,
  formatSigned,
  formatUnitPrice,
  pick,
  type VatView,
} from "./money";
import {
  agreedCaption,
  ALL_CLASSES_LABEL,
  contractsShort,
  EMPTY_LINES_LABEL,
  LINE_COLUMN,
  lineStateLabel,
  RUNG_LEVEL_LABEL,
  RUNG_MANUAL_INACTIVE_TAG,
  RUNG_MANUAL_TAG,
  SECONDARY_MARK,
  variantCaption,
} from "./ourPriceCopy";
import { LineStateText } from "./LineStateText";
import { SortHead } from "./SortHead";
import { ariaSort, compareNullable, nextSort, type SortDir, type SortState } from "./sorting";

export interface OfferLinesTableProps {
  lines: OurPriceLine[];
  vat: VatView;
  /** Название класса объекта: подпись ступени «… · класс · N дог.». */
  rateClass: string;
  /** Подпись «ждёт решения администратора» — ссылка на карточку контекста (только `admin`). */
  canResolve?: boolean;
  onOpenLine: (positionItemId: number) => void;
}

type SortKey = "work" | "unit" | "quantity" | "kp" | "fork" | "ours" | "to_ours";

function compareText(a: string | null, b: string | null, dir: SortDir): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  const result = a.localeCompare(b, "ru");
  return dir === "asc" ? result : -result;
}

/** Значение сортировки по колонке; для денег — тот вид, что на экране. */
function decimalOf(line: OurPriceLine, key: SortKey, vat: VatView): Decimal | null {
  switch (key) {
    case "quantity":
      return line.quantity;
    case "kp":
      return comparedKpPrice(line, vat);
    case "fork":
      return pick(line.range?.min ?? null, vat);
    case "ours":
      return pick(line.ours?.value ?? null, vat);
    case "to_ours": {
      const value = pick(line.to_ours, vat);
      return value === null ? null : absDecimal(value);
    }
    default:
      return null;
  }
}

function compareLines(a: OurPriceLine, b: OurPriceLine, sort: SortState<SortKey>, vat: VatView): number {
  if (sort.key === "work") return compareText(a.title, b.title, sort.dir);
  if (sort.key === "unit") return compareText(a.unit, b.unit, sort.dir);
  return compareNullable(decimalOf(a, sort.key, vat), decimalOf(b, sort.key, vat), sort.dir);
}

/** Подпись ступени: «вариант · Класс 2 · 3 дог.»; у строки без ступени её нет. */
function rungCaption(line: OurPriceLine, rateClass: string): string | null {
  const rung = line.rung;
  if (rung === null) return null;
  const contracts = line.rungs.find((r) => r.level === rung.level && r.scope === rung.scope)?.contracts;
  return [
    RUNG_LEVEL_LABEL[rung.level],
    rung.scope === "cls" ? rateClass : ALL_CLASSES_LABEL,
    contracts === undefined ? null : contractsShort(contracts),
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** Метка ручной ступени у подписи: действующая — «ручная», сохранённая, но не действующая — «ручная не действует». */
function manualMark(line: OurPriceLine): string | null {
  const manual = line.manual_rung;
  if (manual === null) return null;
  return manual.active ? RUNG_MANUAL_TAG : RUNG_MANUAL_INACTIVE_TAG;
}

const features = tableFeatures({});
const helper = createColumnHelper<typeof features, OurPriceLine>();

const DASH = "—";

/** Желаемые ширины числовых колонок (раскладка автоматическая: вилка расширяется по тексту); колонка работы берёт остаток. */
const COLUMN_WIDTH: Record<string, string | undefined> = {
  unit: "w-16",
  quantity: "w-24",
  kp: "w-28",
  fork: "w-44",
  ours: "w-28",
  to_ours: "w-44",
};

/**
 * Строки статьи разбора (спека 4а §2.14, дизайн 3.3): работа и вариант, единица, объём,
 * цена КП, вилка наших договоров со ступенью, наша расценка и разница со знаком.
 * По умолчанию строки идут по модулю разницы, сильнее всего расходящиеся — сверху;
 * числа сравниваются десятичными строками, пустые — в конце.
 */
export function OfferLinesTable({ lines, vat, rateClass, canResolve = false, onOpenLine }: OfferLinesTableProps) {
  const [sort, setSort] = useState<SortState<SortKey>>({ key: "to_ours", dir: "desc" });

  const sorted = useMemo(
    () => [...lines].sort((a, b) => compareLines(a, b, sort, vat)),
    [lines, sort, vat]
  );

  const columns = useMemo(() => {
    return [
      helper.display({
        id: "work",
        header: LINE_COLUMN.work,
        cell: ({ row }) => {
          const line = row.original;
          const variant = variantCaption(line) ?? "";
          return (
            <div data-testid={`work-${line.position_item_id}`}>
              <button type="button" className="line-clamp-3 text-left text-fg" title={line.title}>
                {line.title}
              </button>
              {variant !== "" && (
                <span className="block text-[0.6875rem] leading-snug text-fg-secondary">
                  {variant}
                  {line.secondary.length > 0 && (
                    <span
                      data-testid={`secondary-${line.position_item_id}`}
                      className="ml-1 text-fg-muted"
                      title={line.secondary.map((s) => `${s.name}: ${s.value}`).join("; ")}
                    >
                      {SECONDARY_MARK}
                    </span>
                  )}
                </span>
              )}
            </div>
          );
        },
      }),
      helper.display({
        id: "unit",
        header: LINE_COLUMN.unit,
        cell: ({ row }) => row.original.unit ?? DASH,
      }),
      helper.display({
        id: "quantity",
        header: LINE_COLUMN.quantity,
        cell: ({ row }) => (
          <span data-testid={`qty-${row.original.position_item_id}`} className="font-mono tabular-nums">
            {row.original.quantity === null ? DASH : formatMoney(row.original.quantity, "")}
          </span>
        ),
      }),
      helper.display({
        id: "kp",
        header: LINE_COLUMN.kpPrice,
        cell: ({ row }) => {
          const price = comparedKpPrice(row.original, vat);
          return (
            <span data-testid={`kp-${row.original.position_item_id}`} className="font-mono tabular-nums">
              {price === null ? (
                DASH
              ) : (
                <>
                  {formatUnitPrice(price)}
                  <span className="block text-[0.6875rem] text-fg-secondary">{comparedUnit(row.original)}</span>
                </>
              )}
            </span>
          );
        },
      }),
      helper.display({
        id: "fork",
        header: LINE_COLUMN.fork,
        cell: ({ row }) => {
          const line = row.original;
          const min = pick(line.range?.min ?? null, vat);
          const max = pick(line.range?.max ?? null, vat);
          const caption = [rungCaption(line, rateClass), manualMark(line)]
            .filter((part): part is string => part !== null)
            .join(" · ");
          return (
            <div>
              <span data-testid={`fork-${line.position_item_id}`} className="font-mono tabular-nums">
                {min !== null && max !== null ? `${formatUnitPrice(min)} – ${formatUnitPrice(max)}` : DASH}
                {line.kind === "system" && min !== null && max !== null && (
                  <span className="block text-[0.6875rem] text-fg-secondary">{comparedUnit(line)}</span>
                )}
              </span>
              {caption !== "" && (
                <span
                  data-testid={`rung-${line.position_item_id}`}
                  className="block whitespace-normal text-[0.6875rem] leading-snug text-fg-secondary"
                >
                  {caption}
                </span>
              )}
            </div>
          );
        },
      }),
      helper.display({
        id: "ours",
        header: LINE_COLUMN.ours,
        cell: ({ row }) => {
          const line = row.original;
          const value = pick(line.ours?.value ?? null, vat);
          // Нет показанной цены (база НДС неизвестна) — метки нет, а не прочерк: решение от вида не зависит.
          const agreed = line.agreed === null ? null : pick(line.agreed, vat);
          return (
            <span data-testid={`ours-${line.position_item_id}`}>
              <span className="font-mono tabular-nums">{value === null ? DASH : formatUnitPrice(value)}</span>
              {line.kind === "system" && value !== null && (
                <span className="block text-[0.6875rem] text-fg-secondary">{comparedUnit(line)}</span>
              )}
              {line.ours?.from_all_classes === true && (
                <Badge variant="outline" className="ml-1">
                  {ALL_CLASSES_LABEL}
                </Badge>
              )}
              {agreed !== null && (
                <span
                  data-testid={`agreed-${line.position_item_id}`}
                  className="block text-[0.6875rem] leading-snug text-fg-secondary"
                >
                  {agreedCaption(formatUnitPrice(agreed))}
                </span>
              )}
            </span>
          );
        },
      }),
      helper.display({
        id: "to_ours",
        header: LINE_COLUMN.toOurs,
        cell: ({ row }) => {
          const line = row.original;
          const diff = pick(line.to_ours, vat);
          const label = lineStateLabel(line);
          return (
            <div data-testid={`to-ours-${line.position_item_id}`}>
              <span
                className={cn(
                  "font-mono tabular-nums",
                  line.state === "unit_mismatch" && "text-fg-muted"
                )}
              >
                {diff === null ? DASH : formatSigned(diff)}
              </span>
              {label !== null && (
                <Badge variant="outline" className="mt-0.5 block h-auto max-w-full whitespace-normal rounded-lg py-0.5 text-left">
                  <LineStateText line={line} label={label} canResolve={canResolve} />
                </Badge>
              )}
            </div>
          );
        },
      }),
    ];
  }, [vat, rateClass, canResolve]);

  const table = useTable({ features, columns, data: sorted });

  if (lines.length === 0) {
    return (
      <p data-testid="lines-empty" className="px-2 py-3 text-sm text-fg-secondary">
        {EMPTY_LINES_LABEL}
      </p>
    );
  }

  const numeric = new Set(["quantity", "kp", "ours", "to_ours"]);
  return (
    <Table>
      <TableHeader>
        {table.getHeaderGroups().map((group) => (
          <TableRow key={group.id}>
            {group.headers.map((header) => (
              <TableHead
                key={header.id}
                aria-sort={ariaSort(sort, header.column.id as SortKey)}
                className={cn(numeric.has(header.column.id) && "text-right", COLUMN_WIDTH[header.column.id])}
              >
                <SortHead
                  label={String(header.column.columnDef.header)}
                  sortKey={header.column.id as SortKey}
                  sort={sort}
                  onSort={(key) => setSort((prev) => nextSort(prev, key))}
                  align={numeric.has(header.column.id) ? "right" : "left"}
                />
              </TableHead>
            ))}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => (
          <TableRow
            key={row.id}
            data-testid={`line-${row.original.position_item_id}`}
            className="cursor-pointer"
            onClick={() => onOpenLine(row.original.position_item_id)}
          >
            {row.getAllCells().map((cell) => (
              <TableCell
                key={cell.id}
                className={cn(
                  "align-top",
                  numeric.has(cell.column.id) && "text-right",
                  cell.column.id === "work" && "whitespace-normal"
                )}
              >
                <table.FlexRender cell={cell} />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
