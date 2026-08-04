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

  it("средневзвешенная ставка показана копейками, точное значение — в подсказке", async () => {
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    /*
      **Найдено пользователем на стенде.** Ставка ячейки вычислена делением
      `SUM(ставка × вес) / SUM(вес)` на `numeric`, и PostgreSQL доводит результат до
      своей шкалы: в матрице стояло «49 467,503222935929» и «32 263 577,210000000000».
      Двенадцать знаков изображают точность, которой нет, а таблица из-за них
      читалась как набор случайных цифр.

      Округление здесь — решение слоя представления и только для ВЫЧИСЛЕННЫХ величин:
      хранимые ставки (паспорт, drill-down, нормативы) по-прежнему показываются
      целиком, иначе форма врала бы о цифре, по которой идёт торг.
    */
    // Ставка с длинной дробью показана копейками...
    const rounded = screen.getByText(/^640,50$/);
    expect(rounded).toBeInTheDocument();
    // ...а точное значение доступно в подсказке, то есть не спрятано.
    expect(rounded).toHaveAttribute("title", expect.stringContaining("640,503222935929"));

    // У ровной ставки подсказки нет — она повторяла бы видимое.
    expect(screen.getByText(/^9\s500,00$/)).not.toHaveAttribute("title");
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

  it("наименование зажато по высоте, иначе одна работа вытесняет страницу", async () => {
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    /*
      **Найдено прогоном стенда, а не тестом.** У реальной работы с наименованием на
      5077 символов ячейка выросла до 1323 px — одна строка выше листа А4, остальные
      строки уезжали за экран. После зажима стало 95 px (замер в Chrome).

      Проверка структурная: высоты в jsdom нет вовсе, раскладка не считается. Зато
      она опровергаема — уберите зажим, и тест краснеет. Настоящую высоту меряет
      прогон стенда, и это записано в отчёте фазы.
    */
    const title = screen.getByTitle(longJobTitle);
    expect(title.className).toContain("line-clamp-3");
    // `block` рядом с зажимом ставит display:block и отменяет -webkit-box, без
    // которого -webkit-line-clamp не работает: так и было в первой редакции.
    expect(title.className.split(/\s+/)).not.toContain("block");
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

  it("неразобранная очередь названа причиной пустой матрицы", async () => {
    /*
      Третья причина пустоты, найденная прогоном стенда: договоры в выборке есть,
      цены заполнены, но каталог не разобран. Прежний текст предлагал «попробовать
      другой текст поиска» — совет, который ничего не исправил бы.
    */
    handlerState.matrixOutcome = "pending-review";
    renderMatrix();

    expect(
      await screen.findByText(/1830 расценённых позиций ждут ручного матчинга/)
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Разобрать очередь" })).toHaveAttribute(
      "href",
      "/review"
    );
    expect(screen.queryByText(/Попробуйте другой текст поиска/)).not.toBeInTheDocument();
  });

  it("пустой поиск не списывается на очередь матчинга", async () => {
    /*
      **Замечание внешнего ревью.** Счётчик очереди относится к выборке, а не к
      поиску, поэтому при непустом `q` он не объясняет пустоту результата. Первая
      редакция всё равно предпочитала сообщение об очереди: человек, искавший
      несуществующую работу, читал «разберите очередь» и шёл не туда.

      Правильно назвать причину, которую человек только что создал сам, — и при этом
      не скрыть очередь: она объясняет, почему матрица в целом тонкая.
    */
    handlerState.positionsPendingReview = 1830;
    const user = userEvent.setup();
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    await user.type(screen.getByLabelText("Работа"), "такой работы нет");

    // Причина пустоты — поиск, и сказано именно это.
    expect(await screen.findByText(/По запросу работ не найдено/)).toBeInTheDocument();
    // Очередь упомянута как дополнение, а не как причина.
    expect(screen.getByText(/1830 расценённых позиций ждут ручного матчинга/)).toBeInTheDocument();
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
