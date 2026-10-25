import { Fragment, useMemo, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { compareDecimalStrings } from "@/lib/decimal";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Decimal, MoneyPair, OurPriceLine, OurPriceOffer, OurPriceOfferArticle } from "@/types/domain";

import { OfferLinesTable } from "./OfferLinesTable";
import { matchesFilter, type OfferFilter } from "./offerFilter";
import { absDecimal, formatBelow, formatSigned, pick, type VatView } from "./money";
import {
  FORK_COLUMN_LABEL,
  LEGEND_MISMATCH,
  OFFER_ARTICLES_TITLE,
  OFFER_COLUMN,
  OFFER_TOTAL_LABEL,
  UNALLOCATED_ARTICLE,
} from "./ourPriceCopy";

export interface OfferArticlesTableProps {
  offer: OurPriceOffer;
  vat: VatView;
  filter: OfferFilter;
  /** Ключ статьи, раскрытой при входе (`?article=`): `work_category_id` либо `unallocated`. */
  initialOpen: string | null;
  rateClass: string;
  onOpenLine: (positionItemId: number) => void;
}

/** Ключ статьи в адресе и в раскрытии: у «Нераспределённого» нет id. */
function articleKey(id: number | null): string {
  return id === null ? UNALLOCATED_ARTICLE : String(id);
}

const DASH = "—";
const COLUMNS = 8;

/** Ширина полосы — доля от `--max`, делит CSS: деньги в JS не переводятся в число. */
const BAR_WIDTH = "w-[calc(var(--v)/var(--max)*100%)]";

function money(pair: MoneyPair | null, vat: VatView, format: (v: Decimal) => string): string {
  const value = pick(pair, vat);
  return value === null ? DASH : format(value);
}

/** Наибольшая из сумм «дороже» и модулей «ниже» по всем статьям; полос нет, пока она не больше нуля. */
function barScale(articles: OurPriceOfferArticle[]): Decimal | null {
  const largest = articles
    .flatMap((a) => [a.lines_totals?.bargain?.net ?? null, a.lines_totals?.below ? absDecimal(a.lines_totals.below.net) : null])
    .filter((v): v is Decimal => v !== null)
    .reduce<Decimal | null>((best, v) => (best === null || compareDecimalStrings(v, best) === 1 ? v : best), null);
  return largest !== null && compareDecimalStrings(largest, "0") === 1 ? largest : null;
}

function Bar({ testId, value, max, tone }: { testId: string; value: Decimal; max: Decimal; tone: string }) {
  return (
    <div
      data-testid={testId}
      style={{ "--v": value, "--max": max } as CSSProperties}
      className={cn("h-1.5 rounded-sm", BAR_WIDTH, tone)}
    />
  );
}

/**
 * Таблица статей разбора (спека 4а §2.14, дизайн 3.3): первая строка — «Итого на м²
 * объекта», дальше статьи; статья раскрывается своими строками. Свёрнутые статьи строк
 * не монтируют: 1 831 строка рисуется только по раскрытым статьям.
 */
