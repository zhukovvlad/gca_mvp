import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sampleStagePositions } from "@/test/fixtures";
import { useStagePositions } from "@/services/queries";
import type { StagePositionsRow } from "@/types/domain";

import { KIND_LABEL, REASON_LABEL } from "./cellCopy";
import { NO_ROWS_LABEL, RETRY_LABEL, UNMATCHED_HINT, UNMATCHED_PILL_LABEL, extraPill, worksHeading } from "./drilldownCopy";
import { PositionDrilldown } from "./PositionDrilldown";

/**
 * Тесты блока разложения (спека 2026-08-30-position-drilldown-design.md §2.1,
 * §2.3, §2.7, §2.10, §2.11, §2.12; задача 10 плана). Мок `useStagePositions` —
 * тем же приёмом, что и у остальных хуков `@/services/queries` в этом наборе:
 * частичный `importActual`, чтобы не терять остальные экспорты модуля
 * (`apiErrorCode` использует сам компонент).
 */
vi.mock("@/services/queries", async (importActual) => ({
  ...(await importActual<object>()),
  useStagePositions: vi.fn(),
}));
const mocked = vi.mocked(useStagePositions);

const PROPS = {
  tenderId: 300,
  workCategoryId: 22,
  articleCode: "3.1",
  offerIds: [7001, 7002],
  columnsCount: 2,
  open: true,
};

function renderRows(props: Partial<typeof PROPS> = {}) {
  return render(
    <table>
      <tbody>
        <PositionDrilldown {...PROPS} {...props} />
      </tbody>
    </table>
  );
}

function success(data = sampleStagePositions) {
  mocked.mockReturnValue({ isPending: false, isError: false, data, refetch: vi.fn() } as never);
}

/**
 * Строка `unmatched` — ЛОКАЛЬНАЯ, а не в общей `sampleStagePositions`
 * (правка ревью 31.08.2026): у общей фикстуры суммы строк сходятся с
 * `article_amount` каждой колонки (§2.13, `fixtures.test.ts`), и пятая строка
 * потребовала бы пересчитывать остаток строки `rest` — churn с риском
 * сломать соседний тест ради строки, которая нужна только здесь. Локальная
 * копия собрана по образцу `cells` существующей строки — форма та же, суммы
 * произвольны, потому что тест не проверяет сходимость.
 */
function unmatchedRow(): StagePositionsRow {
  return {
    kind: "unmatched",
    row_key: "unmatched",
    catalog_position_id: null,
    chapter_ref_raw: null,
    lot_key: null,
    title: "Строки без каталожной привязки (2)",
    ambiguous: false,
    group_count: null,
    cells: sampleStagePositions.rows.find((r) => r.kind === "additional_works")!.cells,
    bargain: { kind: "none", value: null, direction: null, reason: null },
    contribution: { value: null, direction: null, reason: null },
  };
}

