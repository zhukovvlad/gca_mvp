import { useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatConfidence } from "@/lib/format";
import { RecalcOverlay } from "@/pages/tenders/our-price/RecalcOverlay";
import { SAVING_LABEL } from "@/pages/tenders/our-price/ourPriceCopy";
import {
  useDecideReleasable,
  useReleasableProposals,
} from "@/services/queries";
import type { ReleasableProposalRow } from "@/types/domain";

import { releasableLabel } from "./labels";
import { FetchingBar, ListSkeleton } from "./pending";

/** Размер страницы очереди задаёт сервер (`releasable.PAGE_SIZE`). */
const PAGE_SIZE = 50;

interface FamilyGroup {
  familyId: number;
  title: string;
  rows: ReleasableProposalRow[];
}

/** Плоский ответ страницы раскладывается по семьям с сохранением порядка сервера. */
function groupByFamily(rows: ReleasableProposalRow[]): FamilyGroup[] {
  const groups = new Map<number, FamilyGroup>();
  for (const row of rows) {
    const group = groups.get(row.family.id) ?? {
      familyId: row.family.id,
      title: row.family.title,
      rows: [],
    };
    group.rows.push(row);
    groups.set(row.family.id, group);
  }
  return [...groups.values()];
}

function ParameterRow({
  row,
  busy,
  onDecide,
}: {
  row: ReleasableProposalRow;
  busy: boolean;
  onDecide: (releasable: boolean) => void;
}) {
  const reason = row.reason ? ` — «${row.reason}»` : "";
  return (
    <div
      data-testid="releasable-row"
      className="grid grid-cols-[180px_minmax(0,1fr)_auto] items-start gap-4 border-t border-border-subtle px-4 py-3"
    >
      <div className="grid gap-0.5">
        <span className="font-medium text-fg">{row.parameter.name}</span>
        <span className="text-xs text-fg-tertiary">главный</span>
      </div>
      <div className="grid min-w-0 gap-1.5">
        <div className="flex flex-wrap gap-1.5">
          {row.values.map((value) => (
            <Badge key={value} variant="outline">
              {value}
            </Badge>
          ))}
        </div>
        <div className="text-[13px] text-fg-secondary">
          {`Модель: ${releasableLabel(row.releasable)} · ${formatConfidence(row.confidence)}${reason}`}
        </div>
      </div>
      <div className="flex gap-1.5">
        <Button
          type="button"
          size="sm"
          variant={row.releasable ? "secondary" : "outline"}
          disabled={busy}
          onClick={() => onDecide(true)}
        >
          Можно отпустить
        </Button>
        <Button
          type="button"
          size="sm"
          variant={row.releasable ? "outline" : "secondary"}
          disabled={busy}
          onClick={() => onDecide(false)}
        >
          Обязателен
        </Button>
      </div>
    </div>
  );
}

/**
 * Очередь «Можно отпустить» (спека лестницы аналогов §2.6, §2.7, §2.20): сомнительные ответы
 * модели по главным параметрам, по семьям. Решение уходит с `request_hash` предложения: если
 * семью или её схему правили после вопроса, сервер отвечает `409 proposal_stale`, а хук показывает
 * тост и перечитывает очередь.
 */
export function ReleasableQueue() {
  const [page, setPage] = useState(1);
  const query = useReleasableProposals(page);
  const decide = useDecideReleasable();

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const lastValidPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (query.data && !query.isPlaceholderData && page > lastValidPage)
    setPage(lastValidPage);

  return (
    <div className="grid gap-3">
      <FetchingBar active={query.isFetching && !query.isPending} />

      {query.isPending && <ListSkeleton />}
      {query.isError && (
        <EmptyState
          title="Ошибка загрузки"
          description="Не удалось получить очередь «Можно отпустить»."
        />
      )}
      {query.data && items.length === 0 && (
        <EmptyState
          title="Предложений нет"
          description="Сомнительных ответов модели по главным параметрам сейчас нет."
        />
      )}

      <RecalcOverlay active={decide.isPending} label={SAVING_LABEL}>
        <div className="grid gap-3">
          {groupByFamily(items).map((group) => (
            <section
              key={group.familyId}
              data-testid="releasable-family"
              aria-label={group.title}
              className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
            >
              <h4 className="bg-surface-hover px-4 py-2 text-sm font-medium text-fg">
                {group.title}
              </h4>
              {group.rows.map((row) => (
                <ParameterRow
                  key={row.parameter.id}
                  row={row}
                  busy={decide.isPending}
                  onDecide={(releasable) =>
                    decide.mutate({
                      familyId: row.family.id,
                      parameterId: row.parameter.id,
                      releasable,
                      requestHash: row.request_hash,
                    })
                  }
                />
              ))}
            </section>
          ))}
        </div>
      </RecalcOverlay>

      <Pager
        page={page}
        total={total}
        pageSize={PAGE_SIZE}
        onPageChange={setPage}
        showPageNumbers
      />
      <p className="text-[13px] text-fg-tertiary">
        Пока параметр не отмечен «можно отпустить», он обязателен: лучше «не с
        чем сравнить», чем плита против стен.
      </p>
    </div>
  );
}
