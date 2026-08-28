import { EmptyState } from "@/components/ui-domain/EmptyState";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
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

/*
  Fix round 3, п.1, и fix round 4 (найдено замером на стенде — тестовый
  прогон этого не видит: jsdom не считает layout,
  `docs/insights/unobservable-in-the-runner.md`).

  КОНСТРАЙНТ, который обязан выполнять КАЖДЫЙ подписанный узел под столбиком:
  при 10 колонках в окне 1100px соседние экземпляры одного узла (подпись,
  сумма) не должны перекрываться, в обеих темах (при 4 колонках вид не
  меняется — там и так было просторно). Подтвердить это может ТОЛЬКО замер в
  браузере — повторный замер за координатором.

  Причина коллизии была общая для ВСЕГО, что стоит под столбиком, не только
  подписи этапа: `TrackColumn` — контейнер `flex-col` с `items-center`, а
  `items-center` на кросс-оси НЕ растягивает детей до ширины контейнера — они
  сидят по своей ЕСТЕСТВЕННОЙ ширине содержимого. Round 3 закрыл подпись
  этапа; round 4 (замер того же стенда одной строкой ниже) нашёл ТУ ЖЕ
  причину у денежной суммы — раньше не увиденную, потому что это другой
  узел, а не другое проявление уже пофиксенного. `TRACK_TEXT_CLASS` несёт
  общее лечение: `w-full min-w-0` привязывает узел к ширине его колонки (та
  уже ограничена `flex-1 min-w-0` на строке трассы), `break-words` —
  подстраховка перетекания. У суммы (`formatDecimalMoney`) ЭТА подстраховка
  особенно важна: разряды и пробел перед знаком валюты — НЕРАЗРЫВНЫЕ (NBSP),
  то есть обычного места для переноса внутри самой суммы нет вовсе — без
  `break-words` строка не переносилась бы, а просто вылезала бы за край
  колонки. Перенос ничего не скрывает — обе строки, если понадобятся,
  показывают ВСЕ цифры целиком, просто в две строки, а не одну.
*/
const TRACK_TEXT_CLASS = "w-full min-w-0 break-words text-center";
const STAGE_LABEL_CLASS = cn(
  TRACK_TEXT_CLASS,
  "text-2xs font-semibold uppercase tracking-wider text-fg-tertiary"
);
const TRACK_AMOUNT_CLASS = cn(TRACK_TEXT_CLASS, "font-mono text-sm font-semibold text-fg");

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
        <span className={STAGE_LABEL_CLASS}>{stageLabel(column)}</span>
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
      <span className={TRACK_AMOUNT_CLASS}>{formatDecimalMoney(column.total)}</span>
      <span className={STAGE_LABEL_CLASS}>{stageLabel(column)}</span>
      {/*
        Fix round 4: третий узел строки, тем же приёмом — `ChangeBadge`
        (общий компонент, его самого не трогаем) без обёртки сидел бы, как и
        сумма выше, по своей естественной ширине и мог налезать на соседний
        столбик на узких колонках.
      */}
      <div className={TRACK_TEXT_CLASS}>
        <ChangeBadge change={column.total_change} />
      </div>
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
