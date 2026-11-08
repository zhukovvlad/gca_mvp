import { useEffect, useRef, useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatConfidence } from "@/lib/format";
import { RecalcOverlay } from "@/pages/tenders/our-price/RecalcOverlay";
import { SAVING_LABEL } from "@/pages/tenders/our-price/ourPriceCopy";
import { useDecideChainZone, useSectionChains } from "@/services/queries";
import type {
  SectionChainFilter,
  SectionChainRow,
  SectionZone,
} from "@/types/domain";

import {
  SECTION_ZONES,
  ZONE_FILTER_LABEL,
  ZONE_LABEL,
  ZONE_SOURCE_LABEL,
} from "./labels";
import { FetchingBar, ListSkeleton } from "./pending";

/** Размер страницы очереди задаёт сервер (`section_chains.PAGE_SIZE`). */
const PAGE_SIZE = 50;
const FILTERS: SectionChainFilter[] = [
  "disputed",
  "undetermined",
  "rule",
  "model",
  "human",
  "all",
];
const GRID = "grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4 px-4 py-3";
const LIVE_JOB = new Set(["pending", "running"]);

/** Что модель ответила по цепочке: зона с уверенностью и причиной либо «не знаю». */
function modelLine(chain: SectionChainRow): string {
  const reason = chain.proposed_reason ? ` — «${chain.proposed_reason}»` : "";
  if (chain.proposed_zone === null) {
    if (chain.job_status !== null && LIVE_JOB.has(chain.job_status))
      return "Модель: ждёт ответа";
    return `Модель: не знаю${reason}`;
  }
  const zone = ZONE_LABEL[chain.proposed_zone].toLowerCase();
  return `Модель: ${zone} · уверенность ${formatConfidence(chain.proposed_confidence)}${reason}`;
}

function ZoneRow({
  chain,
  highlighted,
  busy,
  onDecide,
}: {
  chain: SectionChainRow;
  highlighted: boolean;
  busy: boolean;
  onDecide: (zone: SectionZone) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView?.({ block: "center" });
  }, [highlighted]);
  return (
    <div
      ref={ref}
      data-testid="zone-row"
      data-highlighted={highlighted ? "true" : undefined}
      className={`${GRID} border-b border-border-subtle last:border-b-0 ${
        highlighted ? "bg-accent-soft ring-2 ring-inset ring-accent-border" : ""
      }`}
    >
      <div className="grid min-w-0 gap-1">
        <div className="font-medium text-fg">{chain.titles.join(" / ")}</div>
        <div className="text-xs text-fg-tertiary">
          {`КП: ${chain.rows_kp} строк · договоры: ${chain.rows_contracts}`}
          {chain.sample_titles.length > 0 &&
            ` · например: ${chain.sample_titles.map((title) => `«${title}»`).join(", ")}`}
        </div>
        <div className="text-[13px] text-fg-secondary">{modelLine(chain)}</div>
        {chain.zone !== null && chain.zone_source !== null && (
          <div className="text-xs text-fg-tertiary">{`зона поставлена: ${ZONE_SOURCE_LABEL[chain.zone_source]}`}</div>
        )}
      </div>
      <ToggleGroup
        variant="outline"
        size="sm"
        spacing={0}
        aria-label="Зона цепочки"
        disabled={busy}
        value={chain.zone === null ? [] : [chain.zone]}
        onValueChange={(value) => {
          const next = value[0];
          if (next !== undefined) onDecide(next as SectionZone);
        }}
      >
        {SECTION_ZONES.map((zone) => (
          <ToggleGroupItem key={zone} value={zone}>
            {ZONE_LABEL[zone]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

interface ZonesQueueProps {
  /** Цепочка, к которой привела ссылка (`?chain=`): строка подсвечивается. */
  highlightChainId?: number | null;
}

/**
 * Очередь «Зоны» (спека лестницы аналогов §2.17, §2.20): цепочки разделов, фильтр (по умолчанию
 * спорные), поиск по ключу цепочки и страницы сервера. Решение ставится на цепочку и действует на
 * все сметы, где она встречается; зону от правила или модели можно поправить, открыв их фильтром.
 */
export function ZonesQueue({ highlightChainId = null }: ZonesQueueProps) {
  const [filter, setFilter] = useState<SectionChainFilter>(
    highlightChainId === null ? "disputed" : "all",
  );
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const query = useSectionChains({ filter, q: search, page });
  const decide = useDecideChainZone();

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const lastValidPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // Очередь сузилась решением, и текущей страницы больше нет.
  if (query.data && !query.isPlaceholderData && page > lastValidPage)
    setPage(lastValidPage);

  const highlightMissing =
    highlightChainId !== null &&
    query.data !== undefined &&
    !query.isPlaceholderData &&
    !items.some((c) => c.id === highlightChainId);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2 text-sm text-fg-secondary">
        <div className="flex items-center gap-2">
          <Label htmlFor="zones-filter" className="text-sm font-normal">
            Показать
          </Label>
          <Select
            value={filter}
            onValueChange={(v) => {
              setFilter((v ?? "disputed") as SectionChainFilter);
              setPage(1);
            }}
          >
            <SelectTrigger id="zones-filter" className="w-44">
              <SelectValue>
                {(raw) =>
                  ZONE_FILTER_LABEL[(raw ?? filter) as SectionChainFilter]
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {FILTERS.map((f) => (
                <SelectItem key={f} value={f}>
                  {ZONE_FILTER_LABEL[f]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Input
          type="search"
          aria-label="Поиск по разделам"
          placeholder="поиск по разделам…"
          className="w-64"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        {query.data && (
          <span className="text-xs text-fg-tertiary">{`${total} цепочек`}</span>
        )}
      </div>

      <FetchingBar active={query.isFetching && !query.isPending} />

      {query.isPending && <ListSkeleton />}
      {query.isError && (
        <EmptyState
          title="Ошибка загрузки"
          description="Не удалось получить очередь «Зоны»."
        />
      )}

      {highlightMissing && (
        <div
          data-testid="zone-not-found"
          className="rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
        >
          Цепочки разделов нет на этой странице очереди. Уточните фильтр или
          поиск.
        </div>
      )}

      {query.data && items.length === 0 && (
        <EmptyState
          title="Цепочек нет"
          description="В этой очереди при текущем фильтре пусто."
        />
      )}

      {items.length > 0 && (
        <RecalcOverlay active={decide.isPending} label={SAVING_LABEL}>
          <div
            data-testid="zones-queue"
            className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
          >
            {items.map((chain) => (
              <ZoneRow
                key={chain.id}
                chain={chain}
                highlighted={chain.id === highlightChainId}
                busy={decide.isPending}
                onDecide={(zone) => decide.mutate({ chainId: chain.id, zone })}
              />
            ))}
          </div>
        </RecalcOverlay>
      )}

      <Pager
        page={page}
        total={total}
        pageSize={PAGE_SIZE}
        onPageChange={setPage}
        showPageNumbers
      />
    </div>
  );
}
