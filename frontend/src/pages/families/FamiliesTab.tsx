import { useState } from "react";
import { Plus } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  useActivateWorkFamily,
  useArchiveWorkFamily,
  useCreateWorkFamily,
  useMergeWorkFamilies,
  useUnits,
  useUpdateWorkFamily,
  useWorkFamilies,
} from "@/services/queries";
import type { WorkFamily, WorkFamilyStatus } from "@/types/domain";

import { FAMILY_STATUS_LABEL } from "./labels";
import { usePersistedPageSize } from "./usePersistedPageSize";

/**
 * Заливка статуса семьи (сверка с макетом 27.09.2026, `mock-families.png`,
 * мокап `.pill.ok`/`.mid`/`.gray`) — активна зелёным (`accent`, тот же тон,
 * что несёт primary-действие приложения), черновик амбером (`warning`,
 * общий тон с чипом «в смете»), в архиве серым (`neutral`). Три статуса
 * обязаны различаться КЛАССОМ — тест `FamiliesTab.test.tsx` сравнивает их,
 * не цвет (jsdom вычисленный цвет не видит).
 */
const FAMILY_STATUS_TINT: Record<WorkFamilyStatus, string> = {
  active: "border-accent-border bg-accent-soft text-accent-text",
  draft: "border-warning-border bg-warning-soft text-warning-text",
  archived: "border-neutral-border bg-neutral-soft text-neutral-text",
};

const ANY = "any";
const DEFAULT_PAGE_SIZE = 20;
const STATUS_OPTIONS: WorkFamilyStatus[] = ["draft", "active", "archived"];

/**
 * Семьи работ — вкладка «Семьи» (спека `2026-09-25-families-screen-design.md`
 * §2.1, §2.7). Список слева, панель правки выбранной семьи справа — щелчок по
 * строке (не отдельная кнопка), состав действий панели тот же, что нёс
 * прежний диалог правки фичи 1: имя, единица, определение, «Активировать»,
 * «Архивировать», «Слить…».
 *
 * Пагинация — на фронтенде (спека §2.7): `GET /families` отдаёт весь список,
 * страницы режет клиент. Фильтр по статусу открывается на `draft`: после
 * seed это штатное первое состояние экрана — 42 черновика, путь «дописать
 * определение → активировать» проходит здесь (план фичи 1, задача 13,
 * «Утверждения»).
 */
