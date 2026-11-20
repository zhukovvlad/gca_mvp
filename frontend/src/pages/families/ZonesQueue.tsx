import { useIsMutating } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

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
import { RecalcOverlay } from "@/pages/tenders/our-price/RecalcOverlay";
import { SAVING_LABEL } from "@/pages/tenders/our-price/ourPriceCopy";
import {
  useAcceptAllZones,
  useDecideChainZone,
  useSectionChains,
  zoneDecisionMutationKey,
} from "@/services/queries";
import type {
  SectionChainFilter,
  SectionChainRow,
  SectionZone,
} from "@/types/domain";

import { AcceptAllDialog } from "./AcceptAllDialog";
import {
  SECTION_ZONES,
  ZONE_FILTER_LABEL,
  ZONE_LABEL,
  ZONE_SOURCE_LABEL,
  ZONES_EXPLAIN,
  modelLine,
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

/**
 * Четыре кнопки зон цепочки: общие для строки очереди и карточки входа по ссылке, чтобы они не
 * разошлись. Нажатие на стоящую зону от правила или модели закрепляет её решением человека.
 */
export function ZoneToggle({
  chain,
  busy,
  onDecide,
}: {
  chain: Pick<SectionChainRow, "zone" | "zone_source">;
  busy: boolean;
  onDecide: (zone: SectionZone) => void;
}) {
  return (
    <ToggleGroup
      variant="outline"
      size="sm"
      spacing={0}
      aria-label="Зона цепочки"
      disabled={busy}
      value={chain.zone === null ? [] : [chain.zone]}
      onValueChange={(value) => {
        const next = value[0];
        if (next !== undefined) {
          onDecide(next as SectionZone);
          return;
        }
        // Нажали уже стоящую зону: переключатель её отжимает (пустой выбор). Зону от правила
        // или модели это закрепляет решением человека; решение человека не трогается.
        if (chain.zone !== null && chain.zone_source !== "human") onDecide(chain.zone);
      }}
    >
      {SECTION_ZONES.map((zone) => (
        <ToggleGroupItem
          key={zone}
          value={zone}
          // Нажатая зона заметна, как основная кнопка: штатный `bg-muted` в тёмной теме не виден.
          className="aria-pressed:bg-primary aria-pressed:text-primary-foreground"
        >
          {ZONE_LABEL[zone]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function ZoneRow({
  chain,
  busy,
  onDecide,
}: {
  chain: SectionChainRow;
  busy: boolean;
  onDecide: (zone: SectionZone) => void;
}) {
  return (
    <div
      data-testid="zone-row"
      className={`${GRID} border-b border-border-subtle last:border-b-0`}
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
      <ZoneToggle chain={chain} busy={busy} onDecide={onDecide} />
    </div>
  );
}

/**
 * Очередь «Зоны» (спека лестницы аналогов §2.17, §2.20): цепочки разделов, фильтр (по умолчанию
 * спорные, и при входе по ссылке тоже), поиск по ключу цепочки и страницы сервера. Решение
 * ставится на цепочку и действует на все сметы, где она встречается; зону от правила или модели
 * можно поправить, открыв их фильтром.
 *
 * `children` — карточка входа по ссылке: встаёт сразу после строки-пояснения, до фильтра (макет
 * дизайна входа по ссылке): сначала что такое зона, потом просьба её выбрать.
 */
export function ZonesQueue({ children }: { children?: ReactNode } = {}) {
  const [filter, setFilter] = useState<SectionChainFilter>("disputed");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const query = useSectionChains({ filter, q: search, page });
  const decide = useDecideChainZone();
  const acceptAll = useAcceptAllZones();
  // Одно сохранение на карточку входа и очередь: занято, пока идёт любое решение по зонам и
  // перечитывание (мутация держится до его конца), в обеих областях сразу.
  const busy = useIsMutating({ mutationKey: zoneDecisionMutationKey }) > 0;

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const acceptable = query.data?.acceptable_total ?? 0;
  const lastValidPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // Очередь сузилась решением, и текущей страницы больше нет.
  if (query.data && !query.isPlaceholderData && page > lastValidPage)
    setPage(lastValidPage);

  return (
    <div className="grid gap-3">
      <p data-testid="zones-explain" className="text-sm text-fg-secondary">
        {ZONES_EXPLAIN}
      </p>
      {children}
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

      {acceptable > 0 && (
        <AcceptAllDialog
          count={acceptable}
          disabled={busy}
          description={`Будут приняты ${acceptable} зон, предложенных моделью. Ответы «не знаю» останутся в очереди. Исправить потом можно в фильтре «Все» этой очереди.`}
          onConfirm={() => acceptAll.mutate()}
        />
      )}

      {query.isPending && <ListSkeleton />}
      {query.isError && (
        <EmptyState
          title="Ошибка загрузки"
          description="Не удалось получить очередь «Зоны»."
        />
      )}

      {query.data && items.length === 0 && (
        <EmptyState
          title="Цепочек нет"
          description="В этой очереди при текущем фильтре пусто."
        />
      )}

      {items.length > 0 && (
        <RecalcOverlay active={busy} label={SAVING_LABEL}>
          <div
            data-testid="zones-queue"
            className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
          >
            {items.map((chain) => (
              <ZoneRow
                key={chain.id}
                chain={chain}
                busy={busy}
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
