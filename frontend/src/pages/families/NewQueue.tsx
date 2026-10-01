import { useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Button } from "@/components/ui/button";
import { formatConfidence } from "@/lib/format";
import type { NewRow } from "@/types/domain";

import { CreateFamilyDialog } from "./CreateFamilyDialog";
import { SourceChip } from "./SourceChip";
import { usePersistedPageSize } from "./usePersistedPageSize";

const DEFAULT_PAGE_SIZE = 10;
const GRID = "grid grid-cols-[70px_280px_minmax(0,1fr)_70px_170px] items-start gap-3 px-4 py-3";

interface NewQueueProps {
  rows: NewRow[];
  /** Код единицы (`M2`) → символ (`м²`). */
  unitLabel: (code: string | null) => string;
  onOpenFamily?: (familyId: number) => void;
}

/** Строка контекста: наименование, статья и путь — как в очереди «Семья из списка». */
function RowTitle({ row }: { row: NewRow }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <div className="text-fg">{row.title}</div>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-fg-tertiary">
        {row.article && (
          <>
            <SourceChip kind="classifier" />
            <span>{row.article}</span>
          </>
        )}
        {row.path.length > 0 && (
          <>
            <SourceChip kind="estimate" />
            <span className="min-w-0 truncate" title={row.path.join(" / ")}>
              {row.path.join(" / ")}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

/** Что модель ответила в колонке «Имя от ИИ» (спека semantic-suggestions §2.12). */
function AiAnswer({ row }: { row: NewRow }) {
  if (row.suggestion_id === null) {
    return (
      <div className="text-[13px] text-fg-tertiary">
        в единице нет активных семей — ИИ не спрашивали
      </div>
    );
  }
  if (row.is_system) {
    return <div className="text-[13px] font-medium text-info-text">ИИ считает строку системой</div>;
  }
  return <div className="font-medium text-fg">{row.new_family_name}</div>;
}

/**
 * Очередь «Новая» (спека semantic-suggestions §2.12): опубликованные ответы «новая
 * семья» с именем от ИИ и «Завести семью…»; строки единиц без активных семей
 * модель не видела — для них семью заводят на вкладке «Семьи».
 */
export function NewQueue({ rows, unitLabel, onOpenFamily }: NewQueueProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.suggestions.new.pageSize",
    DEFAULT_PAGE_SIZE
  );
  const [creating, setCreating] = useState<(NewRow & { suggestion_id: number }) | null>(null);

  if (rows.length === 0) {
    return <EmptyState title="Пусто" description="В этой очереди при текущих фильтрах пусто." />;
  }

  const lastValidPage = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(page, lastValidPage);
  if (safePage !== page) setPage(safePage);
  const pageRows = rows.slice((safePage - 1) * pageSize, safePage * pageSize);

  return (
    <div className="grid gap-3">
      <div
        data-testid="new-queue"
        className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
      >
        <div
          className={`${GRID} border-b border-border-subtle bg-surface-hover text-xs font-medium text-fg-tertiary`}
        >
          <div>Единица</div>
          <div>Имя от ИИ</div>
          <div>Наименование строки</div>
          <div>Уверен.</div>
          <div />
        </div>
        {pageRows.map((row) => (
          <div
            key={row.context_id}
            data-testid="new-row"
            className={`${GRID} border-b border-border-subtle last:border-b-0 ${
              row.suggestion_id === null ? "bg-surface-hover" : ""
            }`}
          >
            <div className="text-fg-tertiary">{unitLabel(row.unit_code)}</div>
            <AiAnswer row={row} />
            <RowTitle row={row} />
            <div className="font-mono text-[13px] tabular-nums text-fg-secondary">
              {formatConfidence(row.confidence)}
            </div>
            <div>
              {row.suggestion_id !== null && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setCreating({ ...row, suggestion_id: row.suggestion_id as number })}
                >
                  Завести семью…
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>
      <Pager
        page={safePage}
        total={rows.length}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(n) => {
          setPageSize(n);
          setPage(1);
        }}
        showPageNumbers
      />
      <p className="text-[13px] text-fg-tertiary">
        Заведите семью из любой строки и перезапросите единицу — похожие строки придут в «Семья из
        списка» одной группой. Для единицы без активных семей семью заводят на вкладке «Семьи».
      </p>
      <CreateFamilyDialog
        row={creating}
        unitLabel={creating ? unitLabel(creating.unit_code) : ""}
        onClose={() => setCreating(null)}
        onOpenFamily={onOpenFamily}
      />
    </div>
  );
}
