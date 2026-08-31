import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sampleStagePositions, sampleStageSummary } from "@/test/fixtures";
import { formatDecimalMoney, roundDecimalPercent } from "@/lib/format";
import { useStagePositions } from "@/services/queries";
import type { StageSummary, StageSummaryCell, StageSummaryChange, StageSummaryRow, StageSummaryTotalCell } from "@/types/domain";
import { KIND_LABEL, REASON_LABEL, STATE_LABEL } from "./cellCopy";
import { drilldownGroupCount } from "./drilldownData";
import { worksHeading } from "./drilldownCopy";
import { StageSummaryTable } from "./StageSummaryTable";
import { ChangeBadge, SummaryCell, SummaryTotalCell } from "./SummaryCell";

/**
 * Мок `useStagePositions` — тем же приёмом, что `PositionDrilldown.test.tsx`
 * (частичный `importActual`, чтобы не терять `apiErrorCode`): `StageSummaryTable`
 * монтирует `PositionDrilldown` под своей кнопкой «Работы», и её тесты этого
 * файла (задача 11, ниже) настраивают мок сами; остальные тесты кнопку не
 * нажимают, и мок для них молчит.
 */
vi.mock("@/services/queries", async (importActual) => ({
  ...(await importActual<object>()),
  useStagePositions: vi.fn(),
}));
const mockedUseStagePositions = vi.mocked(useStagePositions);

/**
 * Пропсы, которых требует `StageSummaryTable` для запроса разложения
 * (задача 11 плана: `tenderId`, `offerIds`) — механически на КАЖДОМ рендере
 * файла, включая тесты, не имеющие отношения к кнопке «Работы»: пропсы не
 * сделаны опциональными нарочно (см. бриф задачи), а `sampleStageSummary`
 * несёт `tender.id = 300` и участник с `offer_id` 7001/7002/7004 — значения
 * здесь те же, что у страницы в проде.
 */
const TABLE_PROPS = { tenderId: 300, offerIds: [7001, 7002] };

/**
 * Таблица свода — состояния рисуются ПО ДАННЫМ, а не выводятся клиентом из
 * других полей (спека 2026-08-27-stage-summary-design.md §2.5–§2.7, §2.9,
 * §2.13; Global 13). Числа и порядок строк — из `sampleStageSummary`
 * (`frontend/src/test/fixtures.ts`), а не из брифа задачи: фикстура прошла
 * инвариантный набор `fixtures.test.ts` и она источник истины.
 */
