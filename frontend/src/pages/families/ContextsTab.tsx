import { useState } from "react";
import { Search } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useDebounce } from "@/lib/useDebounce";
import { useSemanticContexts } from "@/services/queries";
import type { ContextRow, NameRole, SemanticKind, SemanticState } from "@/types/domain";

import { ContextCard } from "./ContextCard";
import { NAME_ROLE_LABEL, SEMANTIC_KIND_LABEL, SEMANTIC_STATE_LABEL } from "./labels";
import { SourceChip } from "./SourceChip";
import { usePersistedPageSize } from "./usePersistedPageSize";

const ANY = "any";
const DEFAULT_PAGE_SIZE = 20;
const KIND_OPTIONS: SemanticKind[] = ["WORK", "SYSTEM", "UNKNOWN"];
const ROLE_OPTIONS: NameRole[] = ["WORK", "LOCATION_ONLY", "GENERIC_WORK"];
const STATE_OPTIONS: SemanticState[] = ["SUGGESTED", "CONFIRMED", "NOT_APPLICABLE"];

/**
 * Причины точки внимания строки (спека §2.4, §2.6) — «пустой» вытесняет
 * остальные два (у контекста без членств не бывает ни устаревших, ни
 * конфликтных: обоим нужны членства, которых здесь нет), а устаревшие и
 * конфликтные — независимые оси и печатаются ОБЕ, если обе истинны.
 */
function attentionReasons(
  row: Pick<ContextRow, "member_count" | "has_stale_members" | "has_conflicting_members">
): string[] {
  if (row.member_count === 0) return ["пустой — позиций нет"];
  const reasons: string[] = [];
  if (row.has_stale_members) reasons.push("есть устаревшие позиции");
  if (row.has_conflicting_members) reasons.push("есть конфликтные позиции");
  return reasons;
}

