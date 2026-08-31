import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { sampleStagePositions } from "@/test/fixtures";
import { useStagePositions } from "@/services/queries";
import type { StagePositionsRow } from "@/types/domain";

import { KIND_LABEL, REASON_LABEL } from "./cellCopy";
import { NO_ROWS_LABEL, RETRY_LABEL, UNMATCHED_HINT, UNMATCHED_PILL_LABEL, extraPill, worksHeading } from "./drilldownCopy";
import { drilldownGroupCount } from "./drilldownData";
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

function renderRows(props: Partial<typeof PROPS & { onCount: (n: number) => void }> = {}) {
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

  it("open=false не рисует строк, но запрос остаётся включённым (кэш и счётчик N)", () => {
    success();
    renderRows({ open: false });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(mocked).toHaveBeenCalledWith(300, 22, [7001, 7002], true);
  });

  it("onCount получает число групп с учётом свёрнутых (§2.1: N кнопки)", () => {
    success();
    const onCount = vi.fn();
    renderRows({ onCount });
    expect(onCount).toHaveBeenCalledWith(drilldownGroupCount(sampleStagePositions.rows));
  });
});
