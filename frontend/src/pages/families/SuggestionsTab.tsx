import { useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useChangeQueue, useJobs, useQueueStatus, useSuggestions, useUnits } from "@/services/queries";
import type {
  PreviewTarget,
  SuggestionBand,
  SuggestionsParams,
  SuggestionUnitFilter,
} from "@/types/domain";

import { ChangeQueue } from "./ChangeQueue";
import { ErrorsQueue } from "./ErrorsQueue";
import { BAND_LABEL } from "./labels";
import { NewQueue } from "./NewQueue";
import { PreviewDialog } from "./PreviewDialog";
import { SuggestionGroups } from "./SuggestionGroups";
import { SuggestionsHeader } from "./SuggestionsHeader";
import { usePersistedPageSize } from "./usePersistedPageSize";

const ANY = "any";
const NO_UNIT = "none";
const DEFAULT_PAGE_SIZE = 10;
const BAND_OPTIONS: SuggestionBand[] = ["high", "mid", "low"];
type QueueTab = "list" | "change" | "new" | "err";
const EMPTY_ROWS: never[] = [];

const TAB_CLASS =
  "rounded-lg px-4 py-2 text-sm data-active:bg-action data-active:font-medium data-active:text-action-text";

interface SuggestionsTabProps {
  /** Перейти к семье на вкладке «Семьи» (ссылки «Открыть семью»). */
  onOpenFamily?: (familyId: number) => void;
}

/**
 * Вкладка «Предложения» (спека semantic-suggestions §2.12): шапка со сводкой
 * очереди заданий и четыре очереди — «Семья из списка» (группы «семья + полоса
 * уверенности», фильтры единицы и полосы, «только многовладельческие»), «Смена
 * семьи» (те же фильтры; контексты уже с семьёй, спека вариантов §2.12), «Новая»
 * и «Ошибки». Фильтры и подтверждения выполняет сервер; страницы — по группам.
 * Единица фильтрует «Семью из списка», «Смену семьи» и «Новую»; у «Ошибок» её нет.
 */
