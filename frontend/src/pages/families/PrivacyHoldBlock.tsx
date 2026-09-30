import { ShieldAlert } from "lucide-react";
import { useState } from "react";

import { Pager } from "@/components/domain/Pager";
import { Button } from "@/components/ui/button";
import {
  usePrivacyDecline,
  usePrivacyRelease,
  useUnitPrivacyRelease,
} from "@/services/queries";
import type { JobRow, PrivacyMatch, UnitHoldGroup } from "@/types/domain";

import { HighlightedText } from "./HighlightedText";
import { matchPlaceLabel, pluralRu } from "./labels";
import { usePersistedPageSize } from "./usePersistedPageSize";

const DEFAULT_PAGE_SIZE = 10;

/** Совпадение в строках семей или в тексте промпта: его несёт единица, а не строка контекста. */
function isUnitLevel(match: PrivacyMatch): boolean {
  return match.where === "prompt" || /^family:\d+$/.test(match.where);
}

/** Задание, уже описанное строкой единицы (все совпадения — уровня единицы). */
function isCoveredByUnitGroup(job: JobRow): boolean {
  const matches = job.matches ?? [];
  return matches.length > 0 && matches.every(isUnitLevel);
}

function quoted(matches: PrivacyMatch[]): string {
  return [...new Set(matches.map((m) => m.text))].map((t) => `„${t}“`).join(", ");
}

/** Ключ строки единицы: единица, место и весь набор совпадений (id семьи входит в `where`). */
function unitGroupKey(group: UnitHoldGroup): string {
  const set = group.matches.map((m) => JSON.stringify([m.text, m.kind, m.where])).sort();
  return JSON.stringify([group.unit_id, group.place, set]);
}

function heldCount(n: number): string {
  return `${pluralRu(n, "задержан", "задержано", "задержано")} ${n} ${pluralRu(n, "запрос", "запроса", "запросов")}`;
}

interface UnitGroupRowProps {
  group: UnitHoldGroup;
  unitLabel: (code: string | null) => string;
  onOpenFamily?: (familyId: number) => void;
}

/** Одна строка на единицу: совпадение в списке семей или в промпте — «Отправить все K». */
function UnitGroupRow({ group, unitLabel, onOpenFamily }: UnitGroupRowProps) {
  const release = useUnitPrivacyRelease();
  const unit = group.unit_code === null ? "без единицы" : `единицы ${unitLabel(group.unit_code)}`;
  const subject =
    group.place === "family"
      ? "Список семей"
      : group.place === "prompt"
        ? "Текст промпта"
        : "Список семей и текст промпта";
  const verb = group.place === "mixed" ? "содержат" : "содержит";
  const familyMark = group.family_id !== null ? ` (семья ${group.family_id})` : "";

  return (
    <div
      data-testid="unit-hold-group"
      className="flex items-center gap-3 border-b border-border-subtle px-4 py-3 last:border-b-0"
    >
      <div className="min-w-0 flex-1 text-[13px] text-fg">
        {subject} {unit} {verb} {quoted(group.matches)}
        {familyMark} — {heldCount(group.jobs_count)}
        {group.family_title && (
          <span className="ml-1.5 text-fg-tertiary">«{group.family_title}»</span>
        )}
      </div>
      <div className="flex flex-none gap-1.5">
        {group.family_id !== null && onOpenFamily && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenFamily(group.family_id as number)}
          >
            Открыть семью
          </Button>
        )}
        <Button
          size="sm"
          disabled={release.isPending}
          onClick={() => release.mutate({ unitId: group.unit_id, shown: group.matches })}
        >
          Отправить все {group.jobs_count}
        </Button>
      </div>
    </div>
  );
}

/** Задержанное задание со строкой контекста: слова словаря подсвечены, у каждого подписано место. */
function HeldJobRow({
  job,
  unitLabel,
}: {
  job: JobRow;
  unitLabel: (code: string | null) => string;
}) {
  const release = usePrivacyRelease();
  const decline = usePrivacyDecline();
  const shown = job.matches ?? [];
  const contextNeedles = shown.filter((m) => m.where === "context").map((m) => m.text);
  const pending = release.isPending || decline.isPending;

  return (
    <div
      data-testid="held-job"
      className="flex items-start gap-4 border-b border-border-subtle px-4 py-3 last:border-b-0"
    >
      <div className="w-14 flex-none pt-px text-fg-tertiary">{unitLabel(job.unit_code)}</div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="text-fg">
          <HighlightedText text={job.title} needles={contextNeedles} />
        </div>
        <ul className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-fg-secondary">
          {shown.map((match, index) => (
            <li key={index} data-testid="held-match">
              «{match.text}» — {matchPlaceLabel(match.where)}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-none gap-1.5">
        <Button
          size="sm"
          disabled={pending}
          onClick={() => release.mutate({ jobId: job.job_id, shown })}
        >
          Отправить
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => decline.mutate({ jobId: job.job_id, shown })}
        >
          Не отправлять
        </Button>
      </div>
    </div>
  );
}

interface PrivacyHoldBlockProps {
  items: JobRow[];
  unitGroups: UnitHoldGroup[];
  unitLabel: (code: string | null) => string;
  onOpenFamily?: (familyId: number) => void;
}

/**
 * «Задержано проверкой» (спека semantic-suggestions §2.10, §2.12): запросы, в теле
 * которых найдены слова словаря (объекты, подрядчики, договоры). Совпадения в списке
 * семей и в промпте — одной строкой на единицу; остальные — по строке контекста.
 * Решение шлёт ровно тот набор совпадений, что показан.
 */
export function PrivacyHoldBlock({
  items,
  unitGroups,
  unitLabel,
  onOpenFamily,
}: PrivacyHoldBlockProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize(
    "gca.families.suggestions.hold.pageSize",
    DEFAULT_PAGE_SIZE
  );
  const own = items.filter((job) => !isCoveredByUnitGroup(job));
  const safePage = Math.min(page, Math.max(1, Math.ceil(own.length / pageSize)));
  if (safePage !== page) setPage(safePage);
  if (own.length === 0 && unitGroups.length === 0) return null;

  const pageJobs = own.slice((safePage - 1) * pageSize, safePage * pageSize);

  return (
    <section data-testid="privacy-hold" className="grid gap-2">
      <div className="flex items-center gap-2 text-[15px] font-semibold text-fg">
        <ShieldAlert className="size-4 text-warning-text" />
        Задержано проверкой
      </div>
      <p className="text-[13px] text-fg-secondary">
        Запрос не отправлен: в нём найдены слова из словаря (объекты, подрядчики, договоры).
        Решите, отправлять ли.
      </p>
      <div className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface">
        {unitGroups.map((group) => (
          <UnitGroupRow
            key={unitGroupKey(group)}
            group={group}
            unitLabel={unitLabel}
            onOpenFamily={onOpenFamily}
          />
        ))}
        {pageJobs.map((job) => (
          <HeldJobRow key={job.job_id} job={job} unitLabel={unitLabel} />
        ))}
      </div>
      {own.length > 0 && (
        <Pager
          page={safePage}
          total={own.length}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(n) => {
            setPageSize(n);
            setPage(1);
          }}
          showPageNumbers
        />
      )}
    </section>
  );
}
