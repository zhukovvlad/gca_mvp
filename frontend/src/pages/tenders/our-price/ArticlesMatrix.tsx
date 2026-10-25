import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { compareDecimalStrings } from "@/lib/decimal";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  Decimal,
  OurPriceCell,
  OurPriceFork,
  OurPriceParticipant,
  OurPriceRound,
  OurPriceRoundArticle,
} from "@/types/domain";

import { formatSigned, pick, type VatView } from "./money";
import {
  ARTICLE_COLUMN_LABEL,
  ARTICLES_TITLE,
  EMPTY_REASON_LABEL,
  FORK_COLUMN_LABEL,
  LEGEND_BEST,
  LEGEND_CHEAP,
  LEGEND_DEAR,
  LEGEND_MISMATCH,
  MATRIX_HINT,
  VAT_MIXED_RATES_LABEL,
  VAT_NET_LABEL,
  VAT_UNKNOWN_LABEL,
  vatShownLabel,
  UNALLOCATED_ARTICLE,
  type EmptyReason,
} from "./ourPriceCopy";

export interface ArticlesMatrixProps {
  round: OurPriceRound;
  view: VatView;
  /**
   * Адрес разбора участника: `null` — без раскрытой статьи («Итого»),
   * `UNALLOCATED_ARTICLE` — строка «Нераспределённое».
   */
  offerHref: (offerId: number, article: number | typeof UNALLOCATED_ARTICLE | null) => string;
}

/** Ключ сортировки: статья, вилка класса либо участник (`offer_id`). */
type SortKey = "article" | "fork" | number;
interface SortState {
  key: SortKey;
  dir: "asc" | "desc";
}

type Tone = "dear" | "cheap";

const TONE_CLASS: Record<Tone, string> = {
  dear: "bg-warning-soft text-warning-text",
  cheap: "bg-info-soft text-info-text",
};

/** Пустые значения всегда в конце, в обоих направлениях сортировки. */
function compareNullable(a: Decimal | null, b: Decimal | null, dir: SortState["dir"]): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  const result = compareDecimalStrings(a, b) ?? 0;
  return dir === "asc" ? result : -result;
}

function compareOrder(a: number | null, b: number | null, dir: SortState["dir"]): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return dir === "asc" ? a - b : b - a;
}

/**
 * Окраска ячейки: выше `max` вилки наших договоров класса — `dear`, ниже `min` —
 * `cheap`. Сравнение по нетто, в любом виде показа: ось сравнения не меняется от
 * переключателя НДС (спека 4а §2.9).
 */
function toneOf(perSqmNet: Decimal | null, fork: OurPriceFork | null): Tone | null {
  if (perSqmNet === null || fork === null) return null;
  if (compareDecimalStrings(perSqmNet, fork.max.net) === 1) return "dear";
  if (compareDecimalStrings(perSqmNet, fork.min.net) === -1) return "cheap";
  return null;
}

/** Причина пустой ячейки — поле участника, а не ветка текста. */
function emptyReason(participant: OurPriceParticipant): EmptyReason | null {
  if (participant.state === "no_estimate") return "no_estimate";
  return participant.per_sqm_reason;
}

/** Подпись ставки участника под его заголовком колонки. */
function participantVatCaption(participant: OurPriceParticipant, view: VatView): string {
  if (view === "net") return VAT_NET_LABEL;
  return participant.vat_rate === null ? VAT_UNKNOWN_LABEL : vatShownLabel(participant.vat_rate);
}

function forkCaption(round: OurPriceRound, view: VatView): string {
  if (view === "net") return VAT_NET_LABEL;
  const rate = round.history.shown_rate;
  return rate === null ? VAT_MIXED_RATES_LABEL : vatShownLabel(rate);
}

/** Вилка общей колонки: `shown` только при одной ставке участников, иначе нетто. */
function forkView(round: OurPriceRound, view: VatView): VatView {
  return view === "shown" && round.history.shown_rate !== null ? "shown" : "net";
}

function SortHead({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === sortKey;
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("h-auto px-1 py-0.5 font-medium", align === "right" && "ml-auto flex")}
      onClick={() => onSort(sortKey)}
    >
      {label}
      {active && (sort.dir === "asc" ? <ArrowUp aria-hidden /> : <ArrowDown aria-hidden />)}
    </Button>
  );
}

function ariaSort(sort: SortState, key: SortKey): "ascending" | "descending" | "none" {
  if (sort.key !== key) return "none";
  return sort.dir === "asc" ? "ascending" : "descending";
}

/**
 * Таблица «статьи × участники» экрана этапа (спека 4а §2.13): первая строка —
 * «Итого», дальше статьи по номеру классификатора; колонка вилки наших договоров
 * класса и по колонке на участника. Цену и сумму «целиком» выбирает `pick` — ни
 * одной арифметики над деньгами.
 */