export function OfferArticlesTable({
  offer,
  vat,
  filter,
  initialOpen,
  rateClass,
  onOpenLine,
}: OfferArticlesTableProps) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() =>
    initialOpen === null ? new Set() : new Set([initialOpen])
  );

  const linesByArticle = useMemo(() => {
    const groups = new Map<string, OurPriceLine[]>();
    for (const line of offer.lines) {
      const key = articleKey(line.article_id);
      const group = groups.get(key);
      if (group) group.push(line);
      else groups.set(key, [line]);
    }
    return groups;
  }, [offer.lines]);

  const scale = useMemo(() => barScale(offer.articles), [offer.articles]);

  function toggle(key: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  /** Колонки «КП», вилка и «статья целиком» общие у «Итого» и статьи. */
  function headCells(row: OurPriceOfferArticle | OurPriceOffer["total"], key: string) {
    const min = pick(row.ours_class_per_sqm?.min ?? null, vat);
    const max = pick(row.ours_class_per_sqm?.max ?? null, vat);
    return (
      <>
        <TableCell data-testid={`art-persqm-${key}`} className="text-right font-mono tabular-nums">
          {money(row.per_sqm, vat, (v) => formatDecimalMoney(v, ""))}
        </TableCell>
        <TableCell data-testid={`art-fork-${key}`} className="text-right font-mono tabular-nums">
          {min !== null && max !== null ? `${formatDecimalMoney(min, "")} – ${formatDecimalMoney(max, "")}` : DASH}
        </TableCell>
        <TableCell data-testid={`art-whole-${key}`} className="text-right font-mono tabular-nums">
          {money(row.whole, vat, formatSigned)}
        </TableCell>
      </>
    );
  }

  function renderTotal() {
    return (
      <TableRow data-testid="article-total" className="border-b-2 bg-surface-sunken font-semibold">
        <TableCell>{OFFER_TOTAL_LABEL}</TableCell>
        {headCells(offer.total, "total")}
        <TableCell colSpan={4} />
      </TableRow>
    );
  }

  function renderArticle(article: OurPriceOfferArticle) {
    const key = articleKey(article.work_category_id);
    const totals = article.lines_totals;
    const above = totals?.bargain ?? null;
    const below = totals?.below ?? null;
    const belowValue = below === null ? null : absDecimal(below.net);
    const mismatch = totals?.unit_mismatch_lines ?? 0;
    const expanded = open.has(key);
    return (
      <Fragment key={key}>
        <TableRow data-testid={`article-${key}`}>
          <TableCell>
            <button
              type="button"
              data-testid={`article-toggle-${key}`}
              aria-expanded={expanded}
              className="flex items-center gap-1 text-left"
              onClick={() => toggle(key)}
            >
              {expanded ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
              {article.code !== null && <span className="font-mono text-xs text-fg-muted">{article.code}</span>}
              {article.title}
            </button>
          </TableCell>
          {headCells(article, key)}
          <TableCell>
            {scale !== null && (
              <div className="flex w-40 items-center">
                <div className="flex flex-1 justify-end">
                  {belowValue !== null && (
                    <Bar testId={`art-bar-below-${key}`} value={belowValue} max={scale} tone="bg-info-text" />
                  )}
                </div>
                <div className="h-3 w-px bg-border" />
                <div className="flex-1">
                  {above !== null && (
                    <Bar testId={`art-bar-above-${key}`} value={above.net} max={scale} tone="bg-warning-text" />
                  )}
                </div>
              </div>
            )}
          </TableCell>
          <TableCell data-testid={`art-above-${key}`} className="text-right font-mono tabular-nums text-warning-text">
            {money(above, vat, formatSigned)}
          </TableCell>
          <TableCell data-testid={`art-below-${key}`} className="text-right font-mono tabular-nums text-info-text">
            {money(below, vat, formatBelow)}
          </TableCell>
          <TableCell data-testid={`art-none-${key}`} className="text-right font-mono text-xs tabular-nums">
            {money(totals?.not_comparable.total ?? null, vat, (v) => formatDecimalMoney(v, ""))}
            {mismatch > 0 && <span className="block text-warning-text">⚠ {mismatch}</span>}
          </TableCell>
        </TableRow>
        {expanded && (
          <TableRow>
            <TableCell colSpan={COLUMNS} className="bg-surface-sunken p-0">
              <OfferLinesTable
                lines={(linesByArticle.get(key) ?? []).filter((line) => matchesFilter(line, filter))}
                vat={vat}
                rateClass={rateClass}
                onOpenLine={onOpenLine}
              />
            </TableCell>
          </TableRow>
        )}
      </Fragment>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{OFFER_ARTICLES_TITLE}</CardTitle>
        <p className="text-xs text-fg-secondary">{LEGEND_MISMATCH}</p>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{OFFER_COLUMN.article}</TableHead>
              <TableHead className="text-right">{OFFER_COLUMN.perSqm}</TableHead>
              <TableHead className="text-right">{FORK_COLUMN_LABEL}</TableHead>
              <TableHead className="text-right">{OFFER_COLUMN.whole}</TableHead>
              <TableHead>{OFFER_COLUMN.bars}</TableHead>
              <TableHead className="text-right">{OFFER_COLUMN.above}</TableHead>
              <TableHead className="text-right">{OFFER_COLUMN.below}</TableHead>
              <TableHead className="text-right">{OFFER_COLUMN.none}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {renderTotal()}
            {offer.articles.map((article) => renderArticle(article))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
