import { StatusPill, type StatusTone } from "@/components/ui-domain/StatusPill";
import { TableCell } from "@/components/ui/table";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CellState, StagePositionsCell, StageSummaryChange } from "@/types/domain";

import { KIND_LABEL, REASON_LABEL, STATE_LABEL } from "./cellCopy";
import { estimateRowsLabel } from "./drilldownCopy";
import { formatQuantity } from "./drilldownData";
import { ChangeBadge } from "./SummaryCell";

/**
 * Ячейка работы попозиционного раскрытия — три этажа: сумма (или подпись
 * состояния), объём заказчика, изменение к предыдущему выбранному этапу
 * (спека 2026-08-30-position-drilldown-design.md §2.4, §2.5, §2.7, §6.3;
 * задача 9 плана).
 *
 * Словари подписей и `ChangeBadge` — те же, что у ячейки свода (`cellCopy.ts`,
 * `SummaryCell.tsx`): контракт свода берётся ДОСЛОВНО (§2.5, §6.3), второй
 * словарь тех же состояний и видов изменения здесь не заводится.
 */

/** Тон пилюли состояния — то же деление, что у ячейки свода (`SummaryCell.tsx`,
 *  `CELL_STATE_TONE`): «снято» тревожно, «отсутствует» — плоский факт о файле,
 *  не о цене, и пилюли не получает вовсе. */
const CELL_STATE_TONE: Record<Exclude<CellState, "amount" | "absent">, StatusTone> = {
  removed: "warning",
  not_evaluated: "neutral",
};

/**
 * Повторяет ли значок изменения слово, которое уже сказало состояние ячейки
 * (§2.5) — тот же приём, что и у `SummaryCell` свода: совпало слово по
 * словарям — второй раз не печатаем. Там функция (`changeRepeatsState`) не
 * экспортирована, поэтому приём повторён здесь тем же способом — сравнением
 * по СУЩЕСТВУЮЩИМ словарям, а не собственным списком пар.
 */
function repeatsState(state: CellState, change: StageSummaryChange): boolean {
  if (state === "amount") return false;
  if (change.kind === "percent" || change.kind === "abs_only" || change.kind === "none") return false;
  return KIND_LABEL[change.kind] === STATE_LABEL[state];
}