describe("Таблица свода — состояния по данным (спека §2.5–§2.7, §2.9, §2.13)", () => {
  it("порядок строк — как пришёл с сервера; «Нераспределённое» и «Итого» в tfoot", () => {
    /*
      Вход РАЗВЁРНУТ относительно фикстуры намеренно. В `sampleStageSummary`
      порядок «Котлован» → «Фасадные работы» совпадает у четырёх разных правил
      сразу: как пришло, по коду («2» < «6»), по убыванию модуля вклада
      (60 > 30) и по алфавиту («К» < «Ф»), — то есть на исходной фикстуре тест
      был бы зелёным при любом из них и не проверял бы ничего
      (`docs/insights/verifying-guards.md`, слой 12: ложную зелень создаёт
      выбор чисел). Переставленные строки не даёт ни одно из трёх сортирующих
      правил, поэтому совпадение с массивом здесь означает ровно то, о чём
      тест: клиент не пересортировывает.

      Сам порядок задаёт сервер по `sort_order` классификатора и по телу
      ответа не наблюдаем вовсе (`sort_order` в контракте нет) — правило
      стережёт бэкендовый тест
      `test_rows_sorted_by_classifier_sort_order_within_level`, граница
      записана в `fixtures.test.ts` на месте снятого инварианта порядка.
    */
    const reversed: StageSummary = {
      ...sampleStageSummary,
      rows: [...sampleStageSummary.rows].reverse(),
    };
    expect(reversed.rows[0].code).toBe("6");
    expect(reversed.rows[1].code).toBe("2");

    render(<StageSummaryTable summary={reversed} {...TABLE_PROPS} />);
    const bodyRows = within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row");
    expect(bodyRows[0]).toHaveTextContent("Фасадные работы");
    expect(bodyRows[1]).toHaveTextContent("Котлован");
    const foot = within(screen.getAllByRole("rowgroup")[2]).getAllByRole("row");
    expect(foot[0]).toHaveTextContent("Нераспределённое");
    expect(foot[1]).toHaveTextContent("Итого по предложению");
  });

  /**
   * Зажим ширины колонки классификатора — восстановление ограничения макета
   * гейта 1 (`table.pass`, `th.art, td.t`: `min-width:250px; max-width:420px`),
   * потерянного реализацией. Ставится на ВСЕ ячейки первой колонки: ширину
   * колонки в авторазметке диктует самая широкая из них.
   *
   * ГРАНИЦА НАБЛЮДАЕМОСТИ (`docs/insights/unobservable-in-the-runner.md`):
   * jsdom раскладку не считает, поэтому здесь проверяется только ТО, ЧЕМ
   * раскладка запрошена. Что из этого вышло, проверяет замер в браузере
   * (devlog §9.6): при раскрытии статей колонка перестала расти, а «Торг» и
   * «Вклад в итог» перестали уезжать за край.
   */
  it("колонка классификатора зажата по ширине и переносит наименование — на всех ячейках первой колонки, включая раскрытого ребёнка", async () => {
    const user = userEvent.setup();
    const { container } = render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);

    // Раскрываем статью: дефект был именно в раскрытом состоянии, и ячейка
    // ребёнка (`pl-8`) до раскрытия не рендерится вовсе.
    await user.click(screen.getByRole("button", { name: /Раскрыть/ }));

    const firstCells = Array.from(container.querySelectorAll("table tr > *:first-child"));
    // Точное число, а не «не меньше»: шапка + два корня + раскрытый ребёнок +
    // «Нераспределённое» + «Итого». С `>=` молча сузившийся селектор оставил бы
    // тест зелёным, проверив меньше ячеек, чем он обещает.
    expect(firstCells).toHaveLength(6);
    for (const cell of firstCells) {
      expect(cell).toHaveClass("min-w-[250px]");
      expect(cell).toHaveClass("max-w-[420px]");
      // Перенос: примитив shadcn несёт `whitespace-nowrap`, и зажим без
      // переноса кладёт наименование поверх соседней колонки — хуже, чем его
      // отсутствие. Проверяются ОБА направления: свой класс стоит И чужой снят.
      // Второе утверждение не лишнее: `whitespace-nowrap` сегодня выбрасывает
      // `twMerge` внутри `cn`, и без этой строки замена `cn` на `clsx` вернула
      // бы дефект целиком при зелёном тесте.
      expect(cell).toHaveClass("whitespace-normal");
      expect(cell).not.toHaveClass("whitespace-nowrap");
      // Подпись строки — по верхней строке, как её числа: она бывает в две-три
      // строки, и центрирование увело бы её с линии собственных сумм.
      expect(cell).toHaveClass("align-top");
    }

    // Разрыв внутри длинного слова — третья половина той же работы, и у неё
    // свой носитель: `break-words` на самом наименовании и `min-w-0`, без
    // которого флекс-элемент не сузится.
    const titles = Array.from(container.querySelectorAll("table tbody tr > td:first-child > div > span:last-child"));
    expect(titles.length).toBeGreaterThan(0);
    for (const title of titles) {
      expect(title).toHaveClass("break-words");
      expect(title).toHaveClass("min-w-0");
    }
  });

  it.each([
    ["removed", "снято"],
    ["not_evaluated", "не оценивалась"],
    ["absent", "—"],
  ])("state=%s рисует «%s» — по полю, а не по сумме", (state, label) => {
    // amount=999.00 намеренно противоречит state: если бы рендер выводил
    // состояние из суммы, тест поймал бы это здесь.
    const cell = { ...sampleStageSummary.rows[0].cells[0], state, amount: "999.00" } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveTextContent(label);
    expect(screen.getByRole("cell")).not.toHaveTextContent("999");
  });

  /**
   * Fix round 3, п.2: макет рисует «отсутствует» плоским прочерком, а
   * «снято»/«не оценивалась» — пилюлями (то и другое остаётся пилюлями); эта
   * ветка проверяет ФАКТ РАЗМЕТКИ — какой элемент отрисован, что наблюдаемо в
   * прогоне — а не саму визуальную приглушённость прочерка: та проверяется
   * замером в браузере, не здесь (`docs/insights/unobservable-in-the-runner.md`).
   */
  it("state=absent рисует плоский прочерк (не пилюлю); removed/not_evaluated остаются пилюлями", () => {
    const dashCell = { ...sampleStageSummary.rows[0].cells[0], state: "absent", amount: null } as StageSummaryCell;
    const { unmount: unmountDash } = render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={dashCell} />
          </tr>
        </tbody>
      </table>
    );
    const dash = screen.getByTestId("cell-dash");
    expect(dash).toHaveTextContent("—");
    // Пилюля (`StatusPill`) несёт `rounded-full`/`border` безусловно — у
    // плоского прочерка их нет: разные элементы, а не один перекрашенный.
    expect(dash).not.toHaveClass("rounded-full");
    unmountDash();

    for (const state of ["removed", "not_evaluated"] as const) {
      const pillCell = { ...sampleStageSummary.rows[0].cells[0], state, amount: null } as StageSummaryCell;
      const { unmount } = render(
        <table>
          <tbody>
            <tr>
              <SummaryCell cell={pillCell} />
            </tr>
          </tbody>
        </table>
      );
      expect(screen.queryByTestId("cell-dash")).not.toBeInTheDocument();
      expect(screen.getByRole("cell").querySelector(".rounded-full")).not.toBeNull();
      unmount();
    }
  });

  /**
   * Дефект внешнего ревью PR: `amount_unavailable_reason` тестировался ДО
   * `state`, и в колонке с неизвестной базой НДС «снято»/«не оценивалась»/
   * «отсутствует» подменялись подписью «нет базы НДС» — состояние ячейки
   * пропадало. Спека §2.5, §2.8 и AGENTS.md §10 сходятся: неизвестная база
   * гасит ТОЛЬКО показанную сумму, состояние ячейки не зависит от ставки НДС
   * и рисуется как обычно. У `removed`/`not_evaluated`/`absent` числа и так не
   * было — подписи «нет базы НДС» рядом с их пилюлей/прочерком макет не
   * показывает ни для одного состояния, поэтому она не выводится вовсе (у
   * `<td>` остаётся `title` с причиной — тот же механизм, что уже был).
   */
  it.each([
    ["removed", "снято"],
    ["not_evaluated", "не оценивалась"],
    ["absent", "—"],
  ])(
    "amount_unavailable_reason не гасит state=%s — остаётся «%s», а не «нет базы НДС»",
    (state, label) => {
      const cell = {
        ...sampleStageSummary.rows[0].cells[0],
        state,
        amount: null,
        amount_unavailable_reason: "unknown_vat_base",
      } as StageSummaryCell;
      render(
        <table>
          <tbody>
            <tr>
              <SummaryCell cell={cell} />
            </tr>
          </tbody>
        </table>
      );
      expect(screen.getByRole("cell")).toHaveTextContent(label);
      expect(screen.getByRole("cell")).not.toHaveTextContent("нет базы НДС");
    }
  );

  it("amount_unavailable_reason гасит сумму ТОЛЬКО у state=amount — «нет базы НДС» вместо числа", () => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[0],
      state: "amount",
      amount: "999.00",
      amount_unavailable_reason: "unknown_vat_base",
    } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveTextContent("нет базы НДС");
    expect(screen.getByRole("cell")).not.toHaveTextContent("999");
  });

  /**
   * Дефект, из-за которого затеяна ревизия §2.16 (внешнее ревью PR #34):
   * итог одной из колонок сходился в ноль при живых строках, но прежний тип
   * («Итого» = `Cell`) публиковал вместе с суммой "0.00" состояние «снято» —
   * агрегату состояние неприменимо по смыслу, сумма нулей равна нулю, а не
   * «неизвестна». `SummaryTotalCell` не несёт `state` вовсе, поэтому нулевой
   * итог печатается ЧИСЛОМ безусловно — тест проверяет ровно это, а не то,
   * что раньше падало молча.
   */
  it("SummaryTotalCell: нулевой итог печатается числом, а не пилюлей «снято» (§2.16, PR #34)", () => {
    const zeroTotal: StageSummaryTotalCell = {
      amount: "0.00",
      amount_unavailable_reason: null,
      rows: { row_count: 3, rows_with_amount: 3, rows_not_finite: 0 },
      change: { kind: "abs_only", value: "0.00", direction: "flat", reason: null },
    };
    render(
      <table>
        <tbody>
          <tr>
            <SummaryTotalCell cell={zeroTotal} />
          </tr>
        </tbody>
      </table>
    );
    const cell = screen.getByRole("cell");
    expect(cell).toHaveTextContent(formatDecimalMoney("0.00").replace(/\s+/g, " "));
    expect(cell).not.toHaveTextContent("снято");
    // Пилюля состояния (`StatusPill`) несёт `rounded-full` безусловно — у
    // числа его нет: разные элементы, а не текст, перекрашенный поверх пилюли.
    expect(cell.querySelector(".rounded-full")).toBeNull();
  });

  it.each([
    ["appeared", "появилась"],
    ["reappeared", "вернулась"],
    ["disappeared", "нет в файле"],
    ["removed", "снято"],
  ])("change.kind=%s подписан «%s»", (kind, label) => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[1],
      state: "amount",
      amount: "5.00",
      change: { kind, value: null, direction: null, reason: null },
    } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveTextContent(label);
  });

  /**
   * Найдено пользователем на стенде 28.08.2026: ячейка, где статью сняли на
   * этом шаге, показывала «снято» ДВАЖДЫ — пилюлей состояния и пилюлей вида
   * изменения. Макет гейта 1 в такой ячейке рисует одну.
   *
   * `sampleStageSummary.rows[0].cells[1]` — ровно этот вход: `state =
   * "removed"` и `change.kind = "removed"` (проверено первым утверждением,
   * иначе тест мог бы зеленеть на ячейке, где повторяться нечему).
   */
  it("состояние и вид изменения не печатают одно слово дважды: «снято» в ячейке ровно один раз", () => {
    const cell = sampleStageSummary.rows[0].cells[1];
    expect(cell.state).toBe("removed");
    expect(cell.change.kind).toBe("removed");

    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    const text = screen.getByRole("cell").textContent ?? "";
    expect(text.match(/снято/g)).toHaveLength(1);
    expect(screen.queryByTestId("change")).toBeNull();
  });

  /**
   * Вторая половина того же правила: значок гасится ТОЛЬКО когда повторяет
   * слово состояния, а не всегда, когда состояние есть. Иначе правка съела бы
   * настоящие изменения (`docs/insights/state-the-rule-as-an-equivalence.md`).
   * `rows[1].children[0].cells[2]` — `state = "absent"` с
   * `change.kind = "disappeared"`: подписи разные («—» и «нет в файле»), и обе
   * обязаны стоять.
   */
  it("значок гасится только при совпадении слова: absent + disappeared показывает и прочерк, и «нет в файле»", () => {
    const cell = sampleStageSummary.rows[1].children[0].cells[2];
    expect(cell.state).toBe("absent");
    expect(cell.change.kind).toBe("disappeared");

    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByTestId("cell-dash")).toBeInTheDocument();
    expect(screen.getByTestId("change")).toHaveTextContent(KIND_LABEL.disappeared);
  });

  /**
   * Вертикальное выравнивание ячейки этапа (найдено пользователем на стенде
   * 28.08.2026: пилюли прилипали к верху, тогда как те же пилюли в «Торге» и
   * «Вкладе» стояли по центру).
   *
   * Правило проверяется В ОБЕ СТОРОНЫ одним набором: число остаётся на верхней
   * строке (иначе суммы этапов перестали бы читаться строкой поперёк таблицы —
   * у первой колонки изменения нет по построению, и центрирование увело бы её
   * вниз в КАЖДОЙ строке), всё остальное — по центру.
   *
   * ГРАНИЦА НАБЛЮДАЕМОСТИ: jsdom раскладку не считает, здесь проверяется только
   * запрошенное выравнивание. Что из него вышло, проверил замер в браузере
   * (devlog §9.7): суммы одной строки на одной линии во всех 113 строках,
   * содержимое 60 нечисловых ячеек — по центру строки.
   */
  it.each([
    ["сумма", { state: "amount", amount: "5.00", amount_unavailable_reason: null }, "align-top"],
    ["снято", { state: "removed", amount: null, amount_unavailable_reason: null }, "align-middle"],
    ["не оценивалась", { state: "not_evaluated", amount: null, amount_unavailable_reason: null }, "align-middle"],
    ["прочерк (статьи нет в файле)", { state: "absent", amount: null, amount_unavailable_reason: null }, "align-middle"],
    ["сумма без базы НДС", { state: "amount", amount: null, amount_unavailable_reason: "unknown_vat_base" }, "align-middle"],
  ])("ячейка «%s» выравнивается по %s", (_name, patch, expected) => {
    const cell = { ...sampleStageSummary.rows[0].cells[0], ...patch } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    const td = screen.getByRole("cell");
    expect(td).toHaveClass(expected);
    expect(td).not.toHaveClass(expected === "align-top" ? "align-middle" : "align-top");
  });

  /**
   * Сверка ТАБЛИЦЫ СТАТЕЙ макета гейта 1 с реализацией одним проходом
   * (28.08.2026): вычисленные стили макета сняты браузером и сопоставлены со
   * стилями страницы свойство за свойством. Здесь — девять правил, которые
   * реализация потеряла; остальные расхождения оказались подменой макета на
   * примитивы и токены проекта и записаны в devlog §9.9 как осознанные. Что
   * НЕ сверялось (трасса, KPI, вторая таблица макета, цвета) — там же.
   *
   * ГРАНИЦА НАБЛЮДАЕМОСТИ: jsdom не считает стилей, здесь проверяется только
   * запрошенное. Напарник — замер: вес корня 600 против 400 у ребёнка; фон
   * строки ребёнка и строки «Итого» совпал с макетом до значения
   * `rgb(247, 246, 242)`, «Нераспределённое» — белое, как в макете; шапка
   * переносится и набрана 600; разделитель закреплённой колонки 1 px.
   */
  it("восстановленные правила макета: веса, подложки, перенос шапки, табличные цифры, разделитель", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    await user.click(screen.getByRole("button", { name: /Раскрыть/ }));

    const bodyRows = within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row");
    const root = bodyRows[0];
    // Ребёнок опознаётся по ОТСТУПУ, а не по проверяемой же подложке: иначе
    // снятие подложки просто перестало бы находить строку, и тест молчал бы.
    const child = bodyRows.find((r) => r.querySelector(".pl-8"));
    expect(child).toBeDefined();

    expect(root).toHaveClass("font-semibold");
    expect(child).not.toHaveClass("font-semibold");
    expect(child).toHaveClass("bg-surface-sunken");

    const headers = within(screen.getAllByRole("rowgroup")[0]).getAllByRole("columnheader");
    for (const th of headers) {
      expect(th).toHaveClass("font-semibold");
      expect(th).toHaveClass("whitespace-normal");
    }
    // Обратная сторона переноса: числовые ячейки ТЕЛА переносить нельзя —
    // именно `whitespace-normal` на них вернул бы дефект с зажимом ширины.
    for (const cell of Array.from(root.children).slice(1)) {
      expect(cell).toHaveClass("whitespace-nowrap");
    }

    // Табличные цифры — во ВСЕХ числовых колонках, включая обе служебные строки.
    expect(root.children[1]).toHaveClass("tabular-nums");
    expect(screen.getAllByTestId("bargain-cell")[0]).toHaveClass("tabular-nums");
    expect(screen.getAllByTestId("contribution-cell")[0]).toHaveClass("tabular-nums");

    // Подвал: «Итого» полужирная и тонированная, «Нераспределённое» — обычная и
    // на подложке поверхности (в макете она не тонирована вовсе).
    const total = screen.getByTestId("row-total");
    const unallocated = screen.getByTestId("row-unallocated");
    expect(total).toHaveClass("font-semibold");
    expect(total).toHaveClass("bg-surface-sunken");
    expect(unallocated).toHaveClass("font-normal");
    expect(unallocated).toHaveClass("bg-surface");
    expect(unallocated).not.toHaveClass("bg-surface-sunken");

    // Разделитель закреплённой колонки — на КАЖДОЙ её ячейке, иначе линия рвётся.
    for (const cell of [headers[0], root.children[0], total.children[0], unallocated.children[0]]) {
      expect(cell).toHaveClass("border-r");
    }

    // Подписи под числом не толстеют вместе со строкой (макет: `.dp`, `.conv` — 400).
    const changeInRoot = within(root).getAllByTestId("change")[0];
    expect(changeInRoot.closest(".font-normal")).not.toBeNull();
    const convergence = screen.getAllByTestId("convergence")[0];
    // Сначала убеждаемся, что узел ЕСТЬ: `?.` на отсутствующем узле дал бы
    // `undefined`, а `expect(undefined).not.toBeNull()` проходит — защита
    // испарилась бы ровно тогда, когда её надо сорвать.
    expect(convergence).toBeInTheDocument();
    expect(convergence.closest(".font-normal")).not.toBeNull();
  });

  /**
   * Строка изменения под суммой — МЕЛЬЧЕ самой суммы, как на макете гейта 1
   * (`.dp { font-size: 11px }` при 13 px у таблицы); реализация это потеряла, и
   * процент читался таким же крупным, как сумма над ним (просьба пользователя
   * 28.08.2026 совпала с макетом).
   *
   * Проверяется В ОБЕ СТОРОНЫ: мельче становится ВТОРАЯ строка ячейки, а
   * значение колонки «Торг» — её собственное, а не подпись к чему-то — размер
   * сохраняет.
   *
   * ГРАНИЦА НАБЛЮДАЕМОСТИ: jsdom не считает вычисленные стили, здесь
   * проверяется только запрошенный класс. Числа — в замере (devlog §9.8):
   * сумма 13 px, все 46 строк изменения 11 px, «Торг» 13 px.
   */
  it("строка изменения под суммой мельче суммы, значение «Торга» — нет", () => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[0],
      state: "amount",
      amount: "5.00",
      amount_unavailable_reason: null,
      change: { kind: "percent", value: "-5.0", direction: "down", reason: null },
    } as StageSummaryCell;
    const { unmount } = render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    // `closest`, а не `parentElement`: размер задаёт ЛЮБОЙ предок, и проверка
    // одного уровня пропустила бы обёртку этажом выше или ниже. Это не
    // придирка — первая редакция теста проверяла родителя и осталась зелёной,
    // когда проба обернула значок «Торга» в `text-2xs`.
    expect(screen.getByTestId("change").closest(".text-2xs")).not.toBeNull();
    unmount();

    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    const bargain = screen.getAllByTestId("bargain-cell")[0];
    expect(within(bargain).getByTestId("change").closest(".text-2xs")).toBeNull();
  });

  /**
   * Правило одно на всю СТРОКУ, а не на колонку ячеек этапа: «Торг», «Вклад в
   * итог» и колонка классификатора живут по нему же. До правки они
   * центрировались примитивом таблицы безусловно, и число «Вклада» стояло на
   * полстроки ниже сумм этапов той же строки — тот самый разнобой, с которого
   * началась правка, только в другой колонке
   * (`docs/insights/state-the-rule-as-an-equivalence.md`: правило, применённое
   * к половине поверхности, ловит половину дефектов).
   */
  it("«Торг» и «Вклад в итог» подчиняются тому же правилу: число по верху, пилюля и прочерк по центру", () => {
    const summary: StageSummary = structuredClone(sampleStageSummary);
    // Строка 0: «Торг» пилюлей, вклад числом. Строка 1: «Торг» процентом, вклад пустой.
    summary.rows[0].bargain = { kind: "removed", value: null, direction: null, reason: null };
    summary.rows[0].contribution = { value: "-60.00", direction: "down", reason: null };
    summary.rows[1].bargain = { kind: "percent", value: "-25.0", direction: "down", reason: null };
    summary.rows[1].contribution = { value: null, direction: null, reason: "absent_endpoint" };

    render(<StageSummaryTable summary={summary} {...TABLE_PROPS} />);
    const bargain = screen.getAllByTestId("bargain-cell");
    const contribution = screen.getAllByTestId("contribution-cell");
    expect(bargain[0]).toHaveClass("align-middle");   // пилюля «снято»
    expect(bargain[1]).toHaveClass("align-top");      // процент
    expect(contribution[0]).toHaveClass("align-top"); // сумма вклада
    expect(contribution[1]).toHaveClass("align-middle"); // прочерк
  });

  it("ячейка «Итого» — то же правило: число по верху, погашенная сумма по центру", () => {
    const base = sampleStageSummary.total.cells[0];
    const { rerender } = render(
      <table>
        <tbody>
          <tr>
            <SummaryTotalCell cell={{ ...base, amount_unavailable_reason: null } as StageSummaryTotalCell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveClass("align-top");

    rerender(
      <table>
        <tbody>
          <tr>
            <SummaryTotalCell
              cell={{ ...base, amount: null, amount_unavailable_reason: "unknown_vat_base" } as StageSummaryTotalCell}
            />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveClass("align-middle");
  });

  /**
   * Колонка «Торг» при `kind = 'none'` — прочерк, а не пустота: макет гейта 1
   * печатает там литерал у статьи, которой нет ни на одном конце пути
   * (`table.pass`, статьи «15» и «99»). Рядом с «Торгом» состояния нет, и пустой
   * слот читается как несчитанное значение — тот же довод, что у KPI и трассы.
   *
   * Вход построен правкой фикстуры, а не взят как есть: ни одна строка
   * `sampleStageSummary` не даёт `bargain.kind = 'none'`, и на ней тест был бы
   * зелёным, ничего не проверив.
   */
  it("«Торг» при kind=none рисует прочерк; у соседней строки с настоящим торгом он не появляется", () => {
    const summary: StageSummary = structuredClone(sampleStageSummary);
    summary.rows[0].bargain = { kind: "none", value: null, direction: null, reason: "no_amounts" };
    expect(summary.rows[1].bargain.kind).toBe("percent");

    render(<StageSummaryTable summary={summary} {...TABLE_PROPS} />);
    const bargainCells = screen.getAllByTestId("bargain-cell");
    expect(bargainCells[0]).toHaveTextContent(/^—$/);
    // Вторая половина: прочерк не подменил настоящее значение соседа.
    expect(bargainCells[1]).not.toHaveTextContent(/^—$/);
    expect(bargainCells[1]).toHaveTextContent("%");
  });

  /**
   * Тон «снято» — тревожный, как у одноимённого состояния ячейки и как на
   * макете (`pill warn` и в ячейке этапа, и в «Торге»). Остальные структурные
   * виды остаются нейтральными: макет печатает их обычной пилюлей.
   */
  it("«снято» в «Торге» окрашено тоном состояния, а «появилась» — нет", () => {
    const summary: StageSummary = structuredClone(sampleStageSummary);
    summary.rows[0].bargain = { kind: "removed", value: null, direction: null, reason: null };
    summary.rows[1].bargain = { kind: "appeared", value: null, direction: null, reason: null };

    render(<StageSummaryTable summary={summary} {...TABLE_PROPS} />);
    const cells = screen.getAllByTestId("bargain-cell");
    expect(cells[0].querySelector(".bg-warning-soft")).not.toBeNull();
    expect(cells[1].querySelector(".bg-warning-soft")).toBeNull();
    expect(cells[1].querySelector(".bg-neutral-soft")).not.toBeNull();
  });

  /**
   * Правило написано через сами словари (`changeRepeatsState` в `SummaryCell`),
   * поэтому оно ровно настолько верно, насколько верно допущение о словарях:
   * совпадение подписи состояния и подписи вида — ОДНО, «снято». Если завтра
   * `cellCopy.ts` переименует «нет в файле» в «—», гашение молча съест значок
   * там, где он нужен, — этот тест покраснеет раньше.
   */
  it("в словарях ровно одно совпадение подписи состояния и подписи вида изменения — removed/removed", () => {
    const collisions: string[] = [];
    for (const [state, stateLabel] of Object.entries(STATE_LABEL)) {
      for (const [kind, kindLabel] of Object.entries(KIND_LABEL)) {
        if (stateLabel === kindLabel) collisions.push(`${state}/${kind}`);
      }
    }
    expect(collisions).toEqual(["removed/removed"]);
  });

  it.each([
    [{ kind: "percent", value: "-50.0", direction: "down", reason: null }, /-50,0%/],
    [{ kind: "abs_only", value: "-20.00", direction: "down", reason: null }, /20,00.*Δ, без %/],
    [{ kind: "disappeared", value: null, direction: null, reason: null }, /нет в файле/],
    [{ kind: "none", value: null, direction: null, reason: "unknown_vat_base" }, /^—$/],
  ])("ChangeBadge исчерпывающе рисует %o (тот же компонент в KPI «Последний к первому»)", (change, expected) => {
    render(<ChangeBadge change={change as StageSummaryChange} dashOnNone />);
    expect(screen.getByTestId("change")).toHaveTextContent(expected);
  });

  it("direction трёх состояний: up/down/flat — знак и тон, flat не окрашен как рост", () => {
    // text-accent-text/text-danger-text/text-fg-tertiary — токены, которые
    // РЕАЛЬНО существуют в теме (frontend/src/index.css, alias --color-*
    // из @theme inline). "text-accent-primary-text" из брифа — несуществующая
    // утилита: Tailwind не находит `--color-accent-primary-text` и не
        // генерирует CSS вовсе, поэтому дефект был бы невидим и типам, и линту,
      // и этому тесту, если бы имя осталось таким.
    for (const [direction, value, cls] of [
      ["up", "+5.0", "text-accent-text"],
      ["down", "-5.0", "text-danger-text"],
      ["flat", "0.0", "text-fg-tertiary"],
    ] as const) {
      const cell = {
        ...sampleStageSummary.rows[0].cells[1],
        change: { kind: "percent", value, direction, reason: null },
      } as StageSummaryCell;
      const { unmount } = render(
        <table>
          <tbody>
            <tr>
              <SummaryCell cell={cell} />
            </tr>
          </tbody>
        </table>
      );
      expect(screen.getByTestId("change")).toHaveClass(cls);
      unmount();
    }
  });

  it("неполнота: значок и подсказка «учтено X из N строк», отдельно неконечные", () => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[0],
      rows: { row_count: 40, rows_with_amount: 37, rows_not_finite: 1 },
    };
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByLabelText(/учтено 37 из 40 строк.*неконечных значений: 1/)).toBeInTheDocument();
  });

  it("сходимость трёх состояний в «Итого» — свой текст И свой тон у каждого (fix round 2, п.4)", () => {
    const s = structuredClone(sampleStageSummary);
    s.columns[1].convergence = { ...s.columns[1].convergence, converged: false, delta: "-10.00" };
    s.columns[2].convergence = {
      categories_sum: "90.00",
      file_total: null,
      converged: null,
      delta: null,
      reason: "file_total_unavailable",
    };
    render(<StageSummaryTable summary={s} {...TABLE_PROPS} />);
    const total = screen.getByText("Итого по предложению").closest("tr") as HTMLElement;
    const convergenceSpans = within(total).getAllByTestId("convergence");
    expect(convergenceSpans).toHaveLength(3);

    expect(convergenceSpans[0]).toHaveTextContent("сходится");
    expect(convergenceSpans[0]).toHaveClass("text-fg-tertiary");

    // Δ печатается настоящим форматом formatDecimalMoney (дефис ASCII, а не
    // типографский минус) — иначе тест зафиксировал бы символ, которого
    // функция форматирования не производит. Расхождение — дефект данных
    // (спека), поэтому тревожный тон, а не тот же тихий, что у «сходится».
    expect(convergenceSpans[1]).toHaveTextContent("не сходится: Δ -10,00");
    expect(convergenceSpans[1]).toHaveClass("text-warning-text");

    // Невозможность сверки — отсутствие файлового итога, а не расхождение:
    // СВОЙ нейтральный тон, отличный от обоих предыдущих.
    expect(convergenceSpans[2]).toHaveTextContent("сверка невозможна: итог файла не единогласен");
    expect(convergenceSpans[2]).toHaveClass("text-neutral-text");

    // Три тона должны быть попарно различны — иначе «различимость» была бы
    // утверждением на глаз, а не проверенным фактом.
    const classes = convergenceSpans.map((el) => el.className);
    expect(new Set(classes).size).toBe(3);
  });

  it("раскрытие статьи показывает детей и помечается aria-expanded", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    const toggle = screen.getByRole("button", { name: /Раскрыть Фасадные работы/ });
    expect(screen.queryByText("Прочее (фасады)")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Прочее (фасады)")).toBeInTheDocument();
  });

  it("подпись разноса под шапкой колонки", () => {
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    expect(screen.getByText(/разнос: 1 решение · 26\.08\.2026/)).toBeInTheDocument();
    // Колонки 1 и 4 (индексы 0 и 2) без ручного разноса; колонка 2 (индекс 1) —
    // единственная с решением (sampleStageSummary.columns[1].manual_overrides).
    expect(screen.getAllByText(/без ручного разноса/)).toHaveLength(2);
  });

  it("«Нераспределённое»: бейдж и «без %» вместо процента — не читается как уступка", () => {
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    const unallocated = screen.getByText("Нераспределённое").closest("tr") as HTMLElement;
    expect(unallocated).toHaveTextContent("обязательная строка");
    expect(unallocated).toHaveTextContent("без %");
  });

  /**
   * Fix round 2, п.3: пилюля «обязательная строка» несёт на макете пояснение
   * («Разделы без статьи классификатора…») в `title` — общий `StatusPill` не
   * принимает и не прокидывает `title`, поэтому пояснение стоит на обёртке
   * вокруг пилюли (`aria-describedby` → `sr-only`-текст), тем же приёмом, что
   * недоступная плитка решётки тендера (`OfferGrid.tsx`). Проверяется, что
   * пояснение ДОСТИЖИМО программно (через id, а не только через наведение
   * курсора, которое в jsdom не наблюдаемо вовсе).
   */
  it("пилюля «обязательная строка» несёт пояснение, достижимое скринридером, не только курсором", () => {
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    const pill = screen.getByText("обязательная строка");
    const describedHost = pill.closest("[aria-describedby]") as HTMLElement;
    expect(describedHost).not.toBeNull();
    const describedId = describedHost.getAttribute("aria-describedby") as string;
    const explanation = document.getElementById(describedId);
    expect(explanation).not.toBeNull();
    expect(explanation).toHaveTextContent(/Разделы без статьи классификатора/);
  });

  /**
   * Fix round 2, п.2: макет печатает код статьи классификатора ПЕРЕД
   * названием на каждой строке (`table.pass`, `<span class="code">`); ответ
   * несёт `row.code`, но страница его не рисовала — расхождение с
   * согласованным макетом без письменного долга запрещено правилом проекта.
   * Проверяются коды на трёх уровнях: корень («Котлован»/«Фасадные работы»),
   * ребёнок («Прочее (фасады)») и «Нераспределённое» (код-прочерк на макете).
   */
  it("код статьи классификатора печатается перед названием, как на макете", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);

    const kotlovan = sampleStageSummary.rows[0];
    const facades = sampleStageSummary.rows[1];
    expect(kotlovan.code).not.toBeNull();
    expect(facades.code).not.toBeNull();

    const kotlovanRow = screen.getByText(kotlovan.title).closest("tr") as HTMLElement;
    expect(within(kotlovanRow).getByTestId("row-code")).toHaveTextContent(kotlovan.code as string);
    const facadesRow = screen.getByText(facades.title).closest("tr") as HTMLElement;
    expect(within(facadesRow).getByTestId("row-code")).toHaveTextContent(facades.code as string);

    await user.click(screen.getByRole("button", { name: new RegExp(`Раскрыть ${facades.title}`) }));
    const child = facades.children[0];
    expect(child.code).not.toBeNull();
    const childRow = screen.getByText(child.title).closest("tr") as HTMLElement;
    expect(within(childRow).getByTestId("row-code")).toHaveTextContent(child.code as string);

    // «Нераспределённое» не несёт кода классификатора (`code: null`), но
    // ячейка кода на его строке всё равно есть — как на макете («—»).
    expect(sampleStageSummary.unallocated.code).toBeNull();
    const unallocatedRow = screen.getByText("Нераспределённое").closest("tr") as HTMLElement;
    expect(within(unallocatedRow).getByTestId("row-code")).toHaveTextContent("—");
  });

  /**
   * Обычная строка: «Торг» и «Вклад» — две правые колонки, ради которых
   * существует страница. Управляется `sampleStageSummary.rows[1]` («Фасадные
   * работы»): её `bargain` — процент со знаком и направлением `down`, её
   * `contribution` — сумма с тем же направлением. Ожидания читаются из самой
   * ячейки фикстуры (`roundDecimalPercent`/`formatDecimalMoney` — те же
   * форматтеры, что используют `ChangeBadge`/`ContributionValue`), а не
   * переписаны литералом — тест не отстанет, если проценты в фикстуре
   * поменяются.
   */
  it("Торг и Вклад обычной строки — знак, число и тон по направлению (rows[1] «Фасадные работы»)", () => {
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    const row = sampleStageSummary.rows[1];
    expect(row.bargain.kind).toBe("percent");
    expect(row.bargain.direction).toBe("down");
    expect(row.contribution.direction).toBe("down");

    const tr = screen.getByText(row.title).closest("tr") as HTMLElement;
    const bargainCell = within(tr).getByTestId("bargain-cell");
    const contributionCell = within(tr).getByTestId("contribution-cell");

    const expectedBargainText = roundDecimalPercent(row.bargain.value)!.text;
    expect(bargainCell).toHaveTextContent(expectedBargainText);
    // down → тот же тон, что «направление трёх состояний» уже закрепил для
    // ChangeBadge в ячейках — здесь проверяется, что «Торг» вызывает тот же
    // компонент, а не рисует цвет своей веткой.
    expect(within(bargainCell).getByTestId("change")).toHaveClass("text-danger-text");

    // toHaveTextContent normalizes the ELEMENT's text (NBSP → obычный пробел),
    // но не нормализует переданную строку: formatDecimalMoney вставляет НЕразрывный
    // пробел перед «₽», и байт-в-байт сравнение с ним провалилось бы даже при
    // видимо одинаковом тексте — нормализуем ожидание тем же правилом (`\s+` → " ").
    const expectedContributionText = formatDecimalMoney(row.contribution.value).replace(/\s+/g, " ");
    expect(contributionCell).toHaveTextContent(expectedContributionText);
    expect(within(contributionCell).getByTestId("contribution-value")).toHaveClass("text-danger-text");
  });

  /**
   * Ребёнок со статьёй, исчезнувшей из файла на последнем этапе: управляется
   * `sampleStageSummary.rows[1].children[0]` («Прочее (фасады)»). `bargain.kind
   * === "disappeared"` — структурная подпись (`KIND_LABEL`), не число; у
   * `contribution` `value === null` с `reason === "absent_endpoint"` — причина,
   * не ноль. Родителя нужно раскрыть, иначе строки ребёнка нет в DOM.
   */
  it("Торг и Вклад ребёнка со снятой статьёй — структурная подпись и причина, не число/ноль (rows[1].children[0] «Прочее (фасады)»)", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} />);
    const parent = sampleStageSummary.rows[1];
    const child = parent.children[0];
    expect(child.bargain.kind).toBe("disappeared");
    expect(child.contribution.value).toBeNull();
    expect(child.contribution.reason).not.toBeNull();

    await user.click(screen.getByRole("button", { name: new RegExp(`Раскрыть ${parent.title}`) }));

    const tr = screen.getByText(child.title).closest("tr") as HTMLElement;
    const bargainCell = within(tr).getByTestId("bargain-cell");
    const contributionCell = within(tr).getByTestId("contribution-cell");

    expect(bargainCell).toHaveTextContent(KIND_LABEL[child.bargain.kind as keyof typeof KIND_LABEL]);
    expect(bargainCell).not.toHaveTextContent(/\d/);

    // Вклад — прочерк (не «0»/«0,00»), причина названа атрибутом title:
    // тот же приём, что «Нераспределённое».bargain несёт REASON в title рядом
    // с коротким видимым «без %».
    expect(contributionCell).not.toHaveTextContent(/\d/);
    const contributionValue = within(contributionCell).getByTestId("contribution-value");
    expect(contributionValue.textContent?.trim()).toBe("—");
    expect(contributionValue).toHaveAttribute("title", REASON_LABEL[child.contribution.reason!]);
  });
});

