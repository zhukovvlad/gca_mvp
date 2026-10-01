import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { formatConfidence } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  useConfirmSuggestions,
  useOtherFamily,
  useRejectSuggestion,
  useWorkFamilies,
} from "@/services/queries";
import type { SuggestionBand, SuggestionGroup, SuggestionRow } from "@/types/domain";

import { BAND_LABEL } from "./labels";
import { SourceChip } from "./SourceChip";

/** Сколько строк группа печатает сразу; остальные — «… и ещё K, отмечены», раскрываются кнопкой. */
const VISIBLE_ROWS = 20;

/** Ключ группы: пара «семья + полоса» (одна семья даёт до трёх групп). */
function groupKey(group: SuggestionGroup): string {
  return `${group.family_id}:${group.band}`;
}

const BAND_TINT: Record<SuggestionBand, string> = {
  high: "border-accent-border bg-accent-soft text-accent-text",
  mid: "border-warning-border bg-warning-soft text-warning-text",
  low: "border-danger-border bg-danger-soft text-danger-text",
};

const CONFIDENCE_TEXT: Record<SuggestionBand, string> = {
  high: "text-accent-text",
  mid: "text-warning-text",
  low: "text-danger-text",
};

function formatDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleDateString("ru-RU");
}

interface OtherFamilyDialogProps {
  group: SuggestionGroup;
  unitLabel: string;
  row: SuggestionRow | null;
  onClose: () => void;
}

/**
 * «Другая семья…»: семья выбирается из активных семей ТОЙ ЖЕ единицы, кроме
 * предложенной. Назначение идёт тем же путём, что ручное (`source = manual`,
 * спека semantic-suggestions §2.9) — сервер сверит единицу и активность.
 */