export function PositionCell({
  cell,
  rowCountChanged,
}: {
  cell: StagePositionsCell;
  /**
   * Число строк сметы группы сменилось относительно предыдущей колонки
   * ПРИСУТСТВИЯ (пропуская `absent`) — та же ось, что `quantity_changed`
   * несёт для объёма, но вычислена КЛИЕНТОМ из полной трассы `row.cells`
   * (`drilldownData.ts::estimateRowsChangedPerCell`), а не сервером: контракт
   * не меняется, факт уже есть на проводе (макет 2026-08-29, строка 461;
   * закрывает бывший `docs/TECH_DEBT.md`, пункт 26).
   *
   * Тот же факт теперь решает и ВИДИМОСТЬ счётчика, не только его тон
   * (полировка после мержа, §2.4 спеки): группа с одной строкой сметы —
   * 79,5 % ячеек статей и 96,6 % ячеек допработ на боевой базе (§2.4) — и
   * печатать «1 стр.» там, где число НЕ говорит ничего нового и уже
   * задублировано пилюлей `AMBIGUOUS_PILL` для случаев с несколькими
   * строками, было шумом. Правило: счётчик печатается, когда строк больше
   * одной, ИЛИ когда их число сменилось с прошлой колонки присутствия —
   * второе условие обязательно, иначе ячейка с этим тоном (ниже) осталась
   * бы выделена без единой цифры, объясняющей выделение.
   */
  rowCountChanged: boolean;
}) {
  const {
    state,
    amount,
    amount_unavailable_reason,
    quantity,
    quantity_unit,
    quantity_changed,
    estimate_rows,
    change,
  } = cell;
  // Число печатается только в состоянии `amount` и только когда сумма не
  // погашена неизвестной базой НДС средней колонки (§2.8) — как у ячейки свода.
  const showsNumber = state === "amount" && !amount_unavailable_reason;
  const formattedQuantity = formatQuantity(quantity);
  const hideChange = amount_unavailable_reason !== null || repeatsState(state, change);
  // Счётчик строк сметы печатается, когда он больше единицы, ИЛИ когда он
  // сменился относительно предыдущей колонки присутствия (`rowCountChanged`,
  // §2.4 спеки, полировка после мержа) — не при каждом `estimate_rows > 0`,
  // как было раньше (макет 2026-08-29, строка 458, печатает счётчик
  // безусловно; спека фиксирует расхождение явно). У допработы и
  // диагностической строки (`unmatched`) объёма нет структурно, но число
  // строк сметы у них есть, и счётчик остаётся единственным содержимым
  // этажа — прежде оно терялось целиком, и группа, у которой оно меняется
  // между этапами при той же сумме, читалась только как изменение цены
  // (finding 3 ревью PR #35). Инвариант контракта (§2.11): `estimate_rows
  // === 0` бывает РОВНО у состояния `absent` — там третьего этажа
  // по-прежнему нет вовсе, независимо от этого правила.
  const showsRowCount = estimate_rows > 0 && (estimate_rows > 1 || rowCountChanged);
  const rowsLabel = showsRowCount ? estimateRowsLabel(estimate_rows) : null;
  // Отдельно от `formattedQuantity`: `quantity_unit` нулим самостоятельно
  // (контракт несёт его `string | null` независимо от количества, — ревью PR
  // #35, второй круг), поэтому единица приклеивается к числу ТОЛЬКО когда
  // обе части есть — иначе на этаже читалось бы «5 null», а число без
  // единицы должно остаться просто числом.
  const quantityLabel =
    formattedQuantity === null
      ? null
      : quantity_unit === null
        ? formattedQuantity
        : `${formattedQuantity} ${quantity_unit}`;
  // Части этажа объёма собираются, а не подставляются в один шаблон: список
  // отфильтрован от `null` и склеен разделителем, поэтому ни одна из
  // комбинаций (число строк одно / число строк + объём / число строк + объём
  // без единицы) не требует своего условия, и появление ещё одного
  // необязательного поля в будущем не сможет тихо просочиться строкой
  // `"null"` на экран.
  const tierText = [rowsLabel, quantityLabel].filter((part): part is string => part !== null).join(" · ");

  return (
    <TableCell
      className="text-right tabular-nums"
      title={amount_unavailable_reason ? REASON_LABEL[amount_unavailable_reason] : undefined}
    >
      <div className="flex items-center justify-end gap-1">
        {state === "amount" ? (
          showsNumber ? (
            <span>{formatDecimalMoney(amount)}</span>
          ) : (
            <StatusPill tone="neutral" label="нет базы НДС" />
          )
        ) : state === "absent" ? (
          <span className="text-fg-tertiary">{STATE_LABEL.absent}</span>
        ) : (
          <StatusPill tone={CELL_STATE_TONE[state]} label={STATE_LABEL[state]} />
        )}
      </div>
      {/* Третий этаж есть, когда есть ХОТЯ БЫ ОДНА из двух частей — печатаемый
          счётчик строк сметы (не любой `estimate_rows > 0`, см. `showsRowCount`
          выше) ИЛИ объём (§2.4, §6.3, finding 3 ревью PR #35). Гейт — по
          `tierText`, а не по `rowsLabel` в одиночку: одна строка сметы с
          объёмом и без смены счётчика теперь не печатает счётчик вовсе, но
          этаж обязан остаться на экране ради объёма. У ячейки состояния
          `absent` нет ни счётчика, ни объёма (`estimate_rows === 0`), и этаж
          по-прежнему не рендерится вовсе, а не пустой. Порядок и разделитель —
          по макету 2026-08-29, строки 445-461: «N стр. · объём единица». */}
      {tierText !== "" && (
        // Найдено сверкой с макетом (задача 12, DoD 6): без тона по умолчанию
        // объём рендерился цветом ОСНОВНОГО текста — той же силы, что сумма
        // строкой выше, — и третий этаж переставал читаться как подчинённый
        // (§2.4). Макет держит то же правило: `.qty` приглушён ВСЕГДА,
        // `.qty.chg` — единственное исключение (там уже с 30.08.2026 принят
        // свой акцент, `text-warning-text`, тот же токен, что несёт
        // несходящаяся ячейка свода, а не цвет макета).
        //
        // Тон включается ЛИБО от `quantity_changed`, ЛИБО от `rowCountChanged`
        // (бывший `docs/TECH_DEBT.md`, пункт 26): макет красит акцентом ВЕСЬ
        // этаж целиком (строка 461, группа «Трассы…»), когда меняется число
        // строк сметы при неизменном печатаемом объёме, — эмфаза одна на весь
        // этаж, а не половина текста. Контракт при этом не тронут:
        // `rowCountChanged` не новое поле ответа, а клиентская производная из
        // `row.cells` (`drilldownData.ts::estimateRowsChangedPerCell`),
        // посчитанная той же осью сравнения, что сервер уже применяет к
        // `quantity_changed` — предыдущая колонка ПРИСУТСТВИЯ, а не соседняя
        // по порядку.
        <div
          data-testid="cell-quantity"
          className={cn(
            "mt-0.5 text-2xs",
            quantity_changed || rowCountChanged ? "text-warning-text" : "text-fg-tertiary"
          )}
        >
          {tierText}
        </div>
      )}
      {!hideChange && (
        <div className="mt-0.5 text-2xs font-normal">
          <ChangeBadge change={change} />
        </div>
      )}
    </TableCell>
  );
}