export function ArticlesMatrix({ round, view, offerHref }: ArticlesMatrixProps) {
  const [sort, setSort] = useState<SortState>({ key: "article", dir: "asc" });

  function onSort(key: SortKey) {
    setSort((prev) =>
      prev?.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }
    );
  }

  const rows = useMemo(() => {
    const { dir, key } = sort;
    const cellNet = (article: OurPriceRoundArticle, offerId: number) =>
      article.cells.find((c) => c.offer_id === offerId)?.per_sqm?.net ?? null;
    return [...round.articles].sort((a, b) => {
      let result: number;
      if (key === "fork") {
        result = compareNullable(
          a.ours_class_per_sqm?.min.net ?? null,
          b.ours_class_per_sqm?.min.net ?? null,
          dir
        );
      } else if (typeof key === "number") {
        result = compareNullable(cellNet(a, key), cellNet(b, key), dir);
      } else {
        result = compareOrder(a.sort_order, b.sort_order, dir);
      }
      return result !== 0 ? result : compareOrder(a.sort_order, b.sort_order, "asc");
    });
  }, [round.articles, sort]);

  const fView = forkView(round, view);

  function renderCell(article: OurPriceRoundArticle, rowKey: string, participant: OurPriceParticipant) {
    const cell: OurPriceCell | undefined = article.cells.find((c) => c.offer_id === participant.offer_id);
    const value = pick(cell?.per_sqm ?? null, view);
    const testId = `cell-${rowKey}-${participant.offer_id}`;
    if (cell === undefined || value === null) {
      const reason = emptyReason(participant);
      return (
        <TableCell
          key={participant.offer_id}
          data-testid={testId}
          data-reason={reason ?? undefined}
          title={reason === null ? undefined : EMPTY_REASON_LABEL[reason]}
          className="text-right text-fg-muted"
        >
          —
        </TableCell>
      );
    }
    const tone = toneOf(cell.per_sqm?.net ?? null, article.ours_class_per_sqm);
    const cheapestNet = article.cells.reduce<Decimal | null>((best, c) => {
      const net = c.per_sqm?.net ?? null;
      if (net === null) return best;
      return best === null || compareDecimalStrings(net, best) === -1 ? net : best;
    }, null);
    const cheapest =
      cell.per_sqm !== null &&
      cheapestNet !== null &&
      compareDecimalStrings(cell.per_sqm.net, cheapestNet) === 0;
    const whole = pick(cell.whole, view);
    return (
      <TableCell
        key={participant.offer_id}
        data-testid={testId}
        data-tone={tone ?? undefined}
        data-cheapest={cheapest ? "true" : undefined}
        className={cn("p-0 text-right", tone !== null && TONE_CLASS[tone])}
      >
        <Link
          to={offerHref(
            participant.offer_id,
            rowKey === "total" ? null : (article.work_category_id ?? UNALLOCATED_ARTICLE)
          )}
          className="block px-2 py-1.5 hover:outline hover:outline-2 hover:-outline-offset-2 hover:outline-border"
        >
          <span
            className={cn(
              "font-mono tabular-nums",
              cheapest && "underline decoration-2 underline-offset-4"
            )}
          >
            {formatDecimalMoney(value, "")}
          </span>
          <span className="block font-mono text-xs text-fg-secondary">
            {whole !== null && formatSigned(whole)}
            {cell.unit_mismatch_lines > 0 && (
              <span className="text-warning-text">
                {whole !== null && " · "}⚠ {cell.unit_mismatch_lines}
              </span>
            )}
          </span>
        </Link>
      </TableCell>
    );
  }

  function renderRow(article: OurPriceRoundArticle, rowKey: string, isTotal: boolean) {
    const fork = article.ours_class_per_sqm;
    const min = pick(fork?.min ?? null, fView);
    const max = pick(fork?.max ?? null, fView);
    return (
      <TableRow
        key={rowKey}
        data-testid={`row-${rowKey}`}
        className={isTotal ? "border-b-2 bg-surface-sunken font-semibold" : undefined}
      >
        <TableCell>
          {article.code !== null && (
            <span className="mr-1 font-mono text-xs text-fg-muted">{article.code}</span>
          )}
          {article.title}
        </TableCell>
        <TableCell data-testid={`fork-${rowKey}`} className="text-right font-mono tabular-nums">
          {min !== null && max !== null
            ? `${formatDecimalMoney(min, "")} – ${formatDecimalMoney(max, "")}`
            : "—"}
        </TableCell>
        {round.participants.map((participant) => renderCell(article, rowKey, participant))}
      </TableRow>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{ARTICLES_TITLE}</CardTitle>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-secondary">
          <span className="rounded-md bg-warning-soft px-1.5 text-warning-text">{LEGEND_DEAR}</span>
          <span className="rounded-md bg-info-soft px-1.5 text-info-text">{LEGEND_CHEAP}</span>
          <span>{LEGEND_BEST}</span>
          <span>{LEGEND_MISMATCH}</span>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead aria-sort={ariaSort(sort, "article")}>
                <SortHead
                  label={ARTICLE_COLUMN_LABEL}
                  sortKey="article"
                  sort={sort}
                  onSort={onSort}
                />
              </TableHead>
              <TableHead data-testid="fork-head" aria-sort={ariaSort(sort, "fork")} className="text-right">
                <SortHead label={FORK_COLUMN_LABEL} sortKey="fork" sort={sort} onSort={onSort} align="right" />
                <span className="block text-xs font-normal text-fg-secondary">
                  {forkCaption(round, view)}
                </span>
              </TableHead>
              {round.participants.map((participant) => (
                <TableHead
                  key={participant.offer_id}
                  data-testid={`col-${participant.offer_id}`}
                  aria-sort={ariaSort(sort, participant.offer_id)}
                  className="text-right"
                >
                  <SortHead
                    label={participant.contractor.title}
                    sortKey={participant.offer_id}
                    sort={sort}
                    onSort={onSort}
                    align="right"
                  />
                  <span className="block text-xs font-normal text-fg-secondary">
                    {participantVatCaption(participant, view)}
                  </span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {renderRow(round.total, "total", true)}
            {rows.map((article) => renderRow(article, String(article.work_category_id), false))}
          </TableBody>
        </Table>
        <p data-testid="matrix-hint" className="mt-2 text-xs text-fg-secondary">
          {MATRIX_HINT}
        </p>
      </CardContent>
    </Card>
  );
}
