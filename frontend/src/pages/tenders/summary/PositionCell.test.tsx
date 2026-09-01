import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatDecimalMoney } from "@/lib/format";
import type { StagePositionsCell } from "@/types/domain";

import { KIND_LABEL, STATE_LABEL } from "./cellCopy";
import { PositionCell } from "./PositionCell";

function cell(overrides: Partial<StagePositionsCell>): StagePositionsCell {
  return {
    state: "amount",
    amount: "1000.00",
    amount_unavailable_reason: null,
    quantity: null,
    quantity_unit: null,
    quantity_changed: false,
    estimate_rows: 1,
    change: { kind: "none", value: null, direction: null, reason: "first_column" },
    ...overrides,
  };
}

function renderCell(c: StagePositionsCell, rowCountChanged = false) {
  return render(
    <table>
      <tbody>
        <tr>
          <PositionCell cell={c} rowCountChanged={rowCountChanged} />
        </tr>
      </tbody>
    </table>
  );
}

describe("PositionCell — три этажа (§2.4)", () => {
  it("сумма, объём и изменение стоят в одной ячейке", () => {
    renderCell(
      cell({
        quantity: "8726.397168",
        quantity_unit: "м²",
        change: { kind: "percent", value: "-5.0", direction: "down", reason: null },
      })
    );
    const td = screen.getByRole("cell");
    expect(td).toHaveTextContent("1 000,00");
    expect(td).toHaveTextContent("8 726,4");
    expect(td).toHaveTextContent("м²");
    expect(td).toHaveTextContent("-5");
  });

  it("изменившийся объём выделен тоном, неизменившийся — нет (§6.3)", () => {
    const { rerender } = renderCell(cell({ quantity: "6+11", quantity_unit: "шт", quantity_changed: true }));
    expect(screen.getByTestId("cell-quantity")).toHaveClass("text-warning-text");
    rerender(
      <table>
        <tbody>
          <tr>
            <PositionCell cell={cell({ quantity: "6", quantity_unit: "шт" })} rowCountChanged={false} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByTestId("cell-quantity")).not.toHaveClass("text-warning-text");
  });

  it("у ячейки без объёма и без строк сметы третьего этажа нет (§6.3, absent)", () => {
    // Инвариант контракта (§2.11): `estimate_rows === 0` бывает РОВНО у
    // состояния `absent` — здесь третьего этажа нет вовсе, а не пустой.
    renderCell(cell({ state: "absent", amount: null, estimate_rows: 0 }));
    expect(screen.queryByTestId("cell-quantity")).toBeNull();
  });

  /**
   * Finding 3 ревью PR #35: контракт несёт `estimate_rows` в каждой ячейке, а
   * `PositionCell` его прежде не читал вовсе — печатался только объём, и
   * группа, у которой менялось ЧИСЛО строк сметы (например, допработа), могла
   * быть прочитана только как изменение цены. У допработы и диагностической
   * строки (`unmatched`) объёма нет структурно, но число строк есть — третий
   * этаж обязан показать хотя бы его, без пустого «· » на месте объёма.
   */
  it("допработа/диагностическая строка без объёма печатает только число строк сметы", () => {
    renderCell(cell({ quantity: null, quantity_unit: null, estimate_rows: 3 }));
    const tier = screen.getByTestId("cell-quantity");
    expect(tier).toHaveTextContent("3 стр.");
    // Разделителя без объёма для него самого нет — иначе на этаже был бы
    // хвост «· » без числа за ним.
    expect(tier.textContent).not.toContain("·");
  });

  /**
   * Ревью PR #35 (пятый круг): `quantity_unit` — `string | null` в контракте
   * НЕЗАВИСИМО от `quantity` — единица импортируется неизвестной
   * (`unit_id = NULL`) при количестве, которое известно; на боевом стенде
   * такое сочетание встречается (6 строк из 64505). До правки этаж собирался
   * шаблонной строкой, и `null` внутри нёе стрингифицировался в текст
   * «null» — экран показывал «5 null». Счётчик строк здесь не печатается
   * (одна строка сметы, число не менялось, §2.4 полировки после мержа), так
   * что этаж целиком — объём. Проверка на ОТСУТСТВИЕ текста «null», а не
   * только на присутствие числа: тест, что проверяет лишь число, прошёл бы и
   * с дефектом на месте.
   */
  it("количество без единицы измерения не печатает слово null (ревью PR #35)", () => {
    renderCell(cell({ quantity: "5", quantity_unit: null, estimate_rows: 1 }));
    const tier = screen.getByTestId("cell-quantity");
    expect(tier).toHaveTextContent("5");
    expect(tier.textContent).not.toMatch(/null/i);
    // И никакого хвостового пробела после числа — единицы нет вовсе, а не
    // единица длиной в пробел.
    expect(tier.textContent).not.toMatch(/5\s$/);
    // Счётчика строк на этаже нет вовсе — только объём (одна строка,
    // число не менялось).
    expect(tier.textContent).not.toMatch(/стр\./);
  });

  /**
   * Макет 2026-08-29 (строка 461): группа «Трассы…» на одном из этапов несёт
   * ДВЕ строки сметы вместо одной при ТОЙ ЖЕ печатаемой сумме объёма — именно
   * этот случай доказывает, что счётчик статьи и объём это разные величины:
   * до фикса такая перемена была видна только по значку изменения суммы,
   * хотя сумма при этом могла и не подсказывать про перемену числа строк.
   * Первая колонка (одна строка, число ещё не менялось) счётчик не печатает
   * вовсе (§2.4 полировки после мержа) — только когда строк становится
   * больше одной, счётчик появляется.
   */
  it("число строк сметы группы меняется между этапами при неизменном объёме (макет, строка 461)", () => {
    const { rerender } = renderCell(cell({ quantity: "1", quantity_unit: "компл", estimate_rows: 1 }));
    expect(screen.getByTestId("cell-quantity")).toHaveTextContent("1 компл");
    expect(screen.getByTestId("cell-quantity").textContent).not.toMatch(/стр\./);
    rerender(
      <table>
        <tbody>
          <tr>
            <PositionCell cell={cell({ quantity: "1", quantity_unit: "компл", estimate_rows: 2 })} rowCountChanged={false} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByTestId("cell-quantity")).toHaveTextContent("2 стр. · 1 компл");
  });

  /**
   * Тот же угол, что и предыдущий тест, но про ТОН, а не про ТЕКСТ (бывший
   * `docs/TECH_DEBT.md`, пункт 26): макет красит акцентом весь этаж объёма
   * (`class="qty chg"`, строка 461), когда меняется число строк сметы, даже
   * если печатаемый объём не поменялся. Источник факта — `rowCountChanged`,
   * вычисленный клиентом из `row.cells` (`drilldownData.ts`,
   * `estimateRowsChangedPerCell`), а НЕ второе поле контракта: `quantity_changed`
   * здесь заведомо `false`, чтобы проверка не могла случайно пройти по старому
   * условию.
   */
  it("тон этажа объёма включается и от смены числа строк — не только от quantity_changed (макет, строка 461)", () => {
    renderCell(cell({ quantity: "1", quantity_unit: "компл", estimate_rows: 2, quantity_changed: false }), true);
    expect(screen.getByTestId("cell-quantity")).toHaveClass("text-warning-text");
  });

  it("тон этажа объёма не включается, когда не поменялись ни объём, ни число строк", () => {
    renderCell(cell({ quantity: "1", quantity_unit: "компл", estimate_rows: 2, quantity_changed: false }), false);
    expect(screen.getByTestId("cell-quantity")).not.toHaveClass("text-warning-text");
  });

  it("состояние без суммы печатает подпись из STATE_LABEL, не число", () => {
    renderCell(cell({ state: "absent", amount: null, estimate_rows: 0 }));
    expect(screen.getByRole("cell")).toHaveTextContent(STATE_LABEL.absent);
  });

  it("исчезнувшая печатает «нет в файле», а не «снято» (§6.3)", () => {
    renderCell(
      cell({
        state: "absent",
        amount: null,
        estimate_rows: 0,
        change: { kind: "disappeared", value: null, direction: null, reason: null },
      })
    );
    expect(screen.getByRole("cell")).toHaveTextContent(KIND_LABEL.disappeared);
    expect(screen.getByRole("cell")).not.toHaveTextContent(KIND_LABEL.removed);
  });

  it("«снято» состоянием не повторяется видом изменения (§2.5)", () => {
    renderCell(
      cell({
        state: "removed",
        amount: null,
        change: { kind: "removed", value: null, direction: null, reason: null },
      })
    );
    const matches = screen.getByRole("cell").textContent?.match(/снято/g) ?? [];
    expect(matches).toHaveLength(1);
  });

  it("неизвестная база НДС гасит и число, и значок изменения — печатается только причина (§2.8)", () => {
    renderCell(
      cell({
        amount: null,
        amount_unavailable_reason: "unknown_vat_base",
        // Вид изменения выбран заведомо ВИДИМЫМ (процент), чтобы третья
        // проверка что-то значила: если гашение сломать, здесь появится
        // «-5,0%», и assertion его поймает.
        change: { kind: "percent", value: "-5.0", direction: "down", reason: null },
      })
    );
    const td = screen.getByRole("cell");
    expect(td).toHaveTextContent("нет базы НДС");
    // Отдельное от пилюли утверждение: числа НЕТ вовсе, а не просто пилюля
    // где-то рядом с числом.
    expect(td).not.toHaveTextContent(formatDecimalMoney(null));
    expect(screen.queryByTestId("change")).toBeNull();
  });
});

