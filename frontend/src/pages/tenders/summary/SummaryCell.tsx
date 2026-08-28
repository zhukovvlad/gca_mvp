import type { ReactNode } from "react";

import { StatusPill, type StatusTone } from "@/components/ui-domain/StatusPill";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDecimalMoney, roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CellState, StageSummaryCell, StageSummaryChange } from "@/types/domain";

import { KIND_LABEL, REASON_LABEL, STATE_LABEL } from "./cellCopy";

/**
 * Ячейка таблицы свода и её исчерпывающий значок изменения (спека
 * 2026-08-27-stage-summary-design.md §2.5–§2.7, §2.9, §2.13; задача 8 плана).
 *
 * Рендер — строго ПО ДАННЫМ (§2.14, Global 13): состояние ячейки, вид
 * изменения, его направление и причина недоступности суммы приходят с сервера
 * каждое своим полем и рисуются своей веткой — ни одно не выводится из
 * другого. В частности состояние (`state`) выигрывает у суммы (`amount`)
 * всегда, даже если они противоречат друг другу (тест
 * `StageSummaryTable.test.tsx`, «по полю, а не по сумме»).
 */

/**
 * Тон пилюли состояния — «снято» тревожнее «не оценивалась». `absent` сюда не
 * входит (fix round 3, п.2): на макете «отсутствует» — плоский прочерк, не
 * пилюля, потому что это факт о ФАЙЛЕ (статьи там нет вовсе), а не о цене —
 * пилюля рядом с «снято»/«не оценивалась» читалась бы как утверждение того же
 * рода про цену, которого здесь нет.
 */
const CELL_STATE_TONE: Record<Exclude<CellState, "amount" | "absent">, StatusTone> = {
  removed: "warning",
  not_evaluated: "neutral",
};

/** Направление → тон текста. `flat` НЕ окрашивается как рост — своим тоном. */
function directionToneClass(direction: StageSummaryChange["direction"]): string | undefined {
  if (direction === "up") return "text-accent-text";
  if (direction === "down") return "text-danger-text";
  if (direction === "flat") return "text-fg-tertiary";
  return undefined;
}

/**
 * Значок изменения — используется и в ячейках таблицы, и в KPI «Последний к
 * первому» (`inKpi`): тот же компонент, а не копия под другую подпись.
 *
 * `switch` по `kind` завершается веткой `never` — если контракт добавит новый
 * вид изменения, а эта ветка не будет расширена, сборка типов упадёт здесь, а
 * не отрисует новый вид пустотой.
 */
export function ChangeBadge({ change, inKpi }: { change: StageSummaryChange; inKpi?: boolean }) {
  const toneClass = directionToneClass(change.direction);

  let content: ReactNode;
  switch (change.kind) {
    case "percent": {
      const pct = roundDecimalPercent(change.value);
      content = pct?.text ?? "—";
      break;
    }
    case "abs_only":
      content = (
        <>
          {formatDecimalMoney(change.value)} Δ, без %
        </>
      );
      break;
    case "appeared":
    case "reappeared":
    case "removed":
    case "disappeared":
      content = <StatusPill tone="neutral" label={KIND_LABEL[change.kind]} />;
      break;
    case "none":
      content = inKpi ? "—" : null;
      break;
    default: {
      // Исчерпывающая проверка компилятором: новый ChangeKind обязан получить
      // свою ветку выше — иначе сборка типов падает здесь.
      const exhaustive: never = change.kind;
      throw new Error(`Неизвестный вид изменения: ${String(exhaustive)}`);
    }
  }

  return (
    <span
      data-testid="change"
      className={toneClass}
      title={change.reason ? REASON_LABEL[change.reason] : undefined}
    >
      {content}
    </span>
  );
}

/** Подпись подсказки неполноты (§2.1 контракта): обе величины и, если есть,
 *  отдельный счётчик неконечных значений — одна причина не заменяет другую. */
function incompletenessLabel(rows: StageSummaryCell["rows"]): string {
  const base = `Сумма неполна: учтено ${rows.rows_with_amount} из ${rows.row_count} строк`;
  return rows.rows_not_finite > 0 ? `${base}; неконечных значений: ${rows.rows_not_finite}` : base;
}

/**
 * Ячейка таблицы свода. `extra` — дополнительная строка под значком изменения
 * (используется строкой «Итого» для подписи сходимости колонки): SummaryCell
 * остаётся единственным местом, где читаются `state`/`amount_unavailable_reason`,
 * и строка «Итого» не заводит свой параллельный рендер той же ячейки.
 */
export function SummaryCell({ cell, extra }: { cell: StageSummaryCell; extra?: ReactNode }) {
  const { state, amount, amount_unavailable_reason, rows, change } = cell;
  const incomplete = rows.rows_with_amount < rows.row_count;

  return (
    <td
      className="text-right tabular-nums align-top"
      title={amount_unavailable_reason ? REASON_LABEL[amount_unavailable_reason] : undefined}
    >
      <div className="flex items-center justify-end gap-1">
        {state === "amount" ? (
          // `amount_unavailable_reason` гасит ТОЛЬКО показанную сумму — ровно
          // то, чего база НДС лишает (спека §2.5, §2.8; AGENTS.md §10): для
          // остальных состояний числа никогда не было, поэтому у них нечего
          // withhold-ить, и ветка ниже их не касается (fix round review PR,
          // дефект «состояние пропадает в недоступной колонке»).
          amount_unavailable_reason ? (
            <StatusPill tone="neutral" label="нет базы НДС" />
          ) : (
            <span>{formatDecimalMoney(amount)}</span>
          )
        ) : state === "absent" ? (
          // Плоский прочерк — как на макете (`table.pass`, статья "15"):
          // «отсутствует» говорит, что статьи нет в файле вовсе, а не что-то
          // о её цене, и пилюля здесь читалась бы неверно (fix round 3, п.2).
          // Неизвестная база НДС ничего не меняет: у `absent` и так нет суммы.
          <span data-testid="cell-dash" className="text-fg-tertiary">
            {STATE_LABEL.absent}
          </span>
        ) : (
          // `removed`/`not_evaluated`: состояние рисуется как обычно и при
          // неизвестной базе НДС — сумма и состояние гасятся раздельно (§2.5:
          // «состояние не зависит от ставки НДС»). Причина недоступности суммы
          // уже несётся `title` самой `<td>` (ниже) — второй, видимой рядом с
          // пилюлей подписи макет не показывает ни для одного состояния.
          <StatusPill tone={CELL_STATE_TONE[state]} label={STATE_LABEL[state]} />
        )}
        {incomplete && (
          <Tooltip>
            <TooltipTrigger aria-label={incompletenessLabel(rows)} className={cn("cursor-help text-warning")}>
              ◐
            </TooltipTrigger>
            <TooltipContent>{incompletenessLabel(rows)}</TooltipContent>
          </Tooltip>
        )}
      </div>
      {!amount_unavailable_reason && (
        <div className="mt-0.5">
          <ChangeBadge change={change} />
        </div>
      )}
      {/*
        Без цвета здесь: `extra` (сходимость «Итого», fix round 2, п.4) несёт
        СВОЙ тон в трёх состояниях — жёсткий `text-fg-tertiary` на обёртке
        забивал бы его тем же тоном для всех трёх, что и было дефектом.
      */}
      {extra && <div className="mt-0.5 text-2xs">{extra}</div>}
    </td>
  );
}