export function FamiliesTab() {
  const [statusFilter, setStatusFilter] = useState<string>("draft");
  const [unitFilter, setUnitFilter] = useState<string>(ANY);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [merging, setMerging] = useState<WorkFamily | null>(null);
  const [archiving, setArchiving] = useState<WorkFamily | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.families.pageSize",
    DEFAULT_PAGE_SIZE
  );

  const unitsQ = useUnits();
  const familiesQ = useWorkFamilies(
    statusFilter === ANY ? undefined : (statusFilter as WorkFamilyStatus),
    unitFilter === ANY ? undefined : Number(unitFilter)
  );
  // Сводка над списком (сверка с макетом 27.09.2026, `mock-families.png`:
  // «активных N · черновиков M · в архиве K») — считается по ВСЕМУ списку
  // семей, независимо от текущего фильтра статуса/единицы (решение этой
  // задачи: иначе сводка на фильтре «черновики» показала бы «активных 0» и
  // из неё нельзя было бы понять состав каталога целиком). Отдельный запрос
  // БЕЗ фильтров — тот же `GET /families`, что несут `useWorkFamilies("active")`
  // у карточки контекста, кэш `react-query` не дублирует запрос повторно,
  // если он уже выполнялся с теми же (пустыми) параметрами.
  const allFamiliesQ = useWorkFamilies();
  const archive = useArchiveWorkFamily();

  const allItems = familiesQ.data ?? [];
  const total = allItems.length;

  // Зажим страницы (спека §2.7): фильтр или действие панели
  // (активация/архивирование) сузили выдачу, и текущая страница уже не
  // покрывается ею — правка состояния во время рендера, тем же приёмом, что
  // уже несёт `ContextsTab` для своей выдачи; следующий рендер пересчитывает
  // срез от исправленной страницы. Без стража на успех/ненулевой итог: при
  // `total === 0` (загрузка ещё не пришла или выдача пуста) `Math.ceil(0 /
  // pageSize)` и так даёт 0, а `Math.max(1, …)` поднимает его до 1 — та же
  // страница 1, что и в любом другом случае.
  const lastValidPage = Math.max(1, Math.ceil(total / pageSize));
  if (page > lastValidPage) {
    setPage(lastValidPage);
  }

  const startIndex = (page - 1) * pageSize;
  const items = allItems.slice(startIndex, startIndex + pageSize);
  const selected = allItems.find((f) => f.id === selectedId) ?? null;

  function resetToFirstPage() {
    setPage(1);
  }

  // Счётчик по статусам — «сводка над списком» мокапа, а НЕ производная от
  // `allItems`: та уже отфильтрована текущим статусом/единицей.
  const statusCounts = (allFamiliesQ.data ?? []).reduce(
    (acc, f) => {
      acc[f.status] += 1;
      return acc;
    },
    { draft: 0, active: 0, archived: 0 } as Record<WorkFamilyStatus, number>
  );

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
      <div className="grid gap-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <p className="self-end text-sm text-fg-tertiary tabular-nums">
              активных {statusCounts.active} · черновиков {statusCounts.draft} · в архиве{" "}
              {statusCounts.archived}
            </p>
            <div className="grid gap-1">
              <Label htmlFor="family-status-filter" className="text-xs text-fg-tertiary">Статус</Label>
              <Select
                value={statusFilter}
                onValueChange={(v) => { setStatusFilter(v ?? ANY); resetToFirstPage(); }}
              >
                <SelectTrigger id="family-status-filter" className="w-48">
                  <SelectValue>
                    {(raw) =>
                      !raw || raw === ANY ? "все" : FAMILY_STATUS_LABEL[raw as WorkFamilyStatus]
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>все</SelectItem>
                  {STATUS_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>{FAMILY_STATUS_LABEL[s]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="family-unit-filter" className="text-xs text-fg-tertiary">Единица (фильтр)</Label>
              <Select
                value={unitFilter}
                onValueChange={(v) => { setUnitFilter(v ?? ANY); resetToFirstPage(); }}
              >
                <SelectTrigger id="family-unit-filter" className="w-48">
                  <SelectValue>
                    {(raw) =>
                      !raw || raw === ANY
                        ? "Любая единица"
                        : (unitsQ.data?.find((u) => String(u.id) === raw)?.name ?? "Любая единица")
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>Любая единица</SelectItem>
                  {(unitsQ.data ?? []).map((u) => (
                    <SelectItem key={u.id} value={String(u.id)}>{u.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> Новая семья
          </Button>
        </div>

        {familiesQ.isPending && <Skeleton className="h-40 w-full" />}

        {familiesQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить семьи." />
        )}

        {familiesQ.isSuccess && total === 0 && (
          <EmptyState title="Семей нет" description="Семьи заводятся здесь либо загружаются seed-файлом." />
        )}

        {familiesQ.isSuccess && total > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs font-normal text-fg-tertiary">Название</TableHead>
                    <TableHead className="text-xs font-normal text-fg-tertiary">Единица</TableHead>
                    <TableHead className="text-xs font-normal text-fg-tertiary">Статус</TableHead>
                    <TableHead className="text-xs font-normal text-fg-tertiary">Определение</TableHead>
                    <TableHead className="text-right text-xs font-normal text-fg-tertiary">Контекстов</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((family) => (
                    <TableRow
                      key={family.id}
                      role="row"
                      tabIndex={0}
                      aria-selected={family.id === selectedId}
                      onClick={() => setSelectedId(family.id)}
                      onKeyDown={(e) => {
                        // Прежняя кнопка «Правка» (фича 1) открывала панель с
                        // клавиатуры; без неё строка обязана уметь то же сама
                        // (спека §2.1).
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setSelectedId(family.id);
                        }
                      }}
                      className={cn(
                        "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                        family.id === selectedId && "bg-surface-hover"
                      )}
                    >
                      <TableCell className="font-medium text-fg">{family.title}</TableCell>
                      <TableCell>{family.unit_symbol ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={FAMILY_STATUS_TINT[family.status]}>
                          {FAMILY_STATUS_LABEL[family.status]}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-xs">
                        {family.definition && family.definition.trim() ? (
                          <span className="block truncate text-fg-secondary" title={family.definition}>
                            {family.definition}
                          </span>
                        ) : (
                          <span className="text-destructive">нет определения</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{family.context_count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>

            <p className="text-sm text-fg-tertiary tabular-nums">
              {startIndex + 1}–{Math.min(startIndex + items.length, total)} из {total}
            </p>

            <Pager
              page={page}
              total={total}
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

      {selected ? (
        <FamilyPanel
          key={selected.id}
          family={selected}
          onMerge={() => setMerging(selected)}
          onArchive={() => setArchiving(selected)}
        />
      ) : (
        <EmptyState
          title="Семья не выбрана"
          description="Выберите строку в списке слева, чтобы открыть панель правки."
        />
      )}

      <CreateFamilyDialog open={createOpen} onOpenChange={setCreateOpen} />
      <MergeFamilyDialog family={merging} onOpenChange={(open) => !open && setMerging(null)} />

      <AlertDialog open={archiving !== null} onOpenChange={(open) => !open && setArchiving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Архивировать семью «{archiving?.title}»?</AlertDialogTitle>
            <AlertDialogDescription>
              {archiving && archiving.context_count > 0
                ? `У семьи есть привязанные контексты: ${archiving.context_count}. Сервер откажет — сначала нужно снять привязки.`
                : "Архивная семья перестаёт предлагаться для назначения контексту."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (archiving) archive.mutate(archiving.id);
                    setArchiving(null);
                  }}
                >
                  Архивировать
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * Панель правки выбранной семьи (спека §2.1) — заменяет диалог правки фичи 1
 * («как сейчас по составу действий»): имя/единица/определение те же поля,
 * что нёс `EditFamilyForm`, плюс «Активировать»/«Архивировать»/«Слить…» —
 * прежде кнопки строки списка, теперь кнопки панели. `key={family.id}` у
 * вызывающего (см. `FamiliesTab`) пересоздаёт компонент при смене выбранной
 * семьи — иначе несохранённый черновик полей пережил бы переключение.
 */
function FamilyPanel({
  family,
  onMerge,
  onArchive,
}: {
  family: WorkFamily;
  onMerge: () => void;
  onArchive: () => void;
}) {
  const [title, setTitle] = useState(family.title);
  const [unitName, setUnitName] = useState(family.unit_symbol ?? "");
  const [definition, setDefinition] = useState(family.definition ?? "");
  const update = useUpdateWorkFamily();
  const activate = useActivateWorkFamily();

  const unitLocked = family.context_count > 0;
  const hasDefinition = Boolean(family.definition && family.definition.trim());

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await update.mutateAsync({
        id: family.id,
        input: {
          title: title.trim(),
          definition: definition.trim() || null,
          // `unit_name` отсутствует в теле, пока привязки есть — «не трогать»,
          // а не «снять единицу» (решение оркестратора, план фичи 1, задача 13).
          ...(unitLocked ? {} : { unit_name: unitName.trim() || null }),
        },
      });
    } catch {
      // Причина в тосте.
    }
  }

  return (
    <Surface className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-medium text-fg">Семья «{family.title}»</h3>
        <Badge variant="outline" className={FAMILY_STATUS_TINT[family.status]}>
          {FAMILY_STATUS_LABEL[family.status]}
        </Badge>
      </div>

      <form onSubmit={handleSubmit} className="grid gap-3">
        <div className="grid gap-2">
          <Label htmlFor="edit-family-title">Название</Label>
          <Input id="edit-family-title" value={title} onChange={(e) => setTitle(e.target.value)} required />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="edit-family-unit">Единица</Label>
          <Input
            id="edit-family-unit"
            value={unitLocked ? (family.unit_symbol ?? "") : unitName}
            onChange={(e) => setUnitName(e.target.value)}
            disabled={unitLocked}
          />
          {unitLocked && (
            <p className="text-xs text-fg-tertiary">
              Единица недоступна: привязано контекстов — {family.context_count}
            </p>
          )}
        </div>
        <div className="grid gap-2">
          <Label htmlFor="edit-family-definition">Определение</Label>
          <Textarea id="edit-family-definition" value={definition} onChange={(e) => setDefinition(e.target.value)} />
        </div>
        {/* Одна строка (сверка с макетом 27.09.2026, `mock-families.png`). */}
        <div className="flex flex-wrap gap-2 border-t border-border-subtle pt-3">
          <Button type="submit" disabled={!title.trim() || update.isPending}>
            Сохранить
          </Button>
          {family.status === "draft" && (
            <Button
              variant="outline"
              aria-label={`Активировать семью ${family.title}`}
              disabled={!hasDefinition || activate.isPending}
              onClick={() => activate.mutate(family.id)}
            >
              Активировать
            </Button>
          )}
          {family.status === "active" && (
            <Button variant="outline" onClick={onMerge}>
              Слить
            </Button>
          )}
          {family.status !== "archived" && (
            <Button
              variant="ghost"
              // Видимый текст «В архив» — начало доступного имени (WCAG
              // 2.5.3 «метка в имени»), а не независимая от него подпись.
              aria-label={`В архив: семья ${family.title}`}
              onClick={onArchive}
            >
              В архив
            </Button>
          )}
        </div>
      </form>
      {/* Подсказка под кнопками (сверка с макетом 27.09.2026,
          `mock-families.png`) — ТОЛЬКО у черновика без определения, где
          «Активировать» и без того недоступна: подсказка называет причину,
          а не оставляет недоступную кнопку без объяснения. */}
      {family.status === "draft" && !hasDefinition && (
        <p className="text-xs text-fg-tertiary">Активировать можно только с определением.</p>
      )}
    </Surface>
  );
}

function CreateFamilyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [title, setTitle] = useState("");
  const [unitName, setUnitName] = useState("");
  const [definition, setDefinition] = useState("");
  const create = useCreateWorkFamily();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      await create.mutateAsync({
        title: title.trim(),
        unit_name: unitName.trim() || null,
        definition: definition.trim() || null,
      });
      setTitle("");
      setUnitName("");
      setDefinition("");
      onOpenChange(false);
    } catch {
      // Причина в тосте.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Новая семья работ</DialogTitle>
            <DialogDescription>
              Одна единица на семью (§1.14). Активация потребует определения.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-4">
            <div className="grid gap-2">
              <Label htmlFor="new-family-title">Название (обязательно)</Label>
              <Input id="new-family-title" value={title} onChange={(e) => setTitle(e.target.value)} required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="new-family-unit">Единица</Label>
              <Input id="new-family-unit" value={unitName} onChange={(e) => setUnitName(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="new-family-definition">Определение</Label>
              <Textarea id="new-family-definition" value={definition} onChange={(e) => setDefinition(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!title.trim() || create.isPending}>
              Создать
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function MergeFamilyDialog({
  family,
  onOpenChange,
}: {
  family: WorkFamily | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [targetId, setTargetId] = useState<number | null>(null);
  const activeFamilies = useWorkFamilies("active");
  const merge = useMergeWorkFamilies();

  const candidates = (activeFamilies.data ?? []).filter((f) => f.id !== family?.id);

  async function handleMerge() {
    if (!family || targetId === null) return;
    try {
      await merge.mutateAsync({ id: family.id, targetFamilyId: targetId });
      setTargetId(null);
      onOpenChange(false);
    } catch {
      // Причина в тосте.
    }
  }

  return (
    <Dialog open={family !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Слить семью «{family?.title}»</DialogTitle>
          <DialogDescription>
            Контексты источника переезжают в целевую семью; источник архивируется.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 py-4">
          <Label htmlFor="merge-target-family">Целевая семья</Label>
          <EntitySelect
            id="merge-target-family"
            items={candidates}
            value={targetId}
            onChange={(v) => setTargetId(v as number | null)}
            getLabel={(f) => f.title}
            placeholder="Выбрать семью"
          />
        </div>
        <DialogFooter>
          <Button disabled={targetId === null || merge.isPending} onClick={handleMerge}>
            Слить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
