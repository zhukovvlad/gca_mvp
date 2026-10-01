import { useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { Button } from "@/components/ui/button";
import { useRetryJob } from "@/services/queries";
import type { JobRow, JobsResponse } from "@/types/domain";

import { jobErrorClassLabel } from "./labels";
import { PrivacyHoldBlock } from "./PrivacyHoldBlock";
import { usePersistedPageSize } from "./usePersistedPageSize";

const DEFAULT_PAGE_SIZE = 10;
const GRID = "grid grid-cols-[minmax(0,1fr)_210px_280px_150px] items-start gap-3 px-4 py-3";

interface ErrorsQueueProps {
  errors: JobRow[];
  hold: JobsResponse | undefined;
  unitLabel: (code: string | null) => string;
  onOpenFamily?: (familyId: number) => void;
}

function attemptsText(job: JobRow): string {
  const base = `попыток в этом поколении: ${job.attempts_in_generation}`;
  return job.retry_generation > 0 ? `${base}, ручных повторов: ${job.retry_generation}` : base;
}

function ErrorRow({
  job,
  unitLabel,
}: {
  job: JobRow;
  unitLabel: (code: string | null) => string;
}) {
  const retry = useRetryJob();
  return (
    <div data-testid="error-job" className={`${GRID} border-b border-border-subtle last:border-b-0`}>
      <div className="min-w-0 text-fg">
        {job.title} · {unitLabel(job.unit_code)}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-fg">{jobErrorClassLabel(job.last_error_class)}</span>
        {job.error_text && (
          <span className="line-clamp-2 text-xs text-fg-tertiary" title={job.error_text}>
            {job.error_text}
          </span>
        )}
      </div>
      <div className="text-fg-secondary">{attemptsText(job)}</div>
      <div>
        <Button
          variant="outline"
          size="sm"
          disabled={retry.isPending}
          onClick={() => retry.mutate(job.job_id)}
        >
          Повторить
        </Button>
      </div>
    </div>
  );
}

/**
 * Очередь «Ошибки» (спека semantic-suggestions §2.6, §2.12): задания в `error` с
 * «Повторить» и блок «Задержано проверкой». Повтор — то же задание с новым
 * поколением попыток; история прежних попыток сохраняется.
 */
export function ErrorsQueue({ errors, hold, unitLabel, onOpenFamily }: ErrorsQueueProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.suggestions.errors.pageSize",
    DEFAULT_PAGE_SIZE
  );
  const safePage = Math.min(page, Math.max(1, Math.ceil(errors.length / pageSize)));
  if (safePage !== page) setPage(safePage);
  const pageJobs = errors.slice((safePage - 1) * pageSize, safePage * pageSize);

  return (
    <div className="grid gap-5">
      <div
        data-testid="errors-queue"
        className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
      >
        <div
          className={`${GRID} border-b border-border-subtle bg-surface-hover text-xs font-medium text-fg-tertiary`}
        >
          <div>Контекст</div>
          <div>Класс ошибки</div>
          <div>Состояние</div>
          <div />
        </div>
        {pageJobs.map((job) => (
          <ErrorRow key={job.job_id} job={job} unitLabel={unitLabel} />
        ))}
        {errors.length === 0 && (
          <div className="px-4 py-6 text-center text-sm text-fg-tertiary">Ошибок нет.</div>
        )}
      </div>
      {errors.length > 0 && (
        <Pager
          page={safePage}
          total={errors.length}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(n) => {
            setPageSize(n);
            setPage(1);
          }}
          showPageNumbers
        />
      )}
      <PrivacyHoldBlock
        items={hold?.items ?? []}
        unitGroups={hold?.unit_groups ?? []}
        unitLabel={unitLabel}
        onOpenFamily={onOpenFamily}
      />
    </div>
  );
}
