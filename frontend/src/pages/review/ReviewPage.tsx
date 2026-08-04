import { useState } from "react";
import { ArrowDownWideNarrow, ArrowUpAZ, Search } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { MergeTargetDialog } from "@/components/review/MergeTargetDialog";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { FilterPill } from "@/components/ui-domain/FilterPill";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebounce } from "@/lib/useDebounce";
import {
  useBatchReviewKind,
  useReviewQueue,
  useSetReviewKind,
  useUnits,
} from "@/services/queries";
import { MAX_REVIEW_BATCH, type ManualKind, type ReviewQueueItem, type ReviewSort } from "@/types/domain";

const PAGE_SIZE = 50;
const ALL_UNITS = "all";
const NO_UNIT = "none";

/**
 * Экран «Ручной матчинг» (AGENTS.md §7.2, §5).
 *
 * Очередь на реальной смете начинается с ~1100 строк, поэтому (решение §6.5):
 * пагинация серверная, сортировка по умолчанию — по числу ссылающихся позиций
 * (сначала самые «тяжёлые» работы: их разбор даёт наибольший прирост метрики §10),
 * есть фильтр по единице и подстроке, и есть **пакетная разметка** — первичный
 * разбор это в основном отсев не-работ, а построчно это тысяча нажатий.
 *
 * Строки, на которые не ссылается ни одна позиция, сервер не отдаёт вовсе
 * (решение §6.3) — решать по ним нечего.
 *
 * Права: ручной матчинг принадлежит и `member` (§3), поэтому admin-проверок здесь нет.
 */