function OtherFamilyDialog({ group, unitLabel, row, onClose }: OtherFamilyDialogProps) {
  const familiesQ = useWorkFamilies("active");
  const otherFamily = useOtherFamily();
  const [choice, setChoice] = useState<number | null>(null);

  const candidates = (familiesQ.data ?? []).filter(
    (f) => f.unit_code === group.unit_code && f.id !== group.family_id
  );

  function handleClose() {
    setChoice(null);
    onClose();
  }

  return (
    <Dialog open={row !== null} onOpenChange={(open) => !open && handleClose()}>
      {row !== null && (
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Другая семья</DialogTitle>
            <DialogDescription>{row.title}</DialogDescription>
          </DialogHeader>
          {candidates.length > 0 ? (
            <div className="grid gap-2 py-2">
              <Label htmlFor="other-family-select">Активные семьи {unitLabel}</Label>
              <EntitySelect
                id="other-family-select"
                items={candidates}
                value={choice}
                onChange={(v) => setChoice(v as number | null)}
                getLabel={(f) => f.title}
                placeholder="Выбрать семью"
              />
            </div>
          ) : (
            <p className="py-2 text-sm text-fg-secondary">Других активных семей {unitLabel} нет.</p>
          )}
          <p className="text-xs text-fg-tertiary">
            Назначение идёт тем же путём, что ручное: единица должна совпасть, семья — быть
            активной.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={handleClose}>
              Отмена
            </Button>
            {candidates.length > 0 && (
              <Button
                disabled={choice === null || otherFamily.isPending}
                onClick={() =>
                  choice !== null &&
                  otherFamily.mutate(
                    {
                      suggestionId: row.suggestion_id,
                      familyId: choice,
                      familyTitle: candidates.find((f) => f.id === choice)?.title ?? "",
                    },
                    { onSuccess: handleClose }
                  )
                }
              >
                Назначить
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );
}

interface GroupCardProps {
  group: SuggestionGroup;
  unitLabel: string;
  defaultOpen: boolean;
}

/**
 * Одна группа очереди: предложенная семья, единица и число строк, полоса
 * уверенности, «Подтвердить отмеченные N». Отметки — локальное состояние
 * группы (снятые id); подтверждение шлёт ровно отмеченные, в том числе строки,
 * не выведенные за пределом {@link VISIBLE_ROWS} (они отмечены по умолчанию).
 */
function GroupCard({ group, unitLabel, defaultOpen }: GroupCardProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [showAll, setShowAll] = useState(false);
  const [unchecked, setUnchecked] = useState<ReadonlySet<number>>(new Set());
  const [otherRow, setOtherRow] = useState<SuggestionRow | null>(null);
  const confirm = useConfirmSuggestions();
  const reject = useRejectSuggestion();

  const checkedIds = group.rows.map((r) => r.suggestion_id).filter((id) => !unchecked.has(id));
  const visibleRows = showAll ? group.rows : group.rows.slice(0, VISIBLE_ROWS);
  const hidden = group.rows.length - visibleRows.length;

  function toggle(id: number, checked: boolean) {
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (checked) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div
      data-testid="suggestion-group"
      className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
    >
      <div className="flex items-center gap-3 px-4 py-3.5">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Раскрыть группу"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        </Button>
        <span className="min-w-0 truncate text-[15px] font-semibold text-fg" title={group.family_title}>
          {group.family_title}
        </span>
        <span className="text-[13px] text-fg-tertiary">
          {unitLabel} · {group.total}
        </span>
        <Badge variant="outline" className={BAND_TINT[group.band]}>
          {BAND_LABEL[group.band]}
        </Badge>
        <Button
          className="ml-auto"
          disabled={checkedIds.length === 0 || confirm.isPending}
          onClick={() =>
            confirm.mutate({
              ids: checkedIds,
              familyTitle: group.family_title,
              leftCount: group.rows.length - checkedIds.length,
            })
          }
        >
          Подтвердить отмеченные {checkedIds.length}
        </Button>
      </div>

      {open && (
        <div className="border-t border-border-subtle">
          {visibleRows.map((row) => {
            const isOff = unchecked.has(row.suggestion_id);
            return (
              <div
                key={row.suggestion_id}
                data-testid="suggestion-row"
                className={cn(
                  "flex items-start gap-4 border-b border-border-subtle px-4 py-3 last:border-b-0",
                  isOff && "bg-surface-hover opacity-65"
                )}
              >
                <Checkbox
                  className="mt-1"
                  aria-label="Отметить строку"
                  checked={!isOff}
                  onCheckedChange={(checked) => toggle(row.suggestion_id, checked === true)}
                />
                <div
                  className={cn(
                    "w-11 flex-none pt-px font-mono text-[13px] tabular-nums",
                    CONFIDENCE_TEXT[group.band]
                  )}
                >
                  {formatConfidence(row.confidence)}
                </div>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
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
                  <div className="text-xs text-fg-secondary italic">Пояснение ИИ: {row.reason}</div>
                  {row.previously_rejected && (
                    <div className="text-xs text-warning-text">
                      ранее отклонено admin: {row.previously_rejected.family_title},{" "}
                      {formatDate(row.previously_rejected.decided_at)}
                    </div>
                  )}
                </div>
                <div className="flex flex-none gap-1.5">
                  <Button variant="outline" size="sm" onClick={() => setOtherRow(row)}>
                    Другая семья…
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={reject.isPending}
                    onClick={() => reject.mutate(row.suggestion_id)}
                  >
                    Отклонить
                  </Button>
                </div>
              </div>
            );
          })}
          {hidden > 0 && (
            <div className="flex items-center gap-3 px-4 pt-2.5 pb-3.5 pl-[76px] text-[13px] text-fg-tertiary">
              <span>… и ещё {hidden}, отмечены</span>
              <Button variant="ghost" size="xs" onClick={() => setShowAll(true)}>
                Показать все
              </Button>
            </div>
          )}
        </div>
      )}

      <OtherFamilyDialog
        group={group}
        unitLabel={unitLabel}
        row={otherRow}
        onClose={() => setOtherRow(null)}
      />
    </div>
  );
}

interface SuggestionGroupsProps {
  groups: SuggestionGroup[];
  /** Код единицы (`M2`) → символ (`м²`); неизвестный код печатается как есть. */
  unitLabel: (code: string | null) => string;
}

/** Список групп очереди «Семья из списка»; первая группа раскрыта, как в макете. */
export function SuggestionGroups({ groups, unitLabel }: SuggestionGroupsProps) {
  return (
    <div className="flex flex-col gap-3">
      {groups.map((group, index) => (
        <GroupCard
          key={groupKey(group)}
          group={group}
          unitLabel={unitLabel(group.unit_code)}
          defaultOpen={index === 0}
        />
      ))}
    </div>
  );
}