/** Точка «требует внимания» строки (спека §2.4) — нет узла вовсе, если причин нет. */
function AttentionDot({ row }: { row: ContextRow }) {
  const reasons = attentionReasons(row);
  if (reasons.length === 0) return null;
  const text = reasons.join("; ");

  return (
    <Tooltip>
      <TooltipTrigger
        title={text}
        render={
          <span
            data-testid="attention-dot"
            aria-label={`Требует внимания: ${text}`}
            className="inline-block h-2 w-2 flex-none rounded-full bg-warning"
          />
        }
      />
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}

/**
 * Полный путь классификатора строки (спека §2.4) — путь строки
 * (`work_category_path`, родители СВЕРХУ ВНИЗ, без самой статьи) плюс сама
 * статья последней. `null` — у строки нет статьи вовсе (корзина без
 * назначенной статьи), подсказки тогда нет.
 */
function classifierPathTitle(row: ContextRow): string | null {
  if (!row.work_category_code || !row.work_category_title) return null;
  const segments = [
    ...row.work_category_path,
    { code: row.work_category_code, title: row.work_category_title },
  ];
  return segments.map((s) => `${s.code} ${s.title}`).join(" › ");
}

/**
 * Очередь контекстов — вкладка «Контексты» (спека §2.1, §2.4, §2.7).
 * Самодостаточна: выбор строки и её карточка — панель СПРАВА ОТ СПИСКА
 * ({@link ContextCard}), тем же компонентом, что несла отдельная вкладка
 * «Операции» до этой правки (вкладка упразднена, `FamiliesPage` больше не
 * управляет выбором).
 *
 * Три фильтра-признака (`has_stale_members`, `has_conflicting_members`,
 * `has_no_members`) — три отдельных чекбокса, а не один общий: это разные
 * оси и вход с обоими сразу обязан проходить оба фильтра.
 */
export function ContextsTab() {
  const [searchInput, setSearchInput] = useState("");
  const [categoryInput, setCategoryInput] = useState("");
  const [kind, setKind] = useState<string>(ANY);
  const [role, setRole] = useState<string>(ANY);
  const [state, setState] = useState<string>(ANY);
  const [hasStale, setHasStale] = useState(false);
  const [hasConflicting, setHasConflicting] = useState(false);
  const [hasNoMembers, setHasNoMembers] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.contexts.pageSize",
    DEFAULT_PAGE_SIZE
  );
  const [selectedContextId, setSelectedContextId] = useState<number | null>(null);

  const search = useDebounce(searchInput, 300);
  const categoryId = useDebounce(categoryInput, 300);

  const contextsQ = useSemanticContexts({
    catalog_query: search || undefined,
    work_category_id: categoryId ? Number(categoryId) : undefined,
    semantic_kind: kind === ANY ? undefined : (kind as SemanticKind),
    name_role: role === ANY ? undefined : (role as NameRole),
    semantic_state: state === ANY ? undefined : (state as SemanticState),
    has_stale_members: hasStale || undefined,
    has_conflicting_members: hasConflicting || undefined,
    has_no_members: hasNoMembers || undefined,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  });

  const data = contextsQ.data;

  // Смена фильтра ИЛИ страницы не сбрасывает выбор, пока выбранный контекст
  // есть в выдаче (спека §2.1); снимает — если его в новой выдаче нет. Одна
  // проверка покрывает обе причины смены выдачи: обеим отвечает то же
  // условие «пришли настоящие данные, и id в них нет» — раздельная логика на
  // фильтр/страницу дублировала бы её и разошлась бы при следующей правке.
  //
  // Правка состояния ВО ВРЕМЯ РЕНДЕРА (тот же приём, что `ContextCard` несёт
  // для `syncedCardKey`), а не в эффекте — `react-hooks/set-state-in-effect`
  // запрещает `setState` в теле эффекта. Страж `data` — не `isPending`/
  // `isSuccess`: пока страница ещё грузится (`data` не пришли), снимать
  // нечего, а как только `data` пришли, ложное срабатывание невозможно —
  // условие проверяет саму выдачу, а не отдельно её статус (спека §2.1:
  // `isSuccess` рядом с `data` был эквивалентным решением). Цикла
  // рендеров тоже нет: выбор меняет только щелчок по строке ТЕКУЩЕЙ выдачи
  // (id туда попадает, только пока он в ней есть), а `setSelectedContextId(null)`
  // срабатывает ровно один раз — как только он сработал, `selectedContextId
  // === null`, и всё условие ложно на следующем рендере той же выдачи.
  if (
    selectedContextId !== null &&
    data &&
    !data.items.some((row) => row.id === selectedContextId)
  ) {
    setSelectedContextId(null);
  }

  // Зажим страницы (спека §2.7): выдача сузилась (действие в карточке,
  // фильтр соседа) при том же `page`/`offset`, и текущий `offset`
  // больше не попадает в неё — сервер уже ответил меньшим `total`, чем
  // требует текущая страница. Правка состояния во время рендера, тем же
  // приёмом, что снятие выбора выше: следующий рендер пересчитывает `offset`
  // от исправленного `page`, и запрос уходит уже с ним. `total === 0` не
  // нужен отдельным стражем: `Math.ceil(0 / pageSize)` и так даёт 0, а
  // `Math.max(1, …)` поднимает его до 1 — та же страница 1, что и при
  // положительном `total`.
  if (data) {
    const lastValidPage = Math.max(1, Math.ceil(data.total / pageSize));
    if (page > lastValidPage) {
      setPage(lastValidPage);
    }
  }

  function resetToFirstPage() {
    setPage(1);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-start">
      <div className="grid gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <InputGroup className="max-w-sm flex-1">
            <InputGroupInput
              aria-label="Поиск по написанию каталога"
              placeholder="Написание в каталоге"
              value={searchInput}
              onChange={(e) => {
                setSearchInput(e.target.value);
                resetToFirstPage();
              }}
            />
            <InputGroupAddon align="inline-start">
              <Search size={13} />
            </InputGroupAddon>
          </InputGroup>

          <div className="grid gap-1">
            <Label htmlFor="filter-category" className="text-xs text-fg-tertiary">Статья (id)</Label>
            <Input
              id="filter-category"
              className="w-28"
              inputMode="numeric"
              value={categoryInput}
              onChange={(e) => {
                setCategoryInput(e.target.value);
                resetToFirstPage();
              }}
            />
          </div>

          <div className="grid gap-1">
            <Label htmlFor="filter-kind" className="text-xs text-fg-tertiary">Вид</Label>
            <Select value={kind} onValueChange={(v) => { setKind(v ?? ANY); resetToFirstPage(); }}>
              <SelectTrigger id="filter-kind" className="w-40">
                <SelectValue>
                  {(raw) =>
                    !raw || raw === ANY ? "Любой вид" : SEMANTIC_KIND_LABEL[raw as SemanticKind]
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любой вид</SelectItem>
                {KIND_OPTIONS.map((k) => (
                  <SelectItem key={k} value={k}>{SEMANTIC_KIND_LABEL[k]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1">
            <Label htmlFor="filter-role" className="text-xs text-fg-tertiary">Роль имени</Label>
            <Select value={role} onValueChange={(v) => { setRole(v ?? ANY); resetToFirstPage(); }}>
              <SelectTrigger id="filter-role" className="w-44">
                <SelectValue>
                  {(raw) => (!raw || raw === ANY ? "Любая роль" : NAME_ROLE_LABEL[raw as NameRole])}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любая роль</SelectItem>
                {ROLE_OPTIONS.map((r) => (
                  <SelectItem key={r} value={r}>{NAME_ROLE_LABEL[r]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1">
            <Label htmlFor="filter-state" className="text-xs text-fg-tertiary">Состояние</Label>
            <Select value={state} onValueChange={(v) => { setState(v ?? ANY); resetToFirstPage(); }}>
              <SelectTrigger id="filter-state" className="w-48">
                <SelectValue>
                  {(raw) =>
                    !raw || raw === ANY ? "Любое состояние" : SEMANTIC_STATE_LABEL[raw as SemanticState]
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любое состояние</SelectItem>
                {STATE_OPTIONS.map((s) => (
                  <SelectItem key={s} value={s}>{SEMANTIC_STATE_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="filter-stale"
              checked={hasStale}
              onCheckedChange={(checked) => { setHasStale(checked === true); resetToFirstPage(); }}
            />
            <Label htmlFor="filter-stale" className="text-sm font-normal">Есть устаревшие членства</Label>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="filter-conflicting"
              checked={hasConflicting}
              onCheckedChange={(checked) => { setHasConflicting(checked === true); resetToFirstPage(); }}
            />
            <Label htmlFor="filter-conflicting" className="text-sm font-normal">Есть конфликтные членства</Label>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="filter-no-members"
              checked={hasNoMembers}
              onCheckedChange={(checked) => { setHasNoMembers(checked === true); resetToFirstPage(); }}
            />
            <Label htmlFor="filter-no-members" className="text-sm font-normal">Нет членств</Label>
          </div>
        </div>

        {contextsQ.isPending && <Skeleton className="h-40 w-full" />}

        {contextsQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить контексты." />
        )}

        {data && data.items.length === 0 && (
          <EmptyState
            title="Контекстов нет"
            description="Ни один контекст не подходит под текущие фильтры."
          />
        )}

        {data && data.items.length > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Контекст</TableHead>
                    <TableHead>Единица</TableHead>
                    <TableHead>Семья</TableHead>
                    <TableHead>Позиций</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row) => {
                    const familyCaption =
                      row.work_family_id !== null
                        ? row.family_title ?? `Семья #${row.work_family_id}`
                        : row.comparability_reason === "insufficient_description"
                          ? "семья не назначена, потому что состав не описан"
                          // Спека §2.4: «семья (или «—»)» — простой прочерк,
                          // без семьи и без особой причины (живой прогон).
                          : "—";
                    const pathTitle = classifierPathTitle(row);
                    return (
                      <TableRow
                        key={row.id}
                        role="row"
                        tabIndex={0}
                        aria-selected={row.id === selectedContextId}
                        onClick={() => setSelectedContextId(row.id)}
                        onKeyDown={(e) => {
                          // Тот же пробел доступности, что у строк семей
                          // (спека §2.4) — щелчок мышью не единственный путь.
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setSelectedContextId(row.id);
                          }
                        }}
                        className={cn(
                          "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                          row.id === selectedContextId && "bg-surface-hover"
                        )}
                      >
                        <TableCell className="max-w-sm min-w-0 align-top whitespace-normal">
                          <div className="flex flex-wrap items-start gap-2">
                            <span
                              title={row.standard_job_title}
                              className="line-clamp-2 font-medium break-words text-fg"
                            >
                              {row.standard_job_title}
                            </span>
                            {row.semantic_kind === "SYSTEM" && (
                              <Badge variant="outline">система</Badge>
                            )}
                            {row.archived_at && <Badge variant="outline">архивный</Badge>}
                          </div>
                          {pathTitle ? (
                            <div className="mt-1 flex min-w-0 items-center gap-1">
                              <SourceChip kind="classifier" />
                              <Tooltip>
                                <TooltipTrigger
                                  title={pathTitle}
                                  render={
                                    <span className="min-w-0 flex-1 truncate text-xs text-fg-tertiary">
                                      {row.work_category_code} {row.work_category_title}
                                    </span>
                                  }
                                />
                                <TooltipContent>{pathTitle}</TooltipContent>
                              </Tooltip>
                            </div>
                          ) : (
                            <div className="mt-1 text-xs text-fg-tertiary">—</div>
                          )}
                        </TableCell>
                        <TableCell>{row.unit_code ?? "—"}</TableCell>
                        <TableCell>{familyCaption}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2 tabular-nums">
                            <span>{row.member_count}</span>
                            <AttentionDot row={row} />
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </Surface>

            <p className="text-sm text-fg-tertiary tabular-nums">
              {data.offset + 1}–{data.offset + data.items.length} из {data.total}
            </p>

            <Pager
              page={page}
              total={data.total}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={(n) => {
                setPageSize(n);
                resetToFirstPage();
              }}
              showPageNumbers
            />
          </>
        )}
      </div>

      {/* `key` на элементе карточки (спека §2.5, §2.8) — всё состояние карточки
          (выбор, кэш id группы, открыт/страница у КАЖДОЙ секции группы,
          отложенные ответы запросов группы) принадлежит ОДНОМУ контексту.
          Новый контекст монтирует СВЕЖУЮ карточку: React снимает старое
          дерево и создаёт новое, поэтому ни состояние, ни поздний ответ
          прежнего контекста не могут долететь до новой карточки — сеть
          `groupMemberIds`, пришедшая после переключения, и состояние секции
          группы, совпавшее ключом раздела с чужим контекстом, применяются
          уже к снятому дереву и не видны новой карточке. */}
      <ContextCard key={selectedContextId} contextId={selectedContextId} />
    </div>
  );
}
