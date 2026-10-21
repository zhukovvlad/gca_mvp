import { cn } from "@/lib/utils";
import { formatUsd } from "@/lib/format";
import type { SchemaEditSummary } from "@/types/domain";

import { pluralRu, SCHEMA_EDIT_PENDING_LABEL } from "../labels";

/** Сколько переносов и выведенных значений показывается строками; остальное — числом. */
const LISTED = 6;

function contexts(count: number): string {
  return `${count.toLocaleString("ru-RU")} ${pluralRu(count, "контекст", "контекста", "контекстов")}`;
}

function variantsLine(summary: SchemaEditSummary): string {
  const { variants_before: before, variants_after_now: after, variants_pending_answers: pending } = summary;
  if (pending) {
    return `Варианты семьи соберутся заново: сейчас ${before}, сколько станет — будет известно после ответов модели.`;
  }
  if (before === after) return `Варианты семьи не меняются: их по-прежнему ${before}.`;
  return `Варианты семьи изменятся сразу: было ${before}, станет ${after}.`;
}

interface EditSummaryPanelProps {
  summary: SchemaEditSummary | null;
  /** Запрос по нынешнему телу ещё не вернулся. */
  pending: boolean;
  /** Подпись отказа проверки: сохранить такую правку нельзя. */
  refusal: string | null;
}

/**
 * Низ окна правки (спека 3в §2.5, §2.11): «Что изменит правка» — ответ предпросмотра. Пока он не
 * пришёл, вместо сводки подпись ожидания; при отказе проверки — подпись отказа без кода.
 */
export function EditSummaryPanel({ summary, pending, refusal }: EditSummaryPanelProps) {
  if (refusal !== null) {
    return (
      <div
        role="status"
        className="grid gap-1 rounded-lg border border-danger-border bg-danger-soft px-3 py-2 text-sm text-danger-text"
      >
        <b className="font-semibold">Правку нельзя сохранить</b>
        <p>{refusal}</p>
      </div>
    );
  }
  if (summary === null || pending) {
    return (
      <div role="status" className="rounded-lg border border-info-border bg-info-soft px-3 py-2 text-sm text-info-text">
        {SCHEMA_EDIT_PENDING_LABEL}
      </div>
    );
  }

  const warn = summary.variants_pending_answers || summary.variants_before !== summary.variants_after_now;
  const asked = summary.asked_by_parameter;
  return (
    <div
      aria-label="Что изменит правка"
      role="region"
      className={cn(
        "grid gap-1.5 rounded-lg border px-3 py-2 text-sm",
        warn
          ? "border-warning-border bg-warning-soft text-warning-text"
          : "border-info-border bg-info-soft text-info-text"
      )}
    >
      <b className="font-semibold">Что изменит правка</b>
      <ul className="grid list-disc gap-1 pl-4">
        <li>{variantsLine(summary)}</li>
        {asked.length === 0 ? (
          <li>У модели ничего не запрашивается.</li>
        ) : (
          <>
            {asked.map((entry) => (
              <li key={entry.ordinal}>
                У модели запросится «{entry.name}» — у {contexts(entry.contexts)}.
              </li>
            ))}
            <li>
              Резерв {formatUsd(summary.reserve_usd)}, ожидаемая цена при попадании в кэш ≈{" "}
              {formatUsd(summary.expected_cached_usd)}.
            </li>
          </>
        )}
        {summary.value_moves.length > 0 && (
          <li>
            Прежние значения переносятся без модели:{" "}
            {summary.value_moves
              .slice(0, LISTED)
              .map((move) => `«${move.from_value}» → «${move.to_value}» (${contexts(move.contexts)})`)
              .join(", ")}
            {summary.value_moves.length > LISTED && ` и ещё ${summary.value_moves.length - LISTED}`}.
          </li>
        )}
        {summary.retired.length > 0 && (
          <li>
            Уходят из списка в историю, не удаляются:{" "}
            {summary.retired
              .slice(0, LISTED)
              .map((entry) => `«${entry.value}» (${entry.parameter}, ${contexts(entry.contexts)})`)
              .join(", ")}
            {summary.retired.length > LISTED && ` и ещё ${summary.retired.length - LISTED}`}.
          </li>
        )}
        {summary.refreshing_contexts > 0 && (
          <li>
            У {contexts(summary.refreshing_contexts)} значение уточняется: до ответа они остаются в прежнем
            варианте.
          </li>
        )}
        {summary.repriced_pending_jobs > 0 && (
          <li>Пересчитаются ждущие запросы значений семьи: {summary.repriced_pending_jobs}.</li>
        )}
      </ul>
    </div>
  );
}