export function SuggestionsTab({ onOpenFamily }: SuggestionsTabProps = {}) {
  const [queue, setQueue] = useState<QueueTab>("list");
  const [unitFilter, setUnitFilter] = useState<string>(ANY);
  const [bandFilter, setBandFilter] = useState<string>(ANY);
  const [multiOwner, setMultiOwner] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.suggestions.pageSize",
    DEFAULT_PAGE_SIZE
  );
  const [preview, setPreview] = useState<{ target: PreviewTarget; unitLabel: string } | null>(null);

  const unitsQ = useUnits();
  const statusQ = useQueueStatus();

  const unitParam: SuggestionUnitFilter | undefined =
    unitFilter === ANY ? undefined : unitFilter === NO_UNIT ? NO_UNIT : Number(unitFilter);
  const params: SuggestionsParams = {
    queue: "list",
    unit: unitParam,
    band: bandFilter === ANY ? undefined : (bandFilter as SuggestionBand),
    multi_owner: multiOwner,
  };
  const queueQ = useSuggestions(params, { poll: queue === "list" });
  // Счётчики вкладок берутся из тех же запросов, что и их содержимое, поэтому
  // все очереди читаются, пока открыта другая.
  const changeQ = useChangeQueue(
    { unit: unitParam, band: params.band, multi_owner: multiOwner },
    { poll: queue === "change" }
  );
  const changeRows = changeQ.groups.reduce((sum, g) => sum + g.total, 0);
  const newQ = useSuggestions({ queue: "new", unit: unitParam }, { poll: queue === "new" });
  const errorsQ = useJobs("error", { poll: queue === "err" });
  const holdQ = useJobs("privacy_hold", { poll: queue === "err" });

  const groups = queueQ.data?.groups ?? [];
  const rowsTotal = groups.reduce((sum, g) => sum + g.total, 0);

  // Зажим страницы (тот же приём, что у ContextsTab): очередь сузилась решением
  // или фильтром, и текущая страница больше не покрывается ею.
  if (queueQ.data) {
    const lastValidPage = Math.max(1, Math.ceil(groups.length / pageSize));
    if (page > lastValidPage) setPage(lastValidPage);
  }
  const newRows = newQ.data?.items ?? EMPTY_ROWS;
  const errorRows = errorsQ.data?.items ?? EMPTY_ROWS;
  const errorsTotal = errorRows.length + (holdQ.data?.items.length ?? 0);
  const pageGroups = groups.slice((page - 1) * pageSize, page * pageSize);

  function unitLabel(code: string | null): string {
    if (code === null) return "без единицы";
    return unitsQ.data?.find((u) => u.code === code)?.symbol ?? code;
  }

  // Опрашивается только открытая очередь; скрытая могла устареть, пока на неё не
  // смотрели, поэтому переключение перечитывает её сразу.
  function switchQueue(next: QueueTab) {
    setQueue(next);
    if (next === "list") void queueQ.refetch();
    else if (next === "change") void changeQ.refetch();
    else if (next === "new") void newQ.refetch();
    else {
      void errorsQ.refetch();
      void holdQ.refetch();
    }
  }

  function resetToFirstPage() {
    setPage(1);
  }

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {statusQ.data && (
        <SuggestionsHeader
          status={statusQ.data}
          onPreview={(target, label) => setPreview({ target, unitLabel: label ?? "" })}
        />
      )}
      {statusQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить сводку очереди." />
      )}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <Tabs value={queue} onValueChange={(v) => v && switchQueue(v as QueueTab)}>
          <TabsList className="h-auto rounded-lg border border-border bg-surface p-0">
            <TabsTrigger value="list" className={TAB_CLASS}>
              Семья из списка
              <span className="ml-1 opacity-60">{rowsTotal}</span>
            </TabsTrigger>
            <TabsTrigger value="change" className={TAB_CLASS}>
              Смена семьи
              <span className="ml-1 opacity-60">{changeRows}</span>
            </TabsTrigger>
            <TabsTrigger value="new" className={TAB_CLASS}>
              Новая
              <span className="ml-1 opacity-60">{newRows.length}</span>
            </TabsTrigger>
            <TabsTrigger value="err" className={TAB_CLASS}>
              Ошибки
              <span className="ml-1 opacity-60">{errorsTotal}</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2 text-sm text-fg-secondary">
          {queue !== "err" && (
            <div className="flex items-center gap-2">
              <Label htmlFor="suggestions-unit-filter" className="text-sm font-normal">
                Единица
              </Label>
              <Select
                value={unitFilter}
                onValueChange={(v) => {
                  setUnitFilter(v ?? ANY);
                  resetToFirstPage();
                }}
              >
                <SelectTrigger id="suggestions-unit-filter" className="w-36">
                  <SelectValue>
                    {(raw) =>
                      !raw || raw === ANY
                        ? "все"
                        : raw === NO_UNIT
                          ? "без единицы"
                          : (unitsQ.data?.find((u) => String(u.id) === raw)?.symbol ?? "все")
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>все</SelectItem>
                  <SelectItem value={NO_UNIT}>без единицы</SelectItem>
                  {(unitsQ.data ?? []).map((u) => (
                    <SelectItem key={u.id} value={String(u.id)}>
                      {u.symbol}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {(queue === "list" || queue === "change") && (
            <>
            <div className="flex items-center gap-2">
              <Label htmlFor="suggestions-band-filter" className="text-sm font-normal">
                Уверенность
              </Label>
              <Select
                value={bandFilter}
                onValueChange={(v) => {
                  setBandFilter(v ?? ANY);
                  resetToFirstPage();
                }}
              >
                <SelectTrigger id="suggestions-band-filter" className="w-36">
                  <SelectValue>
                    {(raw) =>
                      !raw || raw === ANY ? "все полосы" : BAND_LABEL[raw as SuggestionBand]
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>все полосы</SelectItem>
                  {BAND_OPTIONS.map((b) => (
                    <SelectItem key={b} value={b}>
                      {BAND_LABEL[b]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center gap-1.5">
              <Checkbox
                id="suggestions-multi-owner"
                checked={multiOwner}
                onCheckedChange={(checked) => {
                  setMultiOwner(checked === true);
                  resetToFirstPage();
                }}
              />
              <Label htmlFor="suggestions-multi-owner" className="text-sm font-normal">
                только многовладельческие
              </Label>
            </div>
            </>
          )}
        </div>
      </div>

      {queue === "list" && queueQ.isPending && <Skeleton className="h-40 w-full" />}

      {queue === "list" && queueQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить очередь предложений." />
      )}

      {queue === "list" && queueQ.data && groups.length === 0 && (
        <EmptyState
          title="Предложений нет"
          description="В этой очереди при текущих фильтрах пусто."
        />
      )}

      {queue === "list" && queueQ.data && groups.length > 0 && (
        <>
          <SuggestionGroups groups={pageGroups} unitLabel={unitLabel} />
          <Pager
            page={page}
            total={groups.length}
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

      {queue === "change" && changeQ.isPending && <Skeleton className="h-40 w-full" />}
      {queue === "change" && changeQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить очередь «Смена семьи»." />
      )}
      {queue === "change" && changeQ.data && <ChangeQueue groups={changeQ.groups} unitLabel={unitLabel} />}

      {queue === "new" && newQ.isPending && <Skeleton className="h-40 w-full" />}
      {queue === "new" && newQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить очередь «Новая»." />
      )}
      {queue === "new" && newQ.data && (
        <NewQueue rows={newRows} unitLabel={unitLabel} onOpenFamily={onOpenFamily} />
      )}

      {queue === "err" && (errorsQ.isPending || holdQ.isPending) && (
        <Skeleton className="h-40 w-full" />
      )}
      {queue === "err" && (errorsQ.isError || holdQ.isError) && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить задания очереди." />
      )}
      {queue === "err" && errorsQ.data && holdQ.data && (
        <ErrorsQueue
          errors={errorRows}
          hold={holdQ.data}
          unitLabel={unitLabel}
          onOpenFamily={onOpenFamily}
        />
      )}

      <PreviewDialog
        target={preview?.target ?? null}
        unitLabel={preview?.unitLabel}
        onClose={() => setPreview(null)}
      />
    </div>
  );
}
