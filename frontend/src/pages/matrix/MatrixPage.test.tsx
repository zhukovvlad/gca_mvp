import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MatrixPage from "./MatrixPage";
import { longJobTitle } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * Сквозная матрица (§6, §7.5).
 *
 * Считает всё сервер (решение §6.3), поэтому здесь проверяется то, за что отвечает
 * экран: раскладка колонок с группировкой по объекту, закрепление первой колонки,
 * различимость «нет норматива» / «работы нет в смете» / «0 %», drill-down и фильтры.
 */

function renderMatrix() {
  return renderWithProviders(<MatrixPage />, { initialRoute: "/matrix" });
}

/**
 * Кликабельная ячейка на пересечении работы и договора.
 *
 * Ищется по индексу колонки в шапке, а не по порядку кнопок: у строки несколько
 * кликабельных ячеек, и `getByTitle` находит их все сразу. Заодно это проверяет,
 * что ячейка стоит в колонке своего договора, — перепутанный порядок колонок
 * иначе прошёл бы незамеченным.
 */
function clickableCellOf(jobTitle: string, contractNumber: string): HTMLElement {
  const header = screen.getByRole("columnheader", { name: contractNumber });
  const headerRow = header.closest("tr") as HTMLElement;
  const columnIndex = [...headerRow.children].indexOf(header);

  const row = screen.getByText(jobTitle).closest("tr") as HTMLElement;
  // Шапка договоров — второй уровень, и в ней нет колонки «Работа»: в строке тела
  // она первая, поэтому индекс сдвинут на единицу.
  const cell = row.children[columnIndex + 1] as HTMLElement;
  return within(cell).getByRole("button");
}

describe("Сквозная матрица", () => {
  it("колонки — договоры, сгруппированные по объекту", async () => {
    renderMatrix();

    // §6: «колонки = договоры (группировка по объекту)». Два уровня шапки.
    expect(await screen.findByRole("columnheader", { name: "ЖК Северный" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "БЦ Восточный" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "ГП-0114" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "ГП-0131" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "ГП-0140" })).toBeInTheDocument();
  });

  it("один объект держит свои договоры под одной группой", async () => {
    renderMatrix();

    // Два договора ЖК Северного стоят под общей шапкой: без colSpan группировка
    // выродилась бы в плоский список колонок.
    const group = await screen.findByRole("columnheader", { name: "ЖК Северный" });
    expect(group.getAttribute("colspan")).toBe("2");
  });

  it("первая колонка закреплена", async () => {
    renderMatrix();
    await screen.findByRole("columnheader", { name: "Работа" });

    // §6 требует закрепления первой колонки. И шапка, и ячейки: закрепи только
    // ячейки — при прокрутке заголовок «Работа» уехал бы, а столбец остался.
    expect(screen.getByRole("columnheader", { name: "Работа" }).className).toContain("sticky");
    const jobCell = screen.getByText("Кладка кирпичная").closest("td");
    expect(jobCell?.className).toContain("sticky");
  });

  it("ставка и отклонение стоят в одной ячейке, знак виден у обоих направлений", async () => {
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    // Превышение и экономия — разные тона, иначе «видно без ручных действий» (§10)
    // не выполняется.
    expect(screen.getByText("+20,0%").className).toContain("text-warning-text");
    expect(screen.getByText("-5,0%").className).toContain("text-accent-text");
    expect(screen.getByText(/12\s000,50/)).toBeInTheDocument();
  });

  it("«нет норматива» и «работы нет в смете» — разные вещи", async () => {
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    /*
      Три состояния ячейки, которые нельзя путать:
        · ставка есть, норматива нет  → прочерк с подсказкой «нет норматива» (§4);
        · работы в смете нет вовсе    → точка с подсказкой про смету;
        · ставка равна нормативу      → «0,0%» цифрой (§10).
      Слей первые два — и человек решил бы, что работу забыли пронормировать,
      тогда как её просто нет в этом договоре.
    */
    expect(screen.getAllByTitle(/Нет норматива/).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Работы нет в смете этого договора").length).toBeGreaterThan(0);
  });

  it("клик по ячейке раскрывает позиции, из которых сложилась ставка", async () => {
    const user = userEvent.setup();
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    // Кликаем ячейку КОНКРЕТНОГО договора: кликабельных ячеек в строке несколько,
    // и getByTitle без уточнения находит их все.
    await user.click(clickableCellOf("Кладка кирпичная", "ГП-0114"));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Кладка кирпичная наружных стен")).toBeInTheDocument();
    expect(within(dialog).getByText("Кладка кирпичная внутренних стен")).toBeInTheDocument();
    // Проверяемость: объяснено, почему ставка средневзвешенная, а не одна из двух.
    expect(within(dialog).getByText(/средневзвешенная ставка/)).toBeInTheDocument();
  });

  it("drill-down показывает, какая смета участвует", async () => {
    const user = userEvent.setup();
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    await user.click(clickableCellOf("Кладка кирпичная", "ГП-0114"));
    const dialog = await screen.findByRole("dialog");

    // §6: участвует только последняя смета. Человек должен видеть, какая именно.
    expect(within(dialog).getByText(/доп. соглашение № 1/)).toBeInTheDocument();
  });

  it("многокилобайтовое наименование не рвёт таблицу и остаётся доступным целиком", async () => {
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    // §11 AGENTS.md и §2.3 брифинга: как такое наименование ведёт себя в ячейке —
    // вопрос к глазу, но полный текст обязан быть в разметке.
    expect(screen.getByTitle(longJobTitle)).toBeInTheDocument();
  });

  it("текстовый фильтр сужает строки", async () => {
    const user = userEvent.setup();
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    await user.type(screen.getByLabelText("Работа"), "кирпич");

    // Фильтр применяет сервер; экран обязан отправить его и показать результат.
    await screen.findByText(/Работ в выборке: 1/);
    expect(screen.queryByTitle(longJobTitle)).not.toBeInTheDocument();
  });

  it("пустая выборка договоров и пустая выборка работ объясняются по-разному", async () => {
    handlerState.matrixOutcome = "no-columns";
    const { unmount } = renderMatrix();
    expect(await screen.findByText(/не попал ни один договор с загруженной сметой/)).toBeInTheDocument();
    unmount();

    // Разные причины пустоты требуют разных подсказок: в первом случае надо
    // ослабить фильтры выборки, во втором — поменять текст поиска.
    handlerState.matrixOutcome = "no-rows";
    renderMatrix();
    expect(await screen.findByText(/Ни одна работа не подошла под фильтры/)).toBeInTheDocument();
  });

  it("объясняет порядок строк — иначе он читается как случайный", async () => {
    renderMatrix();
    expect(
      await screen.findByText(/Порядок — по суммарной стоимости работы во всех договорах/)
    ).toBeInTheDocument();
  });

  it("объясняет, по какой дате работает фильтр периода", async () => {
    renderMatrix();
    // Фильтр по дате СМЕТЫ, не договора: по ней подбирается норматив (§4), и без
    // подписи человек ждал бы фильтрации по дате подписания.
    expect(
      await screen.findByText(/Период — по дате сметы \(при её отсутствии по дате договора\)/)
    ).toBeInTheDocument();
  });
});
