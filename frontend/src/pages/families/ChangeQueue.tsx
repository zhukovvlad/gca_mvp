import { useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import type { ChangeGroup } from "@/types/domain";

import { SuggestionGroups } from "./SuggestionGroups";
import { usePersistedPageSize } from "./usePersistedPageSize";

const DEFAULT_PAGE_SIZE = 10;

interface ChangeQueueProps {
  groups: ChangeGroup[];
  /** Код единицы (`M2`) → символ (`м²`); неизвестный код печатается как есть. */
  unitLabel: (code: string | null) => string;
}

/**
 * Четвёртая очередь «Смена семьи» (спека `2026-10-02-catalog-variants-design.md` §2.12):
 * опубликованные предложения другой семьи контексту, у которого семья уже есть, а правило
 * публикации его не применило. Группы «прежняя семья → предложенная + полоса уверенности»;
 * «Подтвердить» ставит ожидание (или назначает сразу, если варианта нет), «Отклонить» — как в
 * очереди «Семья из списка». Страницы — по группам.
 */
export function ChangeQueue({ groups, unitLabel }: ChangeQueueProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.suggestions.pageSize",
    DEFAULT_PAGE_SIZE
  );

  if (groups.length === 0) {
    return (
      <EmptyState
        title="Смен семьи нет"
        description="Предложений другой семьи для контекстов с семьёй при текущих фильтрах нет."
      />
    );
  }

  // Зажим страницы: очередь сузилась решением или фильтром, и текущая страница её больше не покрывает.
  const lastValidPage = Math.max(1, Math.ceil(groups.length / pageSize));
  if (page > lastValidPage) setPage(lastValidPage);
  const pageGroups = groups.slice((page - 1) * pageSize, page * pageSize);

  return (
    <>
      <SuggestionGroups groups={pageGroups} unitLabel={unitLabel} mode="change" />
      <Pager
        page={Math.min(page, lastValidPage)}
        total={groups.length}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(n) => {
          setPageSize(n);
          setPage(1);
        }}
        showPageNumbers
      />
    </>
  );
}