export default function ReviewPage() {
  const [searchInput, setSearchInput] = useState("");
  const [unitFilter, setUnitFilter] = useState<string>(ALL_UNITS);
  const [sort, setSort] = useState<ReviewSort>("positions");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [mergeItem, setMergeItem] = useState<ReviewQueueItem | null>(null);

  const search = useDebounce(searchInput, 300);
  const unitsQ = useUnits();
  const queueQ = useReviewQueue({
    q: search || undefined,
    unit_id: unitFilter !== ALL_UNITS && unitFilter !== NO_UNIT ? Number(unitFilter) : undefined,
    without_unit: unitFilter === NO_UNIT || undefined,
    sort,
    page,
    page_size: PAGE_SIZE,
  });

  const setKind = useSetReviewKind();
  const batchKind = useBatchReviewKind();

  const data = queueQ.data;
  const items = data?.items ?? [];
  const allOnPageSelected = items.length > 0 && items.every((item) => selected.has(item.id));

  function resetPage() {
    setPage(1);
    setSelected(new Set());
  }

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllOnPage() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) items.forEach((item) => next.delete(item.id));
      else items.forEach((item) => next.add(item.id));
      return next;
    });
  }

  // Выделение живёт поверх страниц (оператор может набрать мусор с нескольких),
  // поэтому упереться в серверный потолок реально — и объяснить это надо здесь.
  const batchTooLarge = selected.size > MAX_REVIEW_BATCH;

  async function applyBatch(kind: ManualKind) {
    if (selected.size === 0 || batchTooLarge) return;
    await batchKind.mutateAsync({ ids: [...selected], kind });
    // Разобранные строки уходят из очереди — выделение больше ни к чему не
    // относится, и оставить его значило бы применить следующее действие к
    // строкам, которых на экране уже нет.
    setSelected(new Set());
  }

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Ручной матчинг"
        subtitle={
          data
            ? `В очереди работ: ${data.total}`
            : "Работы, которые матчинг не смог опознать автоматически"
        }
      />

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <InputGroup className="max-w-sm flex-1">
          <InputGroupInput
            aria-label="Поиск по названию работы"
            placeholder="Название работы"
            value={searchInput}
            onChange={(e) => {
              setSearchInput(e.target.value);
              resetPage();
            }}
          />
          <InputGroupAddon align="inline-start">
            <Search size={13} />
          </InputGroupAddon>
        </InputGroup>

        <Select
          value={unitFilter}
          onValueChange={(value: string | null) => {
            setUnitFilter(value ?? ALL_UNITS);
            resetPage();
          }}
        >
          <SelectTrigger className="w-48" aria-label="Единица измерения">
            <SelectValue>
              {(raw) => {
                if (!raw || raw === ALL_UNITS) return "Все единицы";
                if (raw === NO_UNIT) return "Без единицы";
                return unitsQ.data?.find((u) => String(u.id) === raw)?.name ?? "Все единицы";
              }}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_UNITS}>Все единицы</SelectItem>
            <SelectItem value={NO_UNIT}>Без единицы</SelectItem>
            {(unitsQ.data ?? []).map((unit) => (
              <SelectItem key={unit.id} value={String(unit.id)}>
                {unit.name} ({unit.code})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="flex items-center gap-2">
          <FilterPill
            active={sort === "positions"}
            label="По весу"
            onClick={() => {
              setSort("positions");
              resetPage();
            }}
          />
          <FilterPill
            active={sort === "title"}
            label="По названию"
            onClick={() => {
              setSort("title");
              resetPage();
            }}
          />
          {sort === "positions" ? (
            <ArrowDownWideNarrow className="size-4 text-fg-tertiary" aria-hidden />
          ) : (
            <ArrowUpAZ className="size-4 text-fg-tertiary" aria-hidden />
          )}
        </div>
      </div>

      {selected.size > 0 && (
        <Surface className="mt-4 flex flex-wrap items-center gap-3" tone="sunken">
          <span className="text-sm text-fg">Выбрано строк: {selected.size}</span>
          {batchTooLarge && (
            <span role="alert" className="text-sm text-warning-text">
              За один раз можно разметить не больше {MAX_REVIEW_BATCH} строк — пакет
              держит их заблокированными до конца транзакции. Снимите лишние.
            </span>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => void applyBatch("POSITION")}
              disabled={batchKind.isPending || batchTooLarge}
            >
              Утвердить как работы
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void applyBatch("HEADER")}
              disabled={batchKind.isPending || batchTooLarge}
            >
              Пометить заголовками
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => void applyBatch("TRASH")}
              disabled={batchKind.isPending || batchTooLarge}
            >
              В мусор
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Снять выделение
            </Button>
          </div>
        </Surface>
      )}

      <div className="mt-4">
        {queueQ.isPending && (
          <Surface padding="none">
            <div className="space-y-3 p-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          </Surface>
        )}

        {queueQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить очередь." />
        )}

        {data && items.length === 0 && (
          <EmptyState
            title="Очередь пуста"
            description="Все работы опознаны. Очередь наполнится при следующей загрузке сметы."
          />
        )}

        {data && items.length > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <Checkbox
                        aria-label="Выбрать все строки на странице"
                        checked={allOnPageSelected}
                        onCheckedChange={toggleAllOnPage}
                      />
                    </TableHead>
                    <TableHead>Работа</TableHead>
                    <TableHead>Единица</TableHead>
                    <TableHead className="text-right">Позиций</TableHead>
                    <TableHead>Действия</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell>
                        <Checkbox
                          aria-label={`Выбрать «${item.standard_job_title}»`}
                          checked={selected.has(item.id)}
                          onCheckedChange={() => toggle(item.id)}
                        />
                      </TableCell>
                      <TableCell>
                        <div className="font-medium text-fg">{item.standard_job_title}</div>
                        {item.sample_titles.length > 0 && (
                          <ul className="mt-0.5 text-xs text-fg-tertiary">
                            {item.sample_titles.map((title) => (
                              <li key={title} className="truncate">
                                в смете: {title}
                              </li>
                            ))}
                          </ul>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {item.unit_code ?? <span className="text-fg-tertiary">без единицы</span>}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {item.position_count}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1.5">
                          <Button size="xs" variant="outline" onClick={() => setMergeItem(item)}>
                            Слить
                          </Button>
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={setKind.isPending}
                            onClick={() =>
                              setKind.mutate({ toReviewId: item.id, kind: "POSITION" })
                            }
                          >
                            Это работа
                          </Button>
                          <Button
                            size="xs"
                            variant="ghost"
                            disabled={setKind.isPending}
                            onClick={() => setKind.mutate({ toReviewId: item.id, kind: "HEADER" })}
                          >
                            Заголовок
                          </Button>
                          <Button
                            size="xs"
                            variant="ghost"
                            disabled={setKind.isPending}
                            onClick={() => setKind.mutate({ toReviewId: item.id, kind: "TRASH" })}
                          >
                            Мусор
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>

            <Pager page={page} total={data.total} pageSize={PAGE_SIZE} onPageChange={setPage} />
          </>
        )}
      </div>

      <MergeTargetDialog item={mergeItem} onOpenChange={(open) => !open && setMergeItem(null)} />
    </div>
  );
}