/**
 * Кнопка «Работы · N» и вложенные раскрытия (спека
 * 2026-08-30-position-drilldown-design.md §2.1, §2.12, §6.3; задача 11 плана).
 *
 * `useStagePositions` замокан на весь файл (см. `vi.mock` выше) — сеть считает
 * тест хука самого запроса (Task 8, `queries.test.ts`); здесь проверяется
 * поведение ЭКРАНА: три ключа раскрытия (`expandedIds` — уже существующий,
 * `worksOpenIds`, `worksMountedIds`) не путают друг друга, счётчик переживает
 * сворачивание, а блок работ ПОДСТАТЬИ переживает потерю монтажа при
 * сворачивании предка.
 */
describe("Кнопка «Работы · N» и вложенные раскрытия (§2.1, §6.3)", () => {
  /**
   * Рекурсивный вариант чернового хелпера брифа: черновик патчил только
   * ВЕРХНИЙ уровень `rows`, а последнему тесту блока (мount-loss у ребёнка)
   * нужно включить `has_drilldown_rows` у ребёнка «6.99» — единственного
   * ребёнка, который фикстура уже несёт (Task 10 завёл его специально с
   * `false`, ради теста «по полю, а не по children» выше). Patch по коду на
   * любой глубине — то же дерево, что строит `CategoryRowGroup`.
   */
  function summaryWith(flags: Record<string, boolean>): StageSummary {
    function patch(rows: StageSummaryRow[]): StageSummaryRow[] {
      return rows.map((row) => ({
        ...row,
        has_drilldown_rows: flags[row.code ?? ""] ?? row.has_drilldown_rows,
        children: patch(row.children),
      }));
    }
    return { ...sampleStageSummary, rows: patch(sampleStageSummary.rows) };
  }

  function renderTable(summary: StageSummary = sampleStageSummary) {
    return render(<StageSummaryTable summary={summary} {...TABLE_PROPS} />);
  }

  /** Найти строку фикстуры по коду классификатора на любой глубине. */
  function findRowByCode(rows: StageSummaryRow[], code: string): StageSummaryRow | undefined {
    for (const row of rows) {
      if (row.code === code) return row;
      const found = findRowByCode(row.children, code);
      if (found) return found;
    }
    return undefined;
  }

  /**
   * `rowTestId(code)` — не черновой `rowIdByCode` брифа: находит строку
   * фикстуры по коду и возвращает `row-${work_category_id}`, ровно как
   * строит `data-testid` сам `CategoryRowGroup` (`row-${row.work_category_id
   * ?? row.code ?? "row"}`).
   */
  function rowTestId(code: string): string {
    const row = findRowByCode(sampleStageSummary.rows, code);
    if (!row) throw new Error(`Фикстурная строка с кодом "${code}" не найдена`);
    return `row-${row.work_category_id ?? row.code ?? "row"}`;
  }

  /** Код ПЕРВОГО ребёнка строки фикстуры с данным кодом. */
  function childCodeOf(code: string): string {
    const row = findRowByCode(sampleStageSummary.rows, code);
    const child = row?.children[0];
    if (!child?.code) throw new Error(`У строки "${code}" нет ребёнка с кодом`);
    return child.code;
  }

  function successStagePositions() {
    mockedUseStagePositions.mockReturnValue({
      isPending: false,
      isError: false,
      data: sampleStagePositions,
      refetch: vi.fn(),
    } as never);
  }

  it("кнопка рисуется по has_drilldown_rows, а не по children", () => {
    // «6» в фикстуре — с детьми; выключаем её флаг, включаем у бездетной «2»:
    // при правиле «по children» обе проверки ниже были бы красными.
    renderTable(summaryWith({ "6": false, "2": true }));
    expect(within(screen.getByTestId(rowTestId("2"))).getByRole("button", { name: /Работы/ })).toBeInTheDocument();
    expect(within(screen.getByTestId(rowTestId("6"))).queryByRole("button", { name: /Работы/ })).toBeNull();
  });

  /**
   * Раскрытие ЛЕНИВОЕ (§2.1): монтаж `PositionDrilldown`, а значит и вызов
   * `useStagePositions`, обязан ждать ПЕРВОГО клика по кнопке КОНКРЕТНОЙ
   * строки — до этого ни одна статья, включая ту, что показывает кнопку, не
   * должна посылать запрос. Проверяется ИМЕННО этот сценарий, а не просто
   * факт ненулевого счётчика вызовов: до клика утверждается «не вызван ни
   * разу», после — «вызван С work_category_id ЭТОЙ строки» (2, статья
   * «Котлован»), а не любой. Версия, монтирующая блок каждой статьи сразу
   * (по `worksId !== null`, без `worksMountedIds`), тоже дала бы ненулевой
   * счётчик после рендера — поэтому первая часть проверки обязана быть «до
   * клика вызовов нет вовсе», а не «после клика вызов один».
   */
  it("блок работ не шлёт запрос, пока статью не раскрыли хоть раз (§2.1: раскрытие ленивое)", async () => {
    const user = userEvent.setup();
    successStagePositions();
    mockedUseStagePositions.mockClear();
    renderTable();
    // До ЛЮБОГО клика — ни одна статья (включая обе с кнопкой, «2» и «6») не
    // должна была спросить разложение.
    expect(mockedUseStagePositions).not.toHaveBeenCalled();
    const row = screen.getByTestId(rowTestId("2"));
    await user.click(within(row).getByRole("button", { name: /Работы/ }));
    // После клика — вызван РОВНО с work_category_id раскрытой статьи (2), не
    // просто «вызван хоть раз»: подмена условия монтажа на «id не null»
    // (без `worksMountedIds`) тоже прошла бы проверку «вызван», но не эту.
    expect(mockedUseStagePositions).toHaveBeenCalledWith(300, 2, [7001, 7002], true);
  });

  /**
   * Отступление от чернового теста брифа: черновик искал кнопку ГЛОБАЛЬНО
   * (`screen.getByRole("button", { name: /Работы/ })`) в расчёте, что кнопка
   * будет ровно одна. Фикстура с задачи 10 несёт `has_drilldown_rows: true`
   * УЖЕ у обеих корневых строк («2» и «6») по умолчанию — глобальный запрос
   * упал бы на «found multiple elements», прежде чем дошёл бы до проверки
   * поведения. Здесь и в трёх следующих тестах кнопка ищется `within`
   * КОНКРЕТНОЙ строки (`rowTestId`), а не по всему экрану.
   */
  it("клик открывает блок; сворачивание и повторное раскрытие хук не выключают (один запрос — кэш ключа)", async () => {
    const user = userEvent.setup();
    successStagePositions();
    renderTable();
    const row = screen.getByTestId(rowTestId("2"));
    const button = within(row).getByRole("button", { name: /Работы/ });
    await user.click(button);
    expect(screen.getByText(worksHeading("2"))).toBeInTheDocument();
    await user.click(button); // свернуть
    expect(screen.queryByText(worksHeading("2"))).toBeNull();
    await user.click(button); // раскрыть снова
    // хук всё время вызывался с enabled=true — запрос не пересоздаётся,
    // данные из кэша (staleTime: Infinity, Task 8)
    expect(mockedUseStagePositions).toHaveBeenLastCalledWith(300, expect.any(Number), [7001, 7002], true);
  });

  it("после загрузки кнопка — «Работы · N», и N не пропадает при сворачивании (§2.1)", async () => {
    const user = userEvent.setup();
    successStagePositions();
    renderTable();
    const row = screen.getByTestId(rowTestId("2"));
    const button = within(row).getByRole("button", { name: /Работы/ });
    expect(button).not.toHaveTextContent("·"); // до первой загрузки счётчика нет
    await user.click(button);
    const n = drilldownGroupCount(sampleStagePositions.rows);
    expect(within(row).getByRole("button", { name: `Работы · ${n}` })).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: `Работы · ${n}` }));
    expect(within(row).getByRole("button", { name: `Работы · ${n}` })).toBeInTheDocument();
  });

  /**
   * Ревью PR #35, finding 4, на уровне экрана — где дефект реально виден
   * пользователю. `unknown_vat_base` несёт пустой `rows` не потому, что в
   * поддереве нет работ, а потому что сервер отказался их оценить (§2.8);
   * до правки кнопка после загрузки всё равно печатала «Работы · 0», ложно
   * утверждая отсутствие работ. Правильный итог — кнопка ОСТАЁТСЯ на своей
   * подписи ДО первой загрузки (без «·»), как если бы счётчик так и не
   * пришёл: родитель хранит счётчики в `Map` и не получает вызов `onCount`
   * в этом состоянии (`PositionDrilldown.tsx`).
   */
  it("reason=unknown_vat_base: кнопка остаётся на «Работы» без «· 0» даже после загрузки (§2.8)", async () => {
    const user = userEvent.setup();
    mockedUseStagePositions.mockReturnValue({
      isPending: false,
      isError: false,
      data: { ...sampleStagePositions, rows: [], convergence: [], reason: "unknown_vat_base" },
      refetch: vi.fn(),
    } as never);
    renderTable();
    const row = screen.getByTestId(rowTestId("2"));
    const button = within(row).getByRole("button", { name: /Работы/ });
    await user.click(button);
    expect(within(row).getByRole("button", { name: "Работы" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /Работы ·/ })).toBeNull();
  });

  it("шеврон корня гасит и подстатьи, и работы; их собственные ключи независимы", async () => {
    const user = userEvent.setup();
    successStagePositions();
    renderTable(); // «6» — с детьми и с работами по умолчанию
    const parent = screen.getByTestId(rowTestId("6"));
    await user.click(within(parent).getByRole("button", { name: /Раскрыть/ }));
    await user.click(within(parent).getByRole("button", { name: /Работы/ }));
    expect(screen.getByText(worksHeading("6"))).toBeInTheDocument();
    // Сворачиваем ПОДСТАТЬИ — блок работ остаётся
    await user.click(within(parent).getByRole("button", { name: /Свернуть/ }));
    expect(screen.getByText(worksHeading("6"))).toBeInTheDocument();
  });

  /**
   * Эта строка — НЕ `CategoryRowGroup`: «Нераспределённое» рисуется
   * отдельным, рукописным `<TableRow data-testid="row-unallocated">` в
   * `TableFooter` (`StageSummaryTable`, подвал таблицы), а `showWorksButton`
   * — локальная переменная ВНУТРИ `CategoryRowGroup`, которая для этой строки
   * НИКОГДА не вычисляется. Поэтому тест НЕ МОЖЕТ покраснеть ни от какой
   * порчи условия кнопки (`row.has_drilldown_rows` ↔ `children.length`) — он
   * не проходит через этот код вовсе (проверено разбором guard-check'ов
   * выше: ни один из них не поколебал этот тест).
   *
   * Что он проверяет РЕАЛЬНО: инвариант контракта Task 1 — у
   * «Нераспределённого» `has_drilldown_rows` всегда `false`, потому что
   * разложение адресуется `work_category_id`, которого у этой строки нет
   * (§2.11). Первый `expect` — на самом инварианте фикстуры; второй — что из
   * него следует на экране. Если фикстура или контракт когда-нибудь заведут
   * этой строке `work_category_id` без пересмотра инварианта, покраснеет
   * первый `expect`, до того как расхождение станет вопросом рендера.
   */
  it("у «Нераспределённого» кнопки нет: строка рисуется вне CategoryRowGroup, а её has_drilldown_rows всегда false (Task 1)", () => {
    expect(sampleStageSummary.unallocated.has_drilldown_rows).toBe(false);
    renderTable();
    expect(within(screen.getByTestId("row-unallocated")).queryByRole("button", { name: /Работы/ })).toBeNull();
  });

  /**
   * Единственный путь, которым блок ДЕЙСТВИТЕЛЬНО теряет монтаж (не просто
   * визуально сворачивается): работы открываются у РЕБЁНКА, затем сворачивается
   * ПРЕДОК — ребёнок вместе со своим блоком работ пропадает из DOM целиком
   * (`isOpen &&` предка не рендерит детей вовсе), а не просто прячется.
   * Раскрытие предка обратно обязано вернуть и раскрытые подстатьи, и открытый
   * блок работ ребёнка, и счётчик — без нового запроса (`gcTime`/`staleTime:
   * Infinity`, Task 8). Хук здесь замокан и всегда отдаёт успех синхронно,
   * поэтому тест доказывает ровно то, что три ключа раскрытия и счётчик живут
   * в состоянии `StageSummaryTable` НАД рекурсией и переживают размонтирование
   * потомка — а не что-либо про сеть или про скелет: с синхронно резолвящимся
   * моком скелет и не мог бы появиться, и тест о нём ничего не утверждает.
   *
   * Ребёнок «6.99» уже есть в фикстуре (Task 10, `has_drilldown_rows: false`) —
   * добавлять третьего ребёнка не потребовалось: `summaryWith` включает его
   * флаг локально, для этого теста, не трогая фикстуру и не задевая тест
   * «по полю, а не по children» выше.
   */
  it("работы ПОДСТАТЬИ переживают сворачивание предка: раскрытие, счётчик и данные возвращаются из состояния таблицы", async () => {
    const user = userEvent.setup();
    successStagePositions();
    renderTable(summaryWith({ "6.99": true }));
    const parent = screen.getByTestId(rowTestId("6"));
    await user.click(within(parent).getByRole("button", { name: /Раскрыть/ }));
    const childCode = childCodeOf("6");
    const child = screen.getByTestId(rowTestId(childCode));
    await user.click(within(child).getByRole("button", { name: /Работы/ }));
    const n = drilldownGroupCount(sampleStagePositions.rows);
    expect(within(child).getByRole("button", { name: `Работы · ${n}` })).toBeInTheDocument();

    await user.click(within(parent).getByRole("button", { name: /Свернуть/ }));
    expect(screen.queryByText(worksHeading(childCode))).toBeNull(); // блок ушёл вместе со строкой
    await user.click(within(parent).getByRole("button", { name: /Раскрыть/ }));
    // Данные и счётчик не потеряны: раскрытие ребёнка и его блок
    // восстанавливаются из состояния таблицы (worksOpenIds/worksMountedIds/
    // worksCounts) и кэша запроса (Task 8).
    expect(screen.getByText(worksHeading(childCode))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Работы · ${n}` })).toBeInTheDocument();
  });
});
