import { useState, type FormEvent } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { apiErrorCode, REFERENCE_QUERY_MIN } from "@/services/queries";
import type { LineIds, ReferenceSearchResult, ReferenceSearchRow, ReferenceUnits } from "@/types/domain";
import { ZONE_LABEL } from "@/pages/families/labels";

import type { VatView } from "./money";
import {
  LUMP_SEARCH_MARK,
  LUMP_SEARCH_OVERRIDE,
  lumpCaption,
  MARK_KIND_LABEL,
  REF_SEARCH,
  refSearchFound,
  refTruncated,
} from "./ourPriceCopy";
import { ReferenceRows } from "./ReferenceRows";

/** Предел выдачи после свёртки (спека справочных аналогов §2.12): по нему `truncated` говорит «первые 50». */
export const SEARCH_LIMIT = 50;

export interface ReferenceSearchProps {
  /** Запрос, по которому идёт поиск: пуст, пока человек ничего не отправил или набрал меньше двух знаков. */
  committed: string;
  units: ReferenceUnits;
  onSearch: (text: string) => void;
  onUnits: (units: ReferenceUnits) => void;
  search: Pick<UseQueryResult<ReferenceSearchResult>, "data" | "isFetching" | "isError" | "error">;
  vat: VatView;
  kpUnit: string | null;
  canChoose: boolean;
  busy: boolean;
  onChoose: (row: ReferenceSearchRow) => void;
  /** Строка КП панели и её форма словами: для меню отметок у `admin` (спека «Не аналог» §8 п. 5). */
  ids: LineIds;
  formLabel: string;
}

/**
 * «Найти в договорах» (спека справочных аналогов §2.12, §2.18): поле, подсказка «ищите по корням слов»,
 * переключатель «все единицы / как у строки КП» и выдача. Запрос короче двух знаков не уходит на сервер.
 * У системы поля нет: сервер отвечает `422 reference_not_applicable`, и раздел не рисуется.
 */
export function ReferenceSearch({
  committed,
  units,
  onSearch,
  onUnits,
  search,
  vat,
  kpUnit,
  canChoose,
  busy,
  onChoose,
  ids,
  formLabel,
}: ReferenceSearchProps) {
  const [draft, setDraft] = useState(committed);
  const text = draft.trim();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (text.length >= REFERENCE_QUERY_MIN) onSearch(text);
  };
  const code = search.isError ? apiErrorCode(search.error) : undefined;
  if (code === "reference_not_applicable") return null;
  const data = search.data;
  return (
    <div data-testid="reference-search" className="space-y-2">
      <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
        <Input
          aria-label={REF_SEARCH.label}
          data-testid="reference-search-input"
          className="min-w-56 flex-1"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={text.length < REFERENCE_QUERY_MIN}>
          {REF_SEARCH.submit}
        </Button>
        <ToggleGroup
          variant="outline"
          size="sm"
          spacing={0}
          aria-label={REF_SEARCH.unitsLabel}
          value={[units]}
          onValueChange={(value) => {
            // Нажатая кнопка не снимается: единицы поиска всегда названы.
            if (value.length > 0) onUnits(value[0] as ReferenceUnits);
          }}
        >
          <ToggleGroupItem value="all" className="aria-pressed:bg-primary aria-pressed:text-primary-foreground">
            {REF_SEARCH.unitsAll}
          </ToggleGroupItem>
          <ToggleGroupItem value="kp" className="aria-pressed:bg-primary aria-pressed:text-primary-foreground">
            {REF_SEARCH.unitsKp}
          </ToggleGroupItem>
        </ToggleGroup>
      </form>
      <p className="text-xs text-fg-secondary">{REF_SEARCH.hint}</p>
      {search.isError && code === "search_query_invalid" && (
        <p role="alert" data-testid="reference-search-invalid" className="text-sm text-danger-text">
          {REF_SEARCH.invalid}
        </p>
      )}
      {search.isError && code !== "search_query_invalid" && (
        <p role="alert" data-testid="reference-search-failed" className="text-sm text-danger-text">
          {REF_SEARCH.failed}
        </p>
      )}
      {committed !== "" && search.isFetching && data === undefined && (
        <p className="text-sm text-fg-secondary">{REF_SEARCH.loading}</p>
      )}
      {committed !== "" && data !== undefined && !search.isError && (
        <div className="space-y-2">
          {data.rows.length === 0 ? (
            <p data-testid="reference-search-empty" className="text-sm text-fg-secondary">
              {REF_SEARCH.empty}
            </p>
          ) : (
            <>
              <p data-testid="reference-search-found" className="text-xs text-fg-secondary">
                {refSearchFound(data.truncated ? data.total_lines : data.rows.length)}
              </p>
              <ReferenceRows
                rows={data.rows}
                vat={vat}
                kpUnit={kpUnit}
                canChoose={canChoose}
                canLump={canChoose}
                markMenu={canChoose ? { ids, formLabel } : undefined}
                busy={busy}
                testPrefix="search"
                fold={false}
                extra={(row) => <SearchMarks row={row} />}
                onChoose={onChoose}
              />
            </>
          )}
          {data.truncated && (
            <p data-testid="reference-search-truncated" className="text-xs text-warning-text">
              {refTruncated(SEARCH_LIMIT)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Зона строки договора и вид предложенного пересчёта. */
function SearchMarks({ row }: { row: ReferenceSearchRow }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <Badge variant="outline" className="h-auto py-0.5">
        {row.zone === null ? REF_SEARCH.zoneUnknown : ZONE_LABEL[row.zone]}
      </Badge>
      {row.unit !== null && (
        <Badge variant="outline" className="h-auto py-0.5">
          {row.unit}
        </Badge>
      )}
      {row.conversion_kind === "link" && (
        <Badge variant="secondary" className="h-auto py-0.5">
          {REF_SEARCH.linkConversion}
        </Badge>
      )}
      {row.conversion_kind === "manual_needed" && (
        <Badge variant="secondary" className="h-auto py-0.5">
          {REF_SEARCH.manualNeeded}
        </Badge>
      )}
      {row.marks.map((mark) => (
        <Badge
          key={mark.kind}
          variant="secondary"
          data-testid="search-mark"
          title={mark.reason}
          className="h-auto whitespace-normal py-0.5 text-left"
        >
          {MARK_KIND_LABEL[mark.kind]}
        </Badge>
      ))}
      {row.lump?.is_lump === true && (
        <Badge
          variant="secondary"
          data-testid="search-lump-mark"
          title={lumpCaption(row.lump)}
          className="h-auto py-0.5"
        >
          {LUMP_SEARCH_MARK}
          {row.lump.override !== null && ` · ${LUMP_SEARCH_OVERRIDE}`}
        </Badge>
      )}
    </div>
  );
}
