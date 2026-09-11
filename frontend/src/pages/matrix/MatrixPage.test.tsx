import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import MatrixPage from "./MatrixPage";
import { longJobTitle, sampleMatrix } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { CELL_RATE_REASONS } from "@/types/domain";
import type { CellRateReason, MatrixCell, Matrix, MatrixRow } from "@/types/domain";

/**
 * Сквозная матрица (§6, §7.5).
 *
 * Считает всё сервер (решение §6.3), поэтому здесь проверяется то, за что отвечает
 * экран: раскладка колонок с группировкой по объекту, закрепление первой колонки,
 * различимость «нет норматива» / «работы нет в смете» / «0 %», drill-down и фильтры.
 */

/**
 * `overrides` — частичный `Matrix`, а не только `rows`: правило цены (задача 9
 * плана 2026-09-09) добавило `positions_without_price`, и тестам третьей причины
 * пустоты нужно управлять им же, не трогая `handlerState` (`test/handlers.ts` не
 * входит в файлы задачи 9). `total` по умолчанию считается от `rows.length`, как и
 * раньше — переопределить можно явно через сам `overrides.total`.
 */
function renderMatrix(overrides?: Partial<Matrix>) {
  if (overrides) {
    const rows = overrides.rows ?? sampleMatrix.rows;
    server.use(
      http.get("/api/v1/analytics/matrix", () =>
        HttpResponse.json({
          ...sampleMatrix,
          ...overrides,
          rows,
          total: overrides.total ?? rows.length,
        })
      )
    );
  }
  return renderWithProviders(<MatrixPage />, { initialRoute: "/matrix" });
}

/**
 * Строка/ячейка матрицы, переиспользуемая тестами неполноты веса и причины
 * пустого отклонения (задача 9). `contract_id: 10` — реальный договор из
 * `sampleMatrixColumns` («ГП-0114»), иначе ячейка осталась бы без колонки.
 */
const cellFixture: MatrixCell = {
  contract_id: 10,
  rate: "12000.50",
  amount: "360015.00",
  standard_unit_rate: "10000.00",
  deviation_pct: "20.00",
  rate_reason: null,
  deviation_reason: null,
};

/** Ячейка без ставки — носитель для теста пяти причин ниже: только `rate_reason` меняется. */
function unratedCell(reason: CellRateReason): MatrixCell {
  return {
    ...cellFixture,
    rate: null,
    amount: null,
    deviation_pct: null,
    rate_reason: reason,
    deviation_reason: "no_rate",
  };
}

const rowFixture: MatrixRow = {
  catalog_position_id: 701,
  job_title: "Кладка кирпичная",
  unit_code: "M3",
  row_amount: "18000000.00",
  row_amount_incomplete: false,
  cells: [cellFixture],
};

/**
 * Кликабельная ячейка на пересечении работы и договора.
 *
 * Ищется по индексу колонки в шапке, а не по порядку кнопок: у строки несколько
 * кликабельных ячеек, и `getByTitle` находит их все сразу. Заодно это проверяет,
 * что ячейка стоит в колонке своего договора, — перепутанный порядок колонок
 * иначе прошёл бы незамеченным.
 *
 * **Находка задачи 9 (правило цены).** Прежняя версия сдвигала индекс на
 * единицу, предполагая, что второй уровень шапки (строка договоров) не несёт
 * колонки «Работа» — а `tanstack-table` кладёт placeholder именно в ПЕРВУЮ
 * строку (`colSpan`-заголовки объектов), настоящий заголовок «Работа»
 * оказывается во ВТОРОЙ строке рядом с договорами (проверено дампом DOM:
 * `[Работа][ГП-0114][ГП-0131][ГП-0140]` — тот же порядок, что и в теле).
 * Сдвиг оставался незамеченным восемь версий этого файла: у существующих
 * фикстур сосед по индексу (следующий договор) тоже нёс валидную ячейку, а
 * мок drill-down отвечает одними и теми же позициями независимо от того,
 * какой договор кликнули (`http.get(".../matrix/cell", () =>
 * HttpResponse.json(sampleMatrixCellDetail))` не смотрит на query). Задача 9
 * впервые построила ряд, где сосед — прочерк `·` без кнопки, и разница между
 * «индекс верный» и «индекс сдвинут» стала наблюдаемой.
 */
