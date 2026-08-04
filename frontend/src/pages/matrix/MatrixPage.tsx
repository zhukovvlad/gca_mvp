import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import { Search } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { MatrixCellDialog } from "@/components/matrix/MatrixCellDialog";
import { DeviationCell } from "@/components/ui-domain/DeviationCell";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useDebounce } from "@/lib/useDebounce";
import { useMatrix, useRateClasses } from "@/services/queries";
import type { MatrixCell, MatrixColumn, MatrixRow } from "@/types/domain";

const PAGE_SIZE = 50;
const ALL_CLASSES = "all";

/**
 * Сквозная матрица (AGENTS.md §6, §7.5).
 *
 * Строки — каталожные работы (`kind='POSITION'`), колонки — договоры с группировкой
 * по объекту, в ячейке средневзвешенная ставка и отклонение цветом. Считает всё
 * сервер (решение §6.3, замером); здесь только раскладка и фильтры.
 *
 * **Пагинация серверная, по строкам** (решение §6.4): строки — весь каталог, а он
 * растёт с каждым импортом на ~1100 позиций, тогда как колонки ограничены
 * бизнес-величиной (договоры ГП 2024–2027) и сужаются фильтрами §6. Виртуализация
 * ограничила бы DOM, но не ответ сервера, а тяжесть именно в нём: ячеек = строки ×
 * колонки.
 *
 * **Первая колонка закреплена** — этого требует §6. `position: sticky` на ячейках
 * первой колонки: при десятке договоров таблица уезжает по горизонтали, и без
 * закрепления непонятно, к какой работе относится ячейка.
 *
 * Фильтра «статья/раздел» нет намеренно: §6 исключает его из MVP, потому что у него
 * нет формального источника — разделы специфичны для каждой сметы, а строки матрицы
 * каталожные. Min/max/разброс тоже вне MVP (§6).
 */

/** Строка таблицы: серверная строка плюс индекс ячеек по договору. */
interface MatrixTableRow extends MatrixRow {
  byContract: Map<number, MatrixCell>;
}

/** Пометка про очередь ручного матчинга — добавляется к любой причине пустоты. */
function pendingReviewNote(pending: number): string {
  return (
    ` Кроме того, ${pending} расценённых позиций ждут ручного матчинга: пока работа не ` +
    "утверждена в каталоге, она не попадает ни в матрицу, ни в нормативы (AGENTS.md §4)."
  );
}

/**
 * Почему матрица пуста. Причины разные, и путать их нельзя.
 *
 * Порядок важен и выстрадан двумя находками:
 *
 * 1. **Прогон стенда.** На живой базе сметы были загружены и расценены, но каталог
 *    целиком состоял из `TO_REVIEW`, а экран предлагал «попробовать другой текст
 *    поиска» — отправлял искать несуществующую проблему. Так появилась причина
 *    «ждут матчинга».
 * 2. **Замечание внешнего ревью.** Обратная ошибка: счётчик очереди относится к
 *    выборке, а не к поиску, поэтому при непустом `q` он **не объясняет** пустоту
 *    результата. Экран всё равно предпочитал сообщение об очереди, и человек,
 *    искавший несуществующую работу, снова шёл не туда.
 *
 * Отсюда правило: называется причина, которую человек создал сам (поиск), а очередь
 * упоминается **дополнением** — она объясняет, почему матрица в целом тонкая, но не
 * почему не нашёлся конкретный текст. Скрывать её нельзя: именно так и появился
 * дефект, найденный стендом.
 */
function emptyReason(
  matrix:
    | { columns: unknown[]; positions_pending_review: number; positions_non_work: number }
    | undefined,
  search: string
): string {
  if (matrix === undefined) {
    return "Данные не загрузились. Обновите страницу.";
  }
  const pending = matrix.positions_pending_review;

  if (matrix.columns.length === 0) {
    return (
      "В выборку не попал ни один договор с загруженной сметой. Ослабьте фильтры класса и периода." +
      (pending > 0 ? pendingReviewNote(pending) : "")
    );
  }
  if (search) {
    return (
      "По запросу работ не найдено. Проверьте текст поиска." +
      (pending > 0 ? pendingReviewNote(pending) : "")
    );
  }
  if (pending > 0) {
    return (
      `Сметы загружены, но ${pending} расценённых позиций ждут ручного матчинга: пока работа ` +
      "не утверждена в каталоге, она не попадает ни в матрицу, ни в нормативы (AGENTS.md §4)."
    );
  }
  if (matrix.positions_non_work > 0) {
    // Та же третья причина, что в паспорте: строки уже разобраны и правки не требуют.
    return (
      `Сметы загружены, но все ${matrix.positions_non_work} расценённых позиций отнесены к ` +
      "строкам, помеченным как не-работа (раздел, заголовок лота или мусор). Такие строки в " +
      "матрицу не попадают — это нормально."
    );
  }
  return "Ни одна работа не подошла под фильтры класса и периода.";
}