/**
 * Полировка после мержа (§2.4 спеки): счётчик строк сметы печатается, когда
 * их больше одной, ИЛИ когда их число сменилось с предыдущей колонки
 * присутствия (`rowCountChanged`) — не при каждой непустой ячейке, как было
 * раньше. Пять исходов, которые правило различает; макет (2026-08-29, строка
 * 458) печатает счётчик безусловно и в этом расходится со спекой сознательно
 * (расхождение записано в спеке, а не в макете).
 */
describe("PositionCell — видимость счётчика строк сметы (§2.4, полировка после мержа)", () => {
  it("одна строка сметы с объёмом, число не менялось → печатается только объём", () => {
    renderCell(cell({ quantity: "248", quantity_unit: "м²", estimate_rows: 1 }), false);
    const tier = screen.getByTestId("cell-quantity");
    expect(tier).toHaveTextContent("248 м²");
    expect(tier.textContent).not.toMatch(/стр\./);
  });

  it("одна строка сметы без объёма (допработа), число не менялось → этажа нет вовсе", () => {
    renderCell(cell({ quantity: null, quantity_unit: null, estimate_rows: 1 }), false);
    expect(screen.queryByTestId("cell-quantity")).toBeNull();
  });

  it("несколько строк сметы → счётчик и объём вместе, «3 стр. · 248 м²»", () => {
    renderCell(cell({ quantity: "248", quantity_unit: "м²", estimate_rows: 3 }), false);
    expect(screen.getByTestId("cell-quantity")).toHaveTextContent("3 стр. · 248 м²");
  });

  it("падение с нескольких строк до одной → счётчик остаётся и печатается, выделен тоном", () => {
    // `rowCountChanged=true` — та же ось, что `estimateRowsChangedPerCell`
    // вычисляет клиентом (`drilldownData.ts`): без него ячейка была бы
    // выделена тоном (ниже) без единой цифры, объясняющей выделение.
    renderCell(cell({ quantity: "248", quantity_unit: "м²", estimate_rows: 1 }), true);
    const tier = screen.getByTestId("cell-quantity");
    expect(tier).toHaveTextContent("1 стр. · 248 м²");
    expect(tier).toHaveClass("text-warning-text");
  });

  it("ячейка `absent` — по-прежнему без этажа вовсе, правило её не касается", () => {
    renderCell(cell({ state: "absent", amount: null, quantity: null, quantity_unit: null, estimate_rows: 0 }), true);
    expect(screen.queryByTestId("cell-quantity")).toBeNull();
  });
});
