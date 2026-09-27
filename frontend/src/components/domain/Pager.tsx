import { useId } from "react";

import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { PAGE_SIZE_OPTIONS } from "./pageSize";

interface PagerProps {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  /**
   * Выбор размера страницы (спека §2.7). Необязателен намеренно: без него
   * вид не меняется для пяти существующих экранов (`ContractsPage`,
   * `MatrixPage`, `ReviewPage`, `StandardsPage`, `TendersPage`).
   */
  onPageSizeChange?: (n: number) => void;
  /** Варианты выбора размера — умолчание {@link PAGE_SIZE_OPTIONS}. */
  pageSizeOptions?: readonly number[];
  /** Показать номера страниц вместо «стр. N из M» — см. докстрока ниже. */
  showPageNumbers?: boolean;
}

type PageToken = number | "ellipsis";

/**
 * Первая, последняя, текущая и её соседи — остальное схлопывается в
 * многоточие при разрыве больше единицы. Даёт ровно три последовательности
 * из утверждений спеки §2.7: 5 страниц/текущая 1 → `1, 2, …, 5`;
 * 5 страниц/текущая 3 → `1, 2, 3, 4, 5`; 12 страниц/текущая 6 →
 * `1, …, 5, 6, 7, …, 12`.
 */
function buildPageTokens(current: number, totalPages: number): PageToken[] {
  const wanted = [1, current - 1, current, current + 1, totalPages]
    .filter((p) => p >= 1 && p <= totalPages);
  const pages = [...new Set(wanted)].sort((a, b) => a - b);

  const tokens: PageToken[] = [];
  let previous: number | null = null;
  for (const p of pages) {
    if (previous !== null && p - previous > 1) tokens.push("ellipsis");
    tokens.push(p);
    previous = p;
  }
  return tokens;
}

/**
 * Пагинация: «назад / стр. N из M / вперёд» без новых props — так остаются
 * пять существующих экранов (`ContractsPage`, `MatrixPage`, `ReviewPage`,
 * `StandardsPage`, `TendersPage`). Композиция shadcn-примитивов
 * `Pagination`, а не своя вёрстка.
 *
 * **Номера страниц — только по `showPageNumbers`, опт-ин.** Очередь Review
 * начинается с ~1100 строк (§6.5), то есть десятков страниц, и список
 * номеров занял бы больше места, чем таблица — там `showPageNumbers`
 * остаётся не задан, и вид не меняется. Экран «Семьи и контексты» задаёт
 * его явно: там страниц единицы-десятки (спека §2.7).
 *
 * **Выбор размера остаётся виден даже на единственной странице**, если
 * `onPageSizeChange` задан: без этого условия оператор, выбравший 100 на
 * большом фильтре, а затем сузивший его до одной страницы, не смог бы
 * вернуться к 10 — контрол выбора размера пропал бы вместе с самой
 * пагинацией. Номера же страниц на единственной странице не показываются
 * никогда (`totalPages <= 1` рано выходит из блока `<Pagination>`) — там
 * показывать нечего.
 */
export function Pager({
  page,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  showPageNumbers = false,
}: PagerProps) {
  const pageSizeSelectId = useId();
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1 && !onPageSizeChange) return null;

  return (
    <div className="mt-4 flex flex-wrap items-center justify-center gap-4">
      {onPageSizeChange && (
        <div className="flex items-center gap-2">
          <Label htmlFor={pageSizeSelectId} className="text-sm whitespace-nowrap text-fg-tertiary">
            На странице:
          </Label>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => {
              if (v) onPageSizeChange(Number(v));
            }}
          >
            <SelectTrigger id={pageSizeSelectId} size="sm" className="w-20">
              <SelectValue>{(raw) => raw ?? String(pageSize)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {pageSizeOptions.map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {totalPages > 1 && (
        <Pagination className="mx-0 w-auto">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                text="Назад"
                aria-label="Предыдущая страница"
                aria-disabled={page <= 1}
                className={page <= 1 ? "pointer-events-none opacity-40" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  if (page > 1) onPageChange(page - 1);
                }}
              />
            </PaginationItem>

            {showPageNumbers ? (
              buildPageTokens(page, totalPages).map((token, i) =>
                token === "ellipsis" ? (
                  <PaginationItem key={`ellipsis-${i}`}>
                    <PaginationEllipsis />
                  </PaginationItem>
                ) : (
                  <PaginationItem key={token}>
                    <PaginationLink
                      isActive={token === page}
                      onClick={(e) => {
                        e.preventDefault();
                        if (token !== page) onPageChange(token);
                      }}
                    >
                      {token}
                    </PaginationLink>
                  </PaginationItem>
                )
              )
            ) : (
              <PaginationItem>
                <span className="px-3 text-sm text-fg-secondary tabular-nums">
                  {page} / {totalPages}
                </span>
              </PaginationItem>
            )}

            <PaginationItem>
              <PaginationNext
                text="Вперёд"
                aria-label="Следующая страница"
                aria-disabled={page >= totalPages}
                className={page >= totalPages ? "pointer-events-none opacity-40" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  if (page < totalPages) onPageChange(page + 1);
                }}
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      )}
    </div>
  );
}