describe("PositionDrilldown (§2.1, §2.12, §6.3)", () => {
  it("заголовок блока называет вопрос и статью, строки — под ним", () => {
    success();
    renderRows();
    expect(screen.getByText(worksHeading("3.1"))).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
  });

  it("скелет на загрузке — одной строкой во всю ширину (§2.12)", () => {
    mocked.mockReturnValue({ isPending: true, isError: false, data: undefined, refetch: vi.fn() } as never);
    renderRows();
    const row = screen.getByRole("row");
    expect(within(row).getByRole("cell")).toHaveAttribute("colspan", "5"); // columnsCount + 3
  });

  it("отказ — текст причины и кнопка «Повторить», клик зовёт refetch (§2.12)", async () => {
    const refetch = vi.fn();
    mocked.mockReturnValue({
      isPending: false,
      isError: true,
      data: undefined,
      error: new Error("boom"),
      refetch,
    } as never);
    renderRows();
    await userEvent.setup().click(screen.getByRole("button", { name: RETRY_LABEL }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("reason=no_rows_in_subtree печатает подпись, а не пустоту", () => {
    success({ ...sampleStagePositions, rows: [], convergence: [], reason: "no_rows_in_subtree" });
    renderRows();
    expect(screen.getByText(NO_ROWS_LABEL)).toBeInTheDocument();
  });

  it("reason=unknown_vat_base печатает СВОЮ причину, не подпись no_rows_in_subtree (§2.8)", () => {
    // Структурный близнец теста выше: обе ветки читают `data.reason`, и без
    // отдельного теста ничего не заметило бы, поменяй их местами (находка
    // ревью 31.08.2026). Guard-check ниже это доказывает: свап веток краснит
    // ОБА теста разом, а не один.
    success({ ...sampleStagePositions, rows: [], convergence: [], reason: "unknown_vat_base" });
    renderRows();
    expect(screen.getByText(REASON_LABEL.unknown_vat_base)).toBeInTheDocument();
    expect(screen.queryByText(NO_ROWS_LABEL)).toBeNull();
  });

  it("свёрнутые строки не несут процентов и «Торга» (§2.3)", () => {
    success();
    renderRows();
    for (const kindRow of screen.getAllByTestId(/drill-row-(collapsed|rest)/)) {
      expect(within(kindRow).queryByText(/%/)).toBeNull();
    }
  });

  it("пилюля «допработы · 1.2» у kind=additional_works (§2.7)", () => {
    success();
    renderRows();
    expect(screen.getByText(extraPill("1.2"))).toBeInTheDocument();
  });

  it("две допработы одной ссылки из разных лотов подписаны ПОЛНЫМИ пилюлями и не схлопываются", () => {
    // Единственный тест, который краснеет, если компонент не позовёт
    // `showsLot` и продолжит печатать одну ссылку: отдельные тесты `showsLot` и
    // `extraPill` при этом останутся зелёными, а на экране две РАЗНЫЕ работы
    // снова станут неразличимы (третий круг ревью плана 31.08.2026).
    const base = sampleStagePositions.rows.find((r) => r.kind === "additional_works")!;
    success({
      ...sampleStagePositions,
      rows: [
        { ...base, row_key: "additional_works:lot_1:1", chapter_ref_raw: "1", lot_key: "lot_1", title: "Работа лота 1" },
        { ...base, row_key: "additional_works:lot_2:1", chapter_ref_raw: "1", lot_key: "lot_2", title: "Работа лота 2" },
      ],
    });
    // Компонентная проверка НА САМ React-ключ (§2.11), не только на текст
    // пилюль: пилюли были в той же слепой зоне до третьего круга ревью плана
    // (функция верна и юнит-протестирована, а компонент мог её не звать), и
    // ключ строки — ровно там же сейчас. Спай на `console.error` ловит то,
    // чего `data-row-key` не может: расхождение, где `key=` поменяли, а
    // атрибут — нет (или наоборот) — тогда цифровая проверка одного из двух
    // молчит, а React всё равно предупреждает о дубле ключа.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderRows();
    expect(screen.getByText(extraPill("1", "lot_1"))).toBeInTheDocument();
    expect(screen.getByText(extraPill("1", "lot_2"))).toBeInTheDocument();
    expect(screen.queryByText(extraPill("1"))).toBeNull(); // короткой пилюли быть не должно
    const extraRows = screen.getAllByTestId("drill-row-extra");
    expect(extraRows).toHaveLength(2); // строки не схлопнулись
    const rowKeys = extraRows.map((row) => row.getAttribute("data-row-key"));
    expect(new Set(rowKeys).size).toBe(2); // §2.11: ключ различает лоты, а не только текст пилюли
    const duplicateKeyWarning = consoleError.mock.calls.some((args) => String(args[0]).includes("same key"));
    expect(duplicateKeyWarning).toBe(false);
    consoleError.mockRestore();
  });

  it("kind=unmatched: пилюля несёт UNMATCHED_HINT подсказкой, наименование не дублируется пилюлей (§2.7)", () => {
    // Решение зафиксировано явно (ревью 31.08.2026): наименование строки уже
    // печатает полный серверный `title` («Строки без каталожной привязки
    // (N)») в самой строке (§2.10) — до правки пилюля брала `label={row.title}`
    // дословно и повторяла тот же текст ВТОРОЙ раз рядом. Признано дефектом:
    // пилюля должна быть коротким сигналом «строка диагностическая»
    // (`UNMATCHED_PILL_LABEL`), а не вторым именем строки — полное объяснение
    // остаётся в подсказке `UNMATCHED_HINT`. Тест утверждает и то, и другое:
    // текст `row.title` встречается РОВНО ОДИН раз на экране, а пилюля несёт
    // короткий label с UNMATCHED_HINT в title-атрибуте.
    const row = unmatchedRow();
    success({ ...sampleStagePositions, rows: [row] });
    renderRows();
    expect(screen.getAllByText(row.title)).toHaveLength(1); // не дублируется пилюлей
    const pill = screen.getByText(UNMATCHED_PILL_LABEL);
    expect(pill.closest("[title]")).toHaveAttribute("title", UNMATCHED_HINT);
  });

  it("исчезнувшая работа печатает «нет в файле», а не «снято» (§6.3)", () => {
    success();
    renderRows();
    expect(screen.getAllByText(KIND_LABEL.disappeared).length).toBeGreaterThan(0);
  });

  /**
   * Ветка `feat/drilldown-polish`: до правки этот тест доказывал, что запрос
   * остаётся включённым, потому что от него зависел И кэш, И счётчик N
   * кнопки-родителя (репортился колбэком `onCount`). N с тех пор — поле
   * сводки (`StageSummaryTable.tsx`, `drilldown_group_count`) и от этого
   * компонента больше не зависит вовсе; название теста сужено до того, что он
   * реально доказывает — сохранение кэша хука при свёрнутом блоке (Task 8).
   */
  it("open=false не рисует строк, но запрос остаётся включённым (кэш, Task 8)", () => {
    success();
    renderRows({ open: false });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(mocked).toHaveBeenCalledWith(300, 22, [7001, 7002], true);
  });

  /**
   * Приёмка на стенде (задача 12) нашла раздутие колонки подписи до 5738 px:
   * строка `DrilldownRow` рисовала первую ячейку БЕЗ `FIRST_COL_CLASS`
   * (`cellLayout.ts`), и `TableCell` наследовал базовый `whitespace-nowrap`
   * (`components/ui/table.tsx`) — одна строка со сплошным нередактированным
   * текстом раздувала колонку ВСЕЙ таблицы (`table-layout: auto` делит
   * ширину столбца между всеми ячейками одного индекса), включая короткие
   * заголовки корневых статей. jsdom раскладку не считает (`docs/insights/
   * unobservable-in-the-runner.md`), поэтому здесь проверяется ТОЛЬКО ЧЕМ
   * раскладка запрошена — точно тот же приём, что и соседний тест таблицы
   * свода (`StageSummaryTable.test.tsx`, «колонка классификатора зажата по
   * ширине…»): что из запрошенного вышло на экране, проверяет замер в
   * браузере (задача 12, `task-12-report.md`).
   */
  it("строка разложения делит ЗАЖИМ первой колонки со сводом — оба направления (задача 12)", () => {
    success();
    const { container } = renderRows();
    const titleCells = Array.from(container.querySelectorAll('[data-testid^="drill-row-"] > td:first-child'));
    expect(titleCells.length).toBeGreaterThan(0);
    for (const cell of titleCells) {
      expect(cell).toHaveClass("min-w-[250px]");
      expect(cell).toHaveClass("max-w-[420px]");
      expect(cell).toHaveClass("whitespace-normal");
      // Второе утверждение не лишнее — ровно этот дефект стенд и нашёл:
      // класса не было вовсе, и ячейка молча наследовала `whitespace-nowrap`
      // примитива. Проверка ТОЛЬКО наличия своего класса дефект бы не
      // ловила, если бы кто-то ОБА класса случайно проставил разом.
      expect(cell).not.toHaveClass("whitespace-nowrap");
      expect(cell).toHaveClass("align-top");
    }
  });

  /**
   * Второй дефект той же приёмки: заголовок «Почему изменился итог …» стоял
   * обычным предложением (13px, вес 500, цвет основного текста, подложка
   * `bg-surface-sunken`, неотличимая от рядовой вложенной строки) — в макете
   * это малый капслок-«эркер» секции (§2.12 приёмки, `task-12-report.md`):
   * тот же приём, что уже несёт заголовок «Этап N» шапки свода
   * (`StageSummaryTable.tsx`). Оба направления — свой набор классов стоит, а
   * прежний (неверный) снят — по тому же принципу, что и тест выше.
   */
  it("заголовок блока разложения несёт капслок-стиль эркера, а не текст обычного веса (задача 12)", () => {
    success();
    renderRows();
    const headingRow = screen.getByTestId("drill-heading");
    expect(headingRow).toHaveClass("bg-section-header");
    expect(headingRow).not.toHaveClass("bg-surface-sunken");
    const headingText = within(headingRow).getByText(worksHeading("3.1"));
    expect(headingText).toHaveClass("text-2xs");
    expect(headingText).toHaveClass("font-semibold");
    expect(headingText).toHaveClass("uppercase");
    expect(headingText).toHaveClass("tracking-wider");
    expect(headingText).toHaveClass("text-fg-tertiary");
    // Прежний вес — не рядом стоящий, а СНЯТЫЙ: правка меняла className
    // целиком, а не добавляла классы поверх старых.
    expect(headingText).not.toHaveClass("font-medium");
  });
});
