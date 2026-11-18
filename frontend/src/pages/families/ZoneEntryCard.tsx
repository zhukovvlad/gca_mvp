import { useIsMutating } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Button } from "@/components/ui/button";
import { RecalcOverlay } from "@/pages/tenders/our-price/RecalcOverlay";
import { SAVING_LABEL } from "@/pages/tenders/our-price/ourPriceCopy";
import {
  apiErrorStatus,
  useDecideChainZone,
  useSectionChain,
  zoneDecisionMutationKey,
} from "@/services/queries";

import {
  ZONE_ENTRY_ASK,
  ZONE_ENTRY_BACK,
  ZONE_ENTRY_NOT_FOUND,
  ZONE_ENTRY_TITLE,
  ZONE_SOURCE_LABEL,
  modelLine,
} from "./labels";
import { ListSkeleton } from "./pending";
import { ZoneToggle } from "./ZonesQueue";

/**
 * Карточка входа по ссылке «ждёт решения администратора» (`?chain=<id>`): одна цепочка разделов
 * над очередью «Зоны», независимо от фильтра, поиска и страницы очереди. Решение — то же
 * `PUT …/zone`, что в очереди, и блокировка с ней общая (`zoneDecisionMutationKey`).
 */
export function ZoneEntryCard({ chainId }: { chainId: number }) {
  const query = useSectionChain(chainId);
  const decide = useDecideChainZone();
  const busy = useIsMutating({ mutationKey: zoneDecisionMutationKey }) > 0;
  const navigate = useNavigate();
  // `default` — первая запись истории: назад идти некуда (страницу открыли напрямую).
  const canGoBack = useLocation().key !== "default";

  if (query.isPending) return <ListSkeleton />;
  if (query.isError) {
    if (apiErrorStatus(query.error) === 404) {
      return (
        <div
          data-testid="zone-entry-not-found"
          className="rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
        >
          {ZONE_ENTRY_NOT_FOUND}
        </div>
      );
    }
    return <EmptyState title="Ошибка загрузки" description="Не удалось получить цепочку разделов." />;
  }

  const chain = query.data;
  return (
    <section
      data-testid="zone-entry-card"
      className="grid gap-2 rounded-[10px] border border-accent-border bg-accent-soft px-4 py-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-sm font-semibold text-fg">{ZONE_ENTRY_TITLE}</h3>
        {canGoBack && (
          <Button variant="link" size="sm" className="h-auto p-0" onClick={() => navigate(-1)}>
            {ZONE_ENTRY_BACK}
          </Button>
        )}
      </div>
      <p className="text-[13px] text-fg-secondary">{ZONE_ENTRY_ASK}</p>
      <RecalcOverlay active={busy} label={SAVING_LABEL} placement="center">
        {/* Не ниже плашки «Сохраняем…» (~44 px): у короткой цепочки она не выходит за блок. */}
        <div className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-start gap-4">
          <div className="grid min-w-0 gap-1">
            <div className="font-medium text-fg">{chain.titles.join(" / ")}</div>
            <div className="text-xs text-fg-tertiary">
              {`КП: ${chain.rows_kp} строк · договоры: ${chain.rows_contracts}`}
              {chain.sample_titles.length > 0 &&
                ` · например: ${chain.sample_titles.map((title) => `«${title}»`).join(", ")}`}
            </div>
            {chain.zone_source !== "human" && (
              <div className="text-[13px] text-fg-secondary">{modelLine(chain)}</div>
            )}
            {chain.zone !== null && chain.zone_source !== null && (
              <div className="text-xs text-fg-tertiary">{`зона поставлена: ${ZONE_SOURCE_LABEL[chain.zone_source]}`}</div>
            )}
          </div>
          <ZoneToggle
            chain={chain}
            busy={busy}
            onDecide={(zone) => decide.mutate({ chainId: chain.id, zone })}
          />
        </div>
      </RecalcOverlay>
    </section>
  );
}
