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
import { useQueueStatus, useSuggestions, useUnits } from "@/services/queries";
import type {
  PreviewTarget,
  SuggestionBand,
  SuggestionsParams,
  SuggestionUnitFilter,
} from "@/types/domain";

import { BAND_LABEL } from "./labels";
import { PreviewDialog } from "./PreviewDialog";
import { SuggestionGroups } from "./SuggestionGroups";
import { SuggestionsHeader } from "./SuggestionsHeader";
import { usePersistedPageSize } from "./usePersistedPageSize";

const ANY = "any";
const NO_UNIT = "none";
const DEFAULT_PAGE_SIZE = 10;
const BAND_OPTIONS: SuggestionBand[] = ["high", "mid", "low"];

/**
 * Вкладка «Предложения» (спека semantic-suggestions §2.12): шапка со сводкой
 * очереди заданий и очередь «Семья из списка» — группы «семья + полоса
 * уверенности», фильтры единицы и полосы, «только многовладельческие».
 * Фильтры и подтверждения выполняет сервер; страницы — по группам.
 */
export function SuggestionsTab() {
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
  const queueQ = useSuggestions(params);

  const groups = queueQ.data?.groups ?? [];
  const rowsTotal = groups.reduce((sum, g) => sum + g.total, 0);

  // Зажим страницы (тот же приём, что у ContextsTab): очередь сузилась решением
  // или фильтром, и текущая страница больше не покрывается ею.
  if (queueQ.data) {
    const lastValidPage = Math.max(1, Math.ceil(groups.length / pageSize));
    if (page > lastValidPage) setPage(lastValidPage);
  }
  const pageGroups = groups.slice((page - 1) * pageSize, page * pageSize);

  function unitLabel(code: string | null): string {
    if (code === null) return "без единицы";
    return unitsQ.data?.find((u) => u.code === code)?.symbol ?? code;
  }

  function resetToFirstPage() {
    setPage(1);
  }

  return (
    <div className="grid gap-4">
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
        {/* Одна очередь на этой поставке: «Новая» и «Ошибки» добавляются рядом
            в тот же переключатель. */}
        <Tabs value="list">
          <TabsList className="h-auto rounded-lg border border-border bg-surface p-0">
            <TabsTrigger
              value="list"
              className="rounded-lg px-4 py-2 text-sm data-active:bg-action data-active:font-medium data-active:text-action-text"
            >
              Семья из списка
              <span className="ml-1 opacity-60">{rowsTotal}</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2 text-sm text-fg-secondary">
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
        </div>
      </div>

      {queueQ.isPending && <Skeleton className="h-40 w-full" />}

      {queueQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить очередь предложений." />
      )}

      {queueQ.data && groups.length === 0 && (
        <EmptyState
          title="Предложений нет"
          description="В этой очереди при текущих фильтрах пусто."
        />
      )}

      {queueQ.data && groups.length > 0 && (
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

      <PreviewDialog
        target={preview?.target ?? null}
        unitLabel={preview?.unitLabel}
        onClose={() => setPreview(null)}
      />
    </div>
  );
}