const features = tableFeatures({});
const helper = createColumnHelper<typeof features, MatrixTableRow>();

export default function MatrixPage() {
  const [rateClass, setRateClass] = useState<string>(ALL_CLASSES);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [openCell, setOpenCell] = useState<{ contractId: number; row: MatrixRow } | null>(null);

  const q = useDebounce(search.trim(), 300);
  const classesQ = useRateClasses();

  const params = useMemo(
    () => ({
      rate_class_id: rateClass === ALL_CLASSES ? undefined : Number(rateClass),
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      q: q || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [rateClass, dateFrom, dateTo, q, page]
  );

  const matrixQ = useMatrix(params);
  const matrix = matrixQ.data;

  const rows = useMemo<MatrixTableRow[]>(
    () =>
      (matrix?.rows ?? []).map((row) => ({
        ...row,
        // Индекс по договору: ячейки приходят списком (у JSON нет числовых ключей),
        // а таблице нужен доступ по колонке. Строится один раз на выборку.
        byContract: new Map(row.cells.map((cell) => [cell.contract_id, cell])),
      })),
    [matrix?.rows]
  );

  const columns = useMemo(
    () => buildColumns(matrix?.columns ?? [], (contractId, row) => setOpenCell({ contractId, row })),
    [matrix?.columns]
  );

  const table = useTable({ features, columns, data: rows });

  /** Сброс на первую страницу: смена фильтра меняет состав строк целиком. */
  function resetPage<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      setPage(1);
    };
  }

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Сквозная матрица"
        subtitle="Одна работа — по всем договорам. В ячейке средневзвешенная ставка и отклонение от норматива."
      />

      <Surface className="mt-6" padding="sm">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label htmlFor="matrix-search">Работа</Label>
            <InputGroup className="mt-1.5">
              <InputGroupAddon>
                <Search className="size-4" />
              </InputGroupAddon>
              <InputGroupInput
                id="matrix-search"
                placeholder="часть названия"
                value={search}
                onChange={(event) => resetPage(setSearch)(event.target.value)}
              />
            </InputGroup>
          </div>

          <div>
            <Label htmlFor="matrix-class">Класс объектов</Label>
            {/* `onValueChange` у base-ui отдаёт `string | null` — null означает
                сброс выбора, и он равнозначен «все классы». */}
            <Select
              value={rateClass}
              onValueChange={(value) => resetPage(setRateClass)(value ?? ALL_CLASSES)}
            >
              <SelectTrigger id="matrix-class" className="mt-1.5 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_CLASSES}>Все классы</SelectItem>
                {(classesQ.data ?? []).map((rateClassItem) => (
                  <SelectItem key={rateClassItem.id} value={String(rateClassItem.id)}>
                    {rateClassItem.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label htmlFor="matrix-date-from">Смета с</Label>
            <Input
              id="matrix-date-from"
              type="date"
              className="mt-1.5"
              value={dateFrom}
              onChange={(event) => resetPage(setDateFrom)(event.target.value)}
            />
          </div>

          <div>
            <Label htmlFor="matrix-date-to">Смета по</Label>
            <Input
              id="matrix-date-to"
              type="date"
              className="mt-1.5"
              value={dateTo}
              onChange={(event) => resetPage(setDateTo)(event.target.value)}
            />
          </div>
        </div>
        <p className="mt-3 text-xs text-fg-tertiary">
          Период — по дате сметы (при её отсутствии по дате договора): именно по этой дате
          подбирается норматив.
        </p>
      </Surface>

      {matrixQ.isPending ? (
        <Skeleton className="mt-6 h-64 w-full" />
      ) : matrix === undefined || matrix.rows.length === 0 ? (
        <EmptyState
          className="mt-6"
          title="Нечего сравнивать"
          description={emptyReason(matrix, q)}
          action={
            matrix !== undefined && matrix.positions_pending_review > 0 ? (
              <Button variant="outline" render={<Link to="/review">Разобрать очередь</Link>} />
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="mt-6 overflow-x-auto rounded-lg border border-border-subtle bg-surface">
            <table className="w-full caption-bottom border-collapse text-sm">
              <thead className="[&_tr]:border-b [&_tr]:border-border-subtle">
                {table.getHeaderGroups().map((group) => (
                  <tr key={group.id}>
                    {group.headers.map((header) => (
                      <th
                        key={header.id}
                        colSpan={header.colSpan}
                        scope="col"
                        className={cn(
                          "h-10 px-3 text-left align-middle text-xs font-medium text-fg-secondary",
                          // Закрепление первой колонки (§6) — и в шапке тоже, иначе
                          // при прокрутке заголовок «Работа» уезжает, а ячейки нет.
                          header.column.id === "job" &&
                            "sticky left-0 z-20 min-w-64 max-w-80 bg-surface"
                        )}
                      >
                        {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="border-b border-border-subtle last:border-0">
                    {row.getAllCells().map((cell) => (
                      <td
                        key={cell.id}
                        className={cn(
                          "px-3 py-2 align-top",
                          cell.column.id === "job"
                            ? "sticky left-0 z-10 min-w-64 max-w-80 bg-surface"
                            : "text-right"
                        )}
                      >
                        <table.FlexRender cell={cell} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pager page={matrix.page} total={matrix.total} pageSize={matrix.page_size} onPageChange={setPage} />
          <p className="mt-2 text-xs text-fg-tertiary">
            Работ в выборке: {matrix.total}. Порядок — по суммарной стоимости работы во всех
            договорах выборки, поэтому на первой странице самое весомое. Договоров: {matrix.columns.length}.
          </p>
        </>
      )}

      <MatrixCellDialog
        open={openCell !== null}
        contractId={openCell?.contractId}
        row={openCell?.row}
        column={matrix?.columns.find((c) => c.contract_id === openCell?.contractId)}
        onClose={() => setOpenCell(null)}
      />
    </div>
  );
}

/**
 * Колонки: закреплённая «Работа» плюс группа на объект, внутри — договоры.
 *
 * Группировка по объекту — требование §6, и `helper.group` выражает её ровно так,
 * как она задумана: два уровня шапки, объект над своими договорами. Порядок групп
 * приходит с сервера (он же упорядочил колонки по названию объекта) — здесь он
 * только сохраняется, а не пересобирается.
 */
function buildColumns(
  serverColumns: MatrixColumn[],
  onCellClick: (contractId: number, row: MatrixRow) => void
) {
  const byObject = new Map<number, { title: string; columns: MatrixColumn[] }>();
  for (const column of serverColumns) {
    const group = byObject.get(column.object_id);
    if (group) group.columns.push(column);
    else byObject.set(column.object_id, { title: column.object_title, columns: [column] });
  }

  const jobColumn = helper.display({
    id: "job",
    header: "Работа",
    /*
      Наименование зажато по ширине и высоте, полный текст — в `title`.

      **Найдено прогоном стенда, не тестом.** В реальном каталоге есть работа с
      наименованием на 5077 символов (§11 AGENTS.md), и без зажима её ячейка выросла
      до 1323 px — одна строка выше листа А4, а остальные строки страницы уезжали
      за экран. В jsdom этого не видно: там нет раскладки, и высота всегда нулевая.

      `max-w-*` на самой `td` не работает — табличная раскладка её игнорирует
      (замер: ячейка при `max-w-80` выросла до 595 px), поэтому ограничение стоит на
      вложенном блоке.
    */
    cell: ({ row }) => (
      <div className="max-w-[22rem]">
        <span className="line-clamp-3 text-fg" title={row.original.job_title}>
          {row.original.job_title}
        </span>
        {row.original.unit_code && (
          <span className="text-xs text-fg-tertiary">{row.original.unit_code}</span>
        )}
      </div>
    ),
  });

  const objectGroups = [...byObject.entries()].map(([objectId, group]) =>
    helper.group({
      id: `object-${objectId}`,
      header: group.title,
      columns: group.columns.map((column) =>
        helper.display({
          id: `contract-${column.contract_id}`,
          header: column.contract_number,
          cell: ({ row }) => {
            const cell = row.original.byContract.get(column.contract_id);
            if (!cell) {
              // Пусто — работы нет в этой смете. Это НЕ то же, что «нет норматива»
              // (§10): там ставка есть, а сравнить не с чем.
              return <span className="text-fg-tertiary" title="Работы нет в смете этого договора">·</span>;
            }
            return (
              <button
                type="button"
                className="w-full text-right hover:underline"
                onClick={() => onCellClick(column.contract_id, row.original)}
                title="Показать позиции сметы, из которых сложилась ставка"
              >
                <MoneyCell
                  value={cell.rate}
                  currency=""
                  maxFractionDigits={2}
                  className="block"
                />
                <DeviationCell value={cell.deviation_pct} variant="compact" className="block text-xs" />
              </button>
            );
          },
        })
      ),
    })
  );

  return helper.columns([jobColumn, ...objectGroups]);
}
