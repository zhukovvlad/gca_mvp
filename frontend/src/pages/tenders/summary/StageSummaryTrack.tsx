import { EmptyState } from "@/components/ui-domain/EmptyState";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { formatDecimalMoney } from "@/lib/format";
import type { StageSummary, StageSummaryColumn } from "@/types/domain";

import { REASON_LABEL } from "./cellCopy";
import { ChangeBadge } from "./SummaryCell";

/**
 * Трасса торга — один блок на выбранный этап, высотой в проценты (спека
 * 2026-08-27-stage-summary-design.md §2.3–§2.4, §2.11; задача 8 плана).
 *
 * Обычные блоки, не `recharts`: осей и наведения трасса не несёт, а у
 * `recharts` в проекте два записанных граба (AGENTS.md §11).
 */

/** Подпись колонки: «Этап N» либо «Этап N · метка», если метка есть. */
function stageLabel(column: StageSummaryColumn): string {
  return column.label ? `Этап ${column.stage_no} · ${column.label}` : `Этап ${column.stage_no}`;
}

/**
 * Столбик одной колонки. Высота — ЧИСЛОМ ИЗ `bar_height_pct` (только для CSS,
 * не сравнение и не деление клиентом, §2.14 Global 13): сервер уже посчитал
 * отношение к максимуму, а клиент лишь переводит decimal-строку в проценты
 * CSS-свойства.
 *
 * Колонка с неизвестной базой НДС — штрихованный слот БЕЗ числа: `bar_height_pct`
 * у неё всегда `null` (fixtures.test.ts, инвариант), поэтому столбик рисовать
 * нечем — а не потому, что сумма оказалась нулевой или отрицательной.
 */
function TrackColumn({ column }: { column: StageSummaryColumn }) {
  if (column.vat_state === "unknown_vat_base") {
    return (
      <div className="flex flex-1 flex-col items-center gap-1 min-w-0">
        <div
          data-testid="track-slot-unknown"
          title={REASON_LABEL.unknown_vat_base}
          className="flex h-[150px] w-full max-w-[130px] items-center justify-center rounded-t-md border border-border-subtle bg-[repeating-linear-gradient(45deg,var(--border-subtle)_0_4px,transparent_4px_8px)] p-2 text-center"
        >
          {/* Подпись ВИДИМАЯ, не только в title — читатель обязан увидеть причину без наведения. */}
          <StatusPill tone="neutral" label="нет базы НДС" />
        </div>
        <span className="text-2xs font-semibold uppercase tracking-wider text-fg-tertiary">
          {stageLabel(column)}
        </span>
      </div>
    );
  }

  /*
    Fix round 2, п.5: `bar_height_pct` — decimal-строка либо `null`, и
    `Number(null)` даёт `0`, что нарисовало бы столбик высотой 0 % —
    неотличимый от НАСТОЯЩЕГО нулевого/близкого к нулю итога. `null` значит
    «высоты нет» (сервер её не посчитал), а не «высота — число ноль»: ветка
    отсутствия — явная, а не через приведение типов. Сегодня бэкенд не может
    отдать эту комбинацию для известной по НДС колонки при доступной трассе
    (инвариант `fixtures.test.ts`, «bar_height_pct ≠ null ⇔ track.available
    = true AND column.total !== null» — здесь `vat_state` уже не
    `unknown_vat_base`, то есть `total` известен, и при доступной трассе
    высота обязана быть посчитана), поэтому ветка сегодня не упражняется
    реальным ответом — но контракт поля важнее сегодняшней недостижимости.
  */
  return (
    <div className="flex flex-1 flex-col items-center gap-1 min-w-0">
      <div className="flex h-[150px] w-full max-w-[130px] items-end rounded-t-md bg-surface-sunken">
        {column.bar_height_pct !== null && (
          <div
            data-testid="track-bar"
            className="w-full rounded-t-md bg-accent"
            style={{ height: `${Number(column.bar_height_pct)}%` }}
          />
        )}
      </div>
      <span className="font-mono text-sm font-semibold text-fg">{formatDecimalMoney(column.total)}</span>
      <span className="text-2xs font-semibold uppercase tracking-wider text-fg-tertiary">
        {stageLabel(column)}
      </span>
      <ChangeBadge change={column.total_change} />
    </div>
  );
}

export function StageSummaryTrack({ summary }: { summary: StageSummary }) {
  if (!summary.track.available) {
    return (
      <EmptyState
        title="Трасса не построена"
        description={summary.track.reason ? REASON_LABEL[summary.track.reason] : undefined}
      />
    );
  }

  return (
    <div className="flex items-end gap-4 rounded-lg border border-border-subtle bg-surface p-5">
      {summary.columns.map((column) => (
        <TrackColumn key={column.offer_id} column={column} />
      ))}
    </div>
  );
}
