import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

interface PagerProps {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

/**
 * Серверная пагинация: «назад / стр. N из M / вперёд».
 *
 * Композиция shadcn-примитивов `Pagination`, а не своя вёрстка. Номера страниц
 * не перечисляются намеренно: очередь Review начинается с ~1100 строк (§6.5), то
 * есть десятков страниц, и список номеров занял бы больше места, чем таблица.
 */
export function Pager({ page, total, pageSize, onPageChange }: PagerProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) return null;

  return (
    <Pagination className="mt-4">
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious
            aria-label="Предыдущая страница"
            aria-disabled={page <= 1}
            className={page <= 1 ? "pointer-events-none opacity-40" : undefined}
            onClick={(e) => {
              e.preventDefault();
              if (page > 1) onPageChange(page - 1);
            }}
          >
            Назад
          </PaginationPrevious>
        </PaginationItem>
        <PaginationItem>
          <span className="px-3 text-sm text-fg-secondary tabular-nums">
            {page} / {totalPages}
          </span>
        </PaginationItem>
        <PaginationItem>
          <PaginationNext
            aria-label="Следующая страница"
            aria-disabled={page >= totalPages}
            className={page >= totalPages ? "pointer-events-none opacity-40" : undefined}
            onClick={(e) => {
              e.preventDefault();
              if (page < totalPages) onPageChange(page + 1);
            }}
          >
            Вперёд
          </PaginationNext>
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}
