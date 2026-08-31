import type { StagePositionsCell, StagePositionsRow } from "@/types/domain";

/**
 * Данные попозиционного раскрытия статьи — вспомогательные функции без React
 * (спека 2026-08-30-position-drilldown-design.md §2.1, §2.4, §2.7, §2.11;
 * задача 9 плана).
 */

/**
 * N кнопки «Работы · N» — число ГРУПП разложения, включая свёрнутые (§2.1).
 * Свёрнутые строки (`collapsed_appeared_disappeared`, `rest`) несут своё
 * число групп в `group_count`; все прочие строки — это ровно одна группа
 * каждая.
 */
export function drilldownGroupCount(rows: StagePositionsRow[]): number {
  return rows.reduce(
    (n, r) =>
      n + (r.kind === "collapsed_appeared_disappeared" || r.kind === "rest" ? (r.group_count ?? 0) : 1),
    0
  );
}

const QUANTITY_FORMAT = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

/**
 * Объём этапа: сырые значения через `"+"` (несколько строк сметы, §2.4),
 * каждое форматируется разрядами и не длиннее одного знака после запятой:
 * `"6+11"` -> `"6 + 11"`. Значения в базе лежат как в файле (у площадей это
 * `"8726.397168"`), и форматирование — задача клиента, не хранения.
 */
export function formatQuantity(quantity: string | null): string | null {
  if (quantity === null) return null;
  return quantity
    .split("+")
    .map((part) => QUANTITY_FORMAT.format(Number(part)))
    .join(" + ");
}

/**
 * Устойчивый ключ строки для React — ПОЛЕ ОТВЕТА `row_key`, а не сборка из
 * `kind` и `chapter_ref_raw`: у двух допработ разных лотов ссылка одна
 * (§2.7, §2.11), и собранный ключ схлопнул бы их в одну строку React.
 */
export function drilldownRowKey(row: StagePositionsRow): string {
  return row.row_key;
}

/**
 * Нужно ли называть лот в пилюлях допработы (§2.7). Считаются РАЗЛИЧНЫЕ
 * непустые `lot_key` строк, а не строки: `rows.length > 1` было бы неверно —
 * две допработы ОДНОГО лота в пилюлю лот не приносят, а строки без лота
 * (работы, свёрнутые строки) не участвуют вовсе.
 */
export function showsLot(rows: StagePositionsRow[]): boolean {
  const lots = new Set(rows.map((r) => r.lot_key).filter((lot): lot is string => Boolean(lot)));
  return lots.size > 1;
}

/**
 * Меняется ли число строк сметы группы (`estimate_rows`) относительно
 * ПРЕДЫДУЩЕЙ КОЛОНКИ ПРИСУТСТВИЯ, а не соседней по порядку — та же ось
 * сравнения, что сервер использует для `quantity_changed`
 * (`services/position_drilldown.py::group_cells`: `prev_quantities`
 * продвигается только когда `stage is not None`, т.е. колонка `absent`
 * пропускается, а не читается как «смена от пустоты»). Инвариант контракта
 * (§2.11, тот же, на который опирается `PositionCell`): `estimate_rows === 0`
 * бывает РОВНО у состояния `absent`, поэтому «присутствует» здесь — ровно
 * `estimate_rows > 0`, без отдельного поля состояния на входе.
 *
 * Вычислено на клиенте, а не на сервере: `row.cells` уже несёт ПОЛНУЮ трассу
 * группы по всем колонкам (см. `row.cells.map` в `PositionDrilldown.tsx`), и
 * этого достаточно, чтобы получить факт «число строк сменилось с прошлого
 * присутствия» без изменения контракта — ни переопределения `quantity_changed`,
 * ни нового поля ответа не требуется (находка внешнего ревью, закрывающая
 * `docs/TECH_DEBT.md`, бывший пункт 26).
 *
 * Наивная альтернатива — сравнение с СОСЕДНЕЙ колонкой безусловно — ложно
 * сработала бы на первой колонке после разрыва: она читала бы «после
 * отсутствия» как смену числа строк, хотя причина разрыва — нехватка данных,
 * а не перемена числа строк. Ровно эта ловушка и была написана сервером
 * первой (`if stage is not None`), и клиент обязан повторить её, а не
 * изобретать свою ось.
 */
export function estimateRowsChangedPerCell(cells: StagePositionsCell[]): boolean[] {
  let prevPresentRows: number | null = null;
  return cells.map((cell) => {
    const present = cell.estimate_rows > 0;
    const changed = present && prevPresentRows !== null && cell.estimate_rows !== prevPresentRows;
    if (present) prevPresentRows = cell.estimate_rows;
    return changed;
  });
}