function clickableCellOf(jobTitle: string, contractNumber: string): HTMLElement {
  const header = screen.getByRole("columnheader", { name: contractNumber });
  const headerRow = header.closest("tr") as HTMLElement;
  const columnIndex = [...headerRow.children].indexOf(header);

  const row = screen.getByText(jobTitle).closest("tr") as HTMLElement;
  const cell = row.children[columnIndex] as HTMLElement;
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

  it("средневзвешенная ставка приезжает уже квантованной до копеек", async () => {
    renderMatrix();
    await screen.findByText("Кладка кирпичная");

    /*
      **Найдено пользователем на стенде, затем исправлено на бэкенде.** Раньше ставка
      ячейки приезжала делением `SUM(ставка × вес) / SUM(вес)` на `numeric` с полной
      точностью деления («49 467,503222935929»), и `MoneyCell` защитно округлял её на
      экране, пряча точное значение в `title`. Пересчёт НДС (задача 3) перевёл `rate`
      на `quantize_money` (`crud/analytics.py::_fold_cell`) — теперь ячейка КВАНТУЕТСЯ
      ДО КОПЕЕК на границе ответа, и такого хвоста от API больше не бывает: тест на
      старом сценарии проверял бы формат, которого контракт больше не отдаёт
      (приложение оркестратора задачи 9, п.4).

      Способность `MoneyCell` округлять и прятать точное значение в `title` не
      потеряна — она по-прежнему покрыта на `per_sqm` паспорта
      (`ProjectPassportPage.test.tsx`), где `numeric`-деление реально даёт длинный
      хвост.
    */
    expect(screen.getByText(/^640,50$/)).not.toHaveAttribute("title");
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

  it("объявляет, что суммы и нормативы показаны без НДС", async () => {
    /*
      Правка 1 финального ревью ветки пересчёта НДС: матрица — много-договорная
      поверхность (спека §2.5), общей ставки показа у выборки нет, и она измерена
      в нетто — как и оба листа xlsx («для банка» несёт «Все суммы и нормативы —
      без НДС»). Экранный собрат этой подписи не нёс — аналитик читал ставку
      ячейки как валовую из файла и не сходился ни с паспортом (целевая ставка),
      ни с файлом. Подпись обязана быть видна ДО таблицы, независимо от того,
      загрузились ли данные.
    */
    renderMatrix();
    expect(await screen.findByText(/без НДС \(нетто\)/)).toBeInTheDocument();
  });
});

/**
 * Вес строки: маркер неполноты (спека §2.6, исключение; задача 9).
 *
 * `SUM` игнорирует `NULL`, поэтому вес строки молча считается по ЧАСТИ ячеек,
 * когда база НДС известна не во всех договорах, — без явного признака частичная
 * сумма выглядела бы полной. `row_amount_incomplete` — булев признак строки,
 * который обязан попасть на экран отдельным маркером.
 */
describe("Вес строки: маркер неполноты", () => {
  it("помечает строку, чей вес посчитан не по всем ячейкам", async () => {
    renderMatrix({
      rows: [{ ...rowFixture, row_amount: "100.00", row_amount_incomplete: true }],
    });

    const marker = await screen.findByTestId("row-amount-incomplete");
    expect(marker).toBeInTheDocument();
    // Проверяет СВЯЗЬ, а не наличие текста: `toHaveAccessibleDescription` падает,
    // если `aria-describedby` указывает в пустоту (постоянный id сноски — ровно
    // ради этого; собранный из `catalog_position_id` сослался бы на элемент,
    // которого на текущей странице нет).
    expect(marker).toHaveAccessibleDescription(/база НДС известна не во всех договорах/i);
  });

  it("не помечает строку с полным весом", async () => {
    renderMatrix({
      rows: [{ ...rowFixture, row_amount: "100.00", row_amount_incomplete: false }],
    });

    await screen.findByText(rowFixture.job_title);
    expect(screen.queryByTestId("row-amount-incomplete")).not.toBeInTheDocument();
  });

  it("строка без веса печатает прочерк, а не ноль", async () => {
    renderMatrix({
      rows: [{ ...rowFixture, row_amount: null, row_amount_incomplete: true }],
    });

    expect(await screen.findByTestId("row-amount")).toHaveTextContent("—");
  });
});

/**
 * Причина отсутствия ставки ячейки на экране матрицы (правило цены, спека §2.5;
 * задача 9 плана 2026-09-09). До этой задачи один факт «база НДС неизвестна»
 * жил в поле `deviation_reason`, и `DeviationCell` подписывал ЛЮБОЙ пустой
 * результат как «нет норматива» — «неизвестна база НДС» визуально превращалась
 * в неверное «нет норматива». Задача 9 развела причины по двум полям: ставки
 * нет — `rate_reason`, отклонения нет при посчитанной ставке — `deviation_reason`
 * (у него теперь `no_rate`, когда `rate_reason` непуст, и это не новый факт).
 */
describe("Причина отсутствия ставки ячейки (rate_reason)", () => {
  it("ячейка без базы НДС не выдаёт себя за «нет норматива»", async () => {
    renderMatrix({
      rows: [{ ...rowFixture, cells: [unratedCell("unknown_vat_base")] }],
    });

    expect(await screen.findByText("неизвестна база НДС")).toBeInTheDocument();
    /*
      Находка ревью: `queryByText("нет норматива")` в ячейке матрицы не может
      совпасть НИКОГДА — `DeviationCell` в `variant="compact"` рисует прочерк
      «—», а не слово «нет норматива» (тот текст — только у `variant="full"`,
      паспорт/drill-down). Замерено: с отключённой веткой `rate_reason` (то
      есть с прежним поведением, где `deviation_reason` пришлось бы читать как
      "no_rate" → `DeviationCell` без причины → дефолт "no_standard") тест со
      старой проверкой ПРОХОДИЛ — наблюдаемой она не была. `queryByTitle`
      наблюдаем: компактный вариант всё равно кладёт `title` на прочерк, и
      именно этот `title` был бы "Нет норматива на дату сметы…", если бы ветка
      `rate_reason` не сработала.
    */
    expect(screen.queryByTitle(/Нет норматива на дату сметы/)).not.toBeInTheDocument();
  });
});

/**
 * Пять значений `rate_reason` (правило цены, спека §2.5; задача 9 плана
 * 2026-09-09) обязаны дать пять РАЗЛИЧНЫХ подписей ячейки — сведение любых
 * двух к одному тексту стёрло бы разницу между фактами («нет веса» и «цена
 * отрицательна» — наблюдаемо разные состояния присутствия). Различность
 * предъявлена утверждением (`Set` дедуплицирует совпадения), а не осмотром.
 */
describe("Пять причин отсутствия ставки ячейки — пять разных подписей", () => {
  /**
   * Список причин берётся из `CELL_RATE_REASONS` (`types/domain.ts`) — того же
   * места, что и сам тип `CellRateReason` (построен из `Record<CellRateReason,
   * true>`, чья полнота стережёт `tsc`), а не переписан здесь литералом:
   * шестая причина, забытая в рукописном списке, прошла бы тестом незамеченной
   * (ревью задачи 9). Ожидаемый текст каждой причины — свой `Record`
   * ниже: он тоже типизирован по `CellRateReason` и тоже не даст забыть ключ.
   */
  const EXPECTED_LABEL: Record<CellRateReason, string> = {
    unknown_vat_base: "неизвестна база НДС",
    not_finite: "не число",
    no_weight: "нет веса",
    negative_only: "цена отрицательна",
    no_price: "цены нет",
  };

  it("каждая причина видна на экране РОВНО своим текстом, и ни один текст не повторяет другой", async () => {
    const seenLabels = new Set<string>();
    for (const reason of CELL_RATE_REASONS) {
      const { unmount } = renderMatrix({
        rows: [{ ...rowFixture, cells: [unratedCell(reason)] }],
      });
      await screen.findByText(rowFixture.job_title);
      const label = clickableCellOf(rowFixture.job_title, "ГП-0114").textContent ?? "";
      // Точное совпадение, а не «длина больше нуля» (ревью задачи 9): длина
      // не отличила бы верный текст от опечатки или чужой подписи.
      expect(label).toBe(EXPECTED_LABEL[reason]);
      seenLabels.add(label);
      unmount();
    }

    expect(seenLabels.size).toBe(CELL_RATE_REASONS.length);
  });
});

/**
 * «Цены нет» (объект ячейки ЕСТЬ, ставка пуста) отличимо от «работы нет в
 * смете» (объекта ячейки нет вовсе) — правило цены сделало первое достижимым
 * (позиция без пригодной цены теперь становится строкой матрицы, задача 2
 * плана), и экран обязан различать эти два факта двумя разными наблюдениями,
 * а не одним и тем же прочерком (§10).
 */
describe("«цены нет» отличимо от «работы нет в смете»", () => {
  it("у ячейки с непригодной ценой есть объект, у отсутствующей работы — нет объекта вовсе", async () => {
    renderMatrix({
      rows: [{ ...rowFixture, cells: [unratedCell("no_price")] }],
    });
    await screen.findByText(rowFixture.job_title);

    // Договор ГП-0114 (contract_id: 10): объект ячейки ЕСТЬ, кнопка кликабельна,
    // подпись — «цены нет».
    const pricedCell = clickableCellOf(rowFixture.job_title, "ГП-0114");
    expect(within(pricedCell).getByText("цены нет")).toBeInTheDocument();

    // Договоры ГП-0131/ГП-0140 (contract_id: 11/12): работы в смете нет вовсе —
    // объекта ячейки нет, только прочерк с подсказкой про смету. Точное число
    // (2 — ровно два оставшихся договора из трёх в `sampleMatrixColumns`), а
    // не «больше нуля»: неточная проверка не заметила бы, если бы прочерк
    // ошибочно нарисовался и на самой ГП-0114.
    expect(screen.getAllByTitle("Работы нет в смете этого договора")).toHaveLength(2);
  });
});

/**
 * Третья причина пустой матрицы (правило цены, спека §2.9; задача 5/9 плана
 * 2026-09-09): позиции существуют, пригодной цены у них нет. Текст обязан
 * быть СВОИМ — не текстом очереди матчинга и не текстом «не-работы», иначе
 * третья причина неотличима от одной из двух прежних.
 *
 * **Найдено ревью.** `_without_price_condition` (`backend/crud/analytics.py`)
 * не смотрит на состояние каталожной строки вовсе — счётчик ничего не знает,
 * разобран ли каталог у стоящих за ним позиций. Первая редакция текста
 * утверждала «работы разобраны»: достижимый вход (все цены пустые, все
 * каталожные строки в `TO_REVIEW`) даёт именно такую комбинацию счётчиков
 * (пусто и очередь, и не-работа — предикат цены вычитает эти позиции из
 * очереди первым) при том, что разобрано НИЧЕГО. Текст обязан называть
 * только сам факт «цены нет», не утверждая ничего о каталоге.
 */
describe("Третья причина пустой матрицы: позиции без пригодной цены", () => {
  it("показывает собственный текст, называющий только факт, а не состояние каталога", async () => {
    renderMatrix({ rows: [], positions_without_price: 1200 });

    const message = await screen.findByText(/1200 позиций/);
    expect(message).toHaveTextContent(
      "Сметы загружены, но у 1200 позиций нет пригодной цены (пустая, нулевая, отрицательная или не число) — сравнивать нечего."
    );
    expect(screen.queryByText(/ждут ручного матчинга/)).not.toBeInTheDocument();
    expect(screen.queryByText(/не-работа/)).not.toBeInTheDocument();
    // Слово «разобран»/«разобраны» — это утверждение о СОСТОЯНИИ КАТАЛОГА,
    // которого счётчик не проверяет; текст третьей причины не вправе его нести.
    expect(message.textContent ?? "").not.toMatch(/разобран/);
  });

  /**
   * Вход, которого не видел предыдущий тест: очередь матчинга (первая причина)
   * И позиции без цены (третья причина) ненулевые ОДНОВРЕМЕННО — реальная
   * ситуация, раз счётчики независимы (предикат цены делит их первым, а не
   * состоянием каталога). Первая причина имеет приоритет показа, и её текст
   * не обязан (и не должен) утверждать что-либо о позициях без цены — они
   * тут просто не упомянуты, что и требуется.
   */
  it("очередь матчинга и позиции без цены ненулевые одновременно — приоритет у очереди, третий текст не всплывает", async () => {
    renderMatrix({
      rows: [],
      positions_pending_review: 500,
      positions_without_price: 1200,
    });

    expect(
      await screen.findByText(/500 расценённых позиций ждут ручного матчинга/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/1200 позиций/)).not.toBeInTheDocument();
    expect(screen.queryByText(/нет пригодной цены/)).not.toBeInTheDocument();
  });
});
