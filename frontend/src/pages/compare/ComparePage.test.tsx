import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useLocation } from "react-router-dom";

import ComparePage from "./ComparePage";
import { deviationTone } from "./deviationTone";
import { sampleComparison, sampleInflationSeries } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { DEFAULT_TEST_USER, renderWithProviders } from "@/test/utils";

/**
 * Страница сравнения договоров (спека 2026-08-17, план — задача 8).
 *
 * Агрегат считает сервер (крестики над этим уже стоят в `test_comparison_*`
 * бэкенда) — здесь проверяется то, за что отвечает ЭКРАН: дерево строк по
 * умолчанию свёрнуто до корней, переключатель корзины и режим НДС меняют
 * показ, различимость прочерка/нуля/погашенного числа, подпись состава и
 * подсветка только при трёх и более сопоставимых значениях.
 *
 * **Предпосылка, требующая замера, а не веры на слово** (см. инструкцию
 * задачи 8): `position: sticky` не вычисляется в jsdom — тест ниже проверяет
 * ТОЛЬКО класс, которым запросено закрепление первой колонки; фактическое
 * поведение при горизонтальной прокрутке проверяется прогоном на стенде
 * (задача 9), а не этим файлом.
 */

const SELECTION = "ids=204,203,202,201";

/**
 * Пробник адреса: harness рендерит под `MemoryRouter`, поэтому `window.location`
 * правок роутера НЕ видит, и утверждение о ЗАПИСИ в URL иначе не построить.
 * Читает адрес изнутри роутера и выкладывает его в DOM.
 */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-search">{location.search}</span>;
}

function renderCompare(query = SELECTION, initialUser?: Parameters<typeof renderWithProviders>[1]) {
  return renderWithProviders(<ComparePage />, {
    initialRoute: `/compare?${query}`,
    ...initialUser,
  });
}

describe("Сравнение договоров — предпосылки фикстуры", () => {
  it("строка «1» действительно имеет три сопоставимых значения (испытание подсветки)", () => {
    const row1 = sampleComparison.rows.find((r) => r.code === "1");
    expect(row1?.medians.total.comparable_count).toBe(3);
  });

  it("строка «1» действительно имеет потомка (испытание раскрытия)", () => {
    const child = sampleComparison.rows.find((r) => r.parent_code === "1" && r.kind === "category");
    expect(child?.code).toBe("1.1");
  });

  it("строка «2» действительно имеет меньше трёх сопоставимых (испытание отсутствия подсветки)", () => {
    const row2 = sampleComparison.rows.find((r) => r.code === "2");
    expect(row2?.medians.total.comparable_count).toBeLessThan(3);
  });
});

describe("Сравнение договоров — дерево строк", () => {
  it("по умолчанию видны только корни", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByText("Земляные работы")).toBeInTheDocument();
    expect(screen.getByText("Благоустройство, дороги")).toBeInTheDocument();
    expect(screen.getByText("Нераспределённое")).toBeInTheDocument();

    // Второй уровень и служебная строка «Без подстатьи» скрыты до раскрытия.
    expect(screen.queryByText("Разработка грунта")).not.toBeInTheDocument();
    expect(screen.queryByText("Без подстатьи")).not.toBeInTheDocument();
  });

  it("раскрытие ветки показывает второй уровень (DoD 16)", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    const chevron = screen.getByRole("button", { name: "Развернуть статью 1" });
    await userEvent.click(chevron);

    expect(await screen.findByText("Разработка грунта")).toBeInTheDocument();
    expect(screen.getByText("Без подстатьи")).toBeInTheDocument();
  });
});

describe("Сравнение договоров — переключатель корзины (DoD 15)", () => {
  it("переключение на «ДС» меняет ячейку статьи на ноль", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    const cell = screen.getByTestId("comparison-cell-1-204");
    expect(within(cell).getByText(/1\s440\s000,00/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "ДС" }));

    expect(within(screen.getByTestId("comparison-cell-1-204")).getByText("0,00 ₽")).toBeInTheDocument();
  });
});

describe("Сравнение договоров — НДС и ставка в URL (DoD 18)", () => {
  it("режим и ставка восстанавливаются из адреса при заходе", async () => {
    renderCompare(`${SELECTION}&vat_mode=single&single_rate=22.00`);
    await screen.findByTestId("comparison-caption");

    expect(screen.getByRole("button", { name: "Единая" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Своя ставка" })).toHaveAttribute("aria-pressed", "false");
    expect(await screen.findByText("22,00 %")).toBeInTheDocument();
  });

  it("клик по режиму ПИШЕТ его в адрес и сбрасывает ставку единой", async () => {
    // Восстановление из адреса проверено выше, но «ссылка обязана воспроизводить
    // увиденное» держится на ЗАПИСИ. Без этого теста подмена `setSearchParams` на
    // локальный `useState` оставила бы всё зелёным (замечание финального ревью).
    renderWithProviders(
      <>
        <ComparePage />
        <LocationProbe />
      </>,
      { initialRoute: `/compare?${SELECTION}&vat_mode=single&single_rate=22.00` }
    );
    await screen.findByTestId("comparison-caption");

    await userEvent.click(screen.getByRole("button", { name: "Без НДС" }));

    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("location-search").textContent ?? "");
      expect(params.get("vat_mode")).toBe("net");
      // Ставка единой обязана уйти из адреса вместе с режимом: оставленная, она
      // при возврате в «Единую» восстановила бы ставку, которую человек не выбирал.
      expect(params.get("single_rate")).toBeNull();
      // Выборка при этом сохраняется — иначе страница потеряла бы, что сравнивать.
      expect(params.get("ids")).toBe("204,203,202,201");
    });
  });

  it("по умолчанию (без vat_mode в адресе) активна «Своя ставка»", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByRole("button", { name: "Своя ставка" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("Сравнение договоров — подпись налогового состава (AGENTS.md §10)", () => {
  it("подпись режима печатается на поверхности", async () => {
    renderCompare();
    const caption = await screen.findByTestId("comparison-caption");
    expect(caption.textContent).toBe(sampleComparison.caption);
  });

  it("в режиме «своя ставка» состав виден у каждой колонки", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByTestId("comparison-composition-204").textContent).toBe("20 %");
    expect(screen.getByTestId("comparison-composition-203").textContent).toBe("20 %");
    expect(screen.getByTestId("comparison-composition-202").textContent).toBe("22 %");
    expect(screen.getByTestId("comparison-composition-201").textContent).toBe("16 %");
  });

  it("вне режима «своя ставка» подписи состава не печатаются", async () => {
    renderCompare(`${SELECTION}&vat_mode=net`);
    await screen.findByTestId("comparison-caption");

    expect(screen.queryByTestId("comparison-composition-204")).not.toBeInTheDocument();
  });
});

describe("Сравнение договоров — подсветка отклонений (спека §2.5)", () => {
  it("подсветка есть только при трёх и более сопоставимых", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    // Строка «1» — три сопоставимых (203, 202, 201): отклонение показано.
    expect(screen.getByTestId("comparison-deviation-1-203")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-deviation-1-202")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-deviation-1-201")).toBeInTheDocument();
    // У 204 нет ТЭП — он не мог войти в медиану, отклонения нет вовсе.
    expect(screen.queryByTestId("comparison-deviation-1-204")).not.toBeInTheDocument();

    // Строка «2» — сопоставимых 0: подсветки нет, но экран называет причину.
    expect(screen.queryByTestId("comparison-deviation-2-204")).not.toBeInTheDocument();
    expect(screen.getByTestId("comparison-nomedian-2")).toBeInTheDocument();
    expect(screen.queryByTestId("comparison-nomedian-1")).not.toBeInTheDocument();
  });
});

describe("Сравнение договоров — прочерк, ноль и погашенное число (спека §2.1.2, §2.1.3)", () => {
  it("три состояния различимы в одной строке", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    // 204 — число.
    expect(within(screen.getByTestId("comparison-cell-2-204")).getByText(/600\s000,00/)).toBeInTheDocument();
    // 203 — статья есть, расценена в ноль: настоящий ноль, а не прочерк.
    // Пробел перед знаком валюты — неразрывный (`formatDecimalMoney`), не обычный.
    expect(screen.getByTestId("comparison-cell-2-203").textContent).toBe("0,00 ₽");
    // 202 — статьи нет вовсе: голый прочерк.
    expect(screen.getByTestId("comparison-cell-2-202").textContent).toBe("—");
    // 201 — статья есть, но погашена ДВУМЯ причинами сразу: прочерк со словами, не голый.
    const blanked = screen.getByTestId("comparison-cell-2-201").textContent ?? "";
    expect(blanked).toContain("—");
    expect(blanked).toContain("с ошибкой");
    expect(blanked).toContain("без цены");
    expect(blanked).not.toBe("—");
  });
});

describe("Сравнение договоров — ₽/м² (спека §2.4)", () => {
  it("у договора без ТЭП ₽/м² — прочерк, а не ноль", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByTestId("comparison-persqm-1-204").textContent).toBe("—");
  });
});

describe("Сравнение договоров — первая колонка закреплена (DoD 17)", () => {
  it("шапка и строки несут класс закрепления", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    const headCell = screen.getByRole("columnheader", { name: "Статья классификатора" });
    expect(headCell.className).toContain("sticky");

    const bodyCell = screen.getByTestId("comparison-row-1").querySelector("th");
    expect(bodyCell?.className).toContain("sticky");
  });
});

describe("Сравнение договоров — пороги подсветки закреплены (§2.5 правило 6)", () => {
  // Носителем порогов обязан быть тест: полоса ±10 % дана спекой дословно, а
  // вторая ступень (30 %) — решение реализации. Прежде замена любой из констант
  // на любое число оставляла набор зелёным, а ступень низкой интенсивности не
  // рендерилась ни в одном тесте (замечание финального ревью).
  it.each([
    ["0", "flat"],
    ["10", "flat"],
    ["-10", "flat"],
    ["11", "up-lo"],
    ["-11", "dn-lo"],
    ["30", "up-lo"],
    ["-30", "dn-lo"],
    ["31", "up-hi"],
    ["-31", "dn-hi"],
  ])("отклонение %s даёт ступень %s", (value, tone) => {
    expect(deviationTone(value)?.tone).toBe(tone);
  });

  it("ступень выбирается по ПОКАЗАННОМУ числу, а не по сырому", () => {
    // 10,4 показывается как «+10 %» и потому нейтрально; 10,6 показывается как
    // «+11 %» и потому окрашено. Цвет не расходится с цифрой, которую видит
    // человек, — это и есть основание разбора величины из округлённого текста.
    expect(deviationTone("10.4")?.tone).toBe("flat");
    expect(deviationTone("10.6")?.tone).toBe("up-lo");
  });

  it("отсутствующее отклонение не даёт ступени вовсе", () => {
    expect(deviationTone(null)).toBeNull();
  });
});

describe("Сравнение договоров — подсветка не рисуется на пустой ячейке (§2.5 правило 3)", () => {
  it("ячейка «— (ставка показа не определена)» не получает цветного бейджа", async () => {
    // Единственный достижимый случай: агрегат осознанно гасит ПОКАЗ, оставляя
    // нетто-ось и `deviation_pct` живыми (иначе медиана зависела бы от режима,
    // DoD 10). Общая фикстура его не моделирует — её `blankedBucket` несёт
    // `deviation_pct: null`, то есть различить поведение на ней нельзя.
    // Поэтому ответ подменяется точечно.
    const blankedButDeviating = {
      net: "1000000.00",
      shown: null,
      net_per_sqm: "1000.00",
      shown_per_sqm: null,
      state: "value",
      deviation_pct: "33.33",
      incomplete_reasons: ["display_rate_undefined"],
    };
    server.use(
      http.get("/api/v1/analytics/comparison", () =>
        HttpResponse.json({
          ...sampleComparison,
          vat_mode: "own",
          rows: sampleComparison.rows.map((row) =>
            row.code === "1"
              ? {
                  ...row,
                  cells: row.cells.map((cell, index) =>
                    index === 0 ? { ...cell, total: blankedButDeviating } : cell
                  ),
                }
              : row
          ),
        })
      )
    );

    renderCompare();
    await screen.findByTestId("comparison-caption");

    const contractId = sampleComparison.columns[0].contract_id;
    const amount = screen.getByTestId(`comparison-cell-1-${contractId}`);
    expect(amount.textContent).toContain("ставка показа не определена");

    // Предпосылка: отклонение в данных ЕСТЬ — иначе тест проверял бы его
    // отсутствие в ответе, а не подавление бейджа на пустой ячейке.
    expect(blankedButDeviating.deviation_pct).not.toBeNull();
    expect(screen.getByTestId(`comparison-persqm-1-${contractId}`).textContent).toBe("—");
    expect(screen.queryByTestId(`comparison-deviation-1-${contractId}`)).not.toBeInTheDocument();
  });
});

describe("Сравнение договоров — деньги округляются на показе (замер стенда)", () => {
  it("сумма с длинным хвостом печатается до копеек, точное значение — в подсказке", async () => {
    // Найдено ПРОГОНОМ НА СТЕНДЕ, не тестом: агрегат намеренно не квантует
    // деньги (иначе ДГП + ДС = Итого разошлось бы на копейку, DoD 5), поэтому
    // нетто приезжает частным от `gross_to_net` — «…,949999999999999982».
    // Экран печатал все восемнадцать знаков. Фикстура теперь несёт такой хвост,
    // и круглым его заменять нельзя — вернётся то же молчание.
    const total204 = sampleComparison.totals.find((c) => c.contract_id === 204);
    expect(total204?.total.shown).toContain("949999999999999982");

    renderCompare();
    await screen.findByTestId("comparison-caption");

    const cell = screen.getByTestId("comparison-cell-totals-204");
    // Пробелы нормализуются: `ru-RU` разделяет разряды НЕРАЗРЫВНЫМ пробелом, и
    // сравнение с обычным даёт «expected X to be X» — различие невидимо глазом.
    expect(cell.textContent?.replace(/\s/g, " ")).toBe("2 100 000,95 ₽");
    expect(within(cell).getByTitle(/Точное значение/)).toBeInTheDocument();
  });
});

describe("Сравнение договоров — выгрузка листа (§7.6, третий отчёт)", () => {
  it("кнопка выгрузки есть на странице и зовёт эндпоинт с ТЕМ ЖЕ режимом НДС", async () => {
    // Кнопка нужна именно здесь: выборку договоров даёт только эта страница,
    // экран отчётов её дать не может (§2.6). План не назначил кнопку ни одной
    // задаче, и без неё третий отчёт §7.6 недостижим из интерфейса.
    renderCompare(`${SELECTION}&vat_mode=net`);
    await screen.findByTestId("comparison-caption");

    const button = screen.getByRole("button", { name: "Выгрузить в Excel" });
    expect(button).toBeEnabled();

    await userEvent.click(button);

    // Обработчик MSW пишет параметры запроса в `handlerState` — тем же
    // способом, что у двух других выгрузок. Лист обязан спрашивать ТОТ ЖЕ
    // режим, что открыт на экране, иначе числа файла и экрана разойдутся.
    await waitFor(() => {
      expect(handlerState.lastReportRequest?.report).toBe("comparison");
    });
    expect(handlerState.lastReportRequest?.params).toMatchObject({
      vat_mode: "net",
      ids: "204,203,202,201",
    });
  });
});

describe("Сравнение договоров — права (спека §2.9, DoD 14)", () => {
  it("member видит страницу без ограничений", async () => {
    renderCompare(SELECTION, {
      initialUser: { id: 9, email: "member@example.com", role: "member" },
    });

    expect(await screen.findByTestId("comparison-caption")).toBeInTheDocument();
    expect(screen.getByText("Земляные работы")).toBeInTheDocument();
  });
});


// ---------------------------------------------------------------------------
//  Поправка на инфляцию на /compare (спека 2026-08-18 §2.9, §2.12; задача 14)
// ---------------------------------------------------------------------------

describe("ComparePage: поправка на инфляцию", () => {
  const SELECTION = "/compare?ids=201,202,203";

  beforeEach(() => {
    handlerState.inflationOutcome = "adjusted";
    handlerState.inflationRequests = 0;
    handlerState.inflationSeries = sampleInflationSeries;
    handlerState.lastInflationBody = null;
  });

  /**
   * Рендер страницы вместе с `LocationProbe`: маршрутизатор тестов — `MemoryRouter`,
   * и `window.location` он не трогает вовсе. Утверждения про адрес поэтому читают
   * состояние роутера, а не глобальный объект, — иначе они были бы зелёными на
   * пустой строке, то есть не проверяли бы ничего (замерено красным прогоном).
   */
  async function renderCompare(route = SELECTION, role?: "admin" | "member") {
    renderWithProviders(
      <>
        <ComparePage />
        <LocationProbe />
      </>,
      role
        ? { initialRoute: route, initialUser: { ...DEFAULT_TEST_USER, role } }
        : { initialRoute: route }
    );
    await waitFor(() => expect(screen.getByTestId("comparison-caption")).toBeInTheDocument());
  }

  function search(): string {
    return screen.getByTestId("location-search").textContent ?? "";
  }

  it("умолчательного ряда нет: «Привести» недоступно, месяц пуст и заблокирован", async () => {
    /*
     * Состояние из таблицы §2.12, первая строка. Умолчательный ряд обессмыслил бы
     * `400` контракта: справочник создаётся пустым, и рядов может быть несколько.
     */
    await renderCompare();

    expect(screen.getByRole("button", { name: "Привести" })).toBeDisabled();
    const month = screen.getByLabelText("В ценах");
    expect(month).toBeDisabled();
    expect(month).toHaveValue("");
    expect(screen.queryByTestId("inflation-levels")).not.toBeInTheDocument();
  });

  it("селектор показывает НАЗВАНИЕ ряда, а не его id", async () => {
    /*
     * Найдено замером в браузере, а не тестом: компонентные тесты кликали по опции
     * и не смотрели, что печатает сам триггер, — а он без рендер-функции
     * `SelectValue` печатает сырое значение, то есть «1» вместо
     * «Росстат, ИПЦ, декабрь к декабрю». Читатель по такому селектору не понимает,
     * каким рядом приведены числа, — то есть теряется ровно то, ради чего ряд
     * назван (§2.6).
     */
    await renderCompare();
    const trigger = screen.getByLabelText("Ряд индексов");
    expect(trigger).toHaveTextContent("Выберите ряд");

    await selectSeries("Росстат, ИПЦ, декабрь к декабрю");

    await waitFor(() =>
      expect(screen.getByLabelText("Ряд индексов")).toHaveTextContent(
        "Росстат, ИПЦ, декабрь к декабрю"
      )
    );
    expect(screen.getByLabelText("Ряд индексов")).not.toHaveTextContent(/^1$/);
  });

  it("выбор ряда сам числа не меняет и в адрес не попадает", async () => {
    /*
     * Вторая строка таблицы §2.12. Если выбор ряда уже менял бы числа, кнопка
     * «Привести» ничего не значила бы, а ссылка начала бы приводить без спроса.
     */
    await renderCompare();
    const before = screen.getByTestId("comparison-caption").textContent;

    await selectSeries("Росстат, ИПЦ, декабрь к декабрю");

    expect(screen.getByRole("button", { name: "Привести" })).toBeEnabled();
    expect(screen.getByTestId("comparison-caption").textContent).toBe(before);
    expect(search()).not.toContain("inflation_series_id");
    expect(handlerState.inflationRequests).toBe(0);
  });

  it("включение пишет ряд и РАЗРЕШЁННЫЙ СЕРВЕРОМ месяц в адрес", async () => {
    /*
     * Месяц приходит от сервера (§2.7): `Date.now()` на клиенте — часы читателя, и
     * два человека получили бы два ответа. Тест поэтому утверждает, что в адресе
     * оказался ИМЕННО серверный месяц, а не какой-нибудь.
     */
    await renderCompare();
    await selectSeries("Росстат, ИПЦ, декабрь к декабрю");
    await userEvent.click(screen.getByRole("button", { name: "Привести" }));

    await waitFor(() => expect(search()).toContain("target_month=2026-08"));
    expect(search()).toContain("inflation_series_id=1");

    /*
     * Смена параметров меняет ключ запроса, и до ответа страница показывает
     * skeleton — вместе с группой приведения. Поведение ДОФИЧЕВОЕ: так же ведёт себя
     * переключение режима НДС. Поэтому поле месяца ждём, а не читаем сразу: без
     * ожидания тест ловил бы момент загрузки и падал бы на отсутствии узла
     * (замерено красным прогоном).
     */
    await waitFor(() => expect(screen.getByLabelText("В ценах")).toHaveValue("2026-08"));
    expect(screen.getByRole("button", { name: "Привести" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("возврат к номиналу ЧИСТИТ адрес и убирает полосу уровней", async () => {
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-levels")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Номинал" }));

    await waitFor(() => expect(search()).not.toContain("inflation_series_id"));
    expect(search()).not.toContain("target_month");
    expect(screen.queryByTestId("inflation-levels")).not.toBeInTheDocument();
    expect(screen.getByLabelText("В ценах")).toHaveValue("");
  });

  it("полоса уровней показывает примечание ряда, а источники годов — НЕТ (DoD 32)", async () => {
    /*
     * Парная половина этого утверждения — «источник печатается на листе» — живёт в
     * бэкендовом тесте листа. Здесь проверяется ровно экранная сторона: три
     * источника рядом с тремя процентами превратили бы ориентирующую строку в
     * таблицу.
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));

    expect(bar).toHaveTextContent("официальная публикация, по РФ");
    expect(bar).toHaveTextContent("2024 +7,5%");
    expect(bar).toHaveTextContent("2026 +6,0%");
    expect(bar).toHaveTextContent("прогноз");
    expect(bar).toHaveTextContent("правлен 12.01.2026");
    expect(bar).not.toHaveTextContent("бюллетень");
    expect(bar).not.toHaveTextContent("прогноз Минэка");
  });

  it("чип колонки показывает УРОВЕНЬ, множитель — в подсказке (DoD 33)", async () => {
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-chip-202")).toBeInTheDocument());

    const chip = screen.getByTestId("inflation-chip-202");
    expect(chip).toHaveTextContent("+17,4%");
    expect(chip).toHaveAttribute("title", "множитель × 1.1744");
  });

  it("расходящиеся множители дают «разные» и ПЕРЕЧЕНЬ в подсказке (DoD 33)", async () => {
    /*
     * Утверждается СОДЕРЖИМОЕ подсказки, а не факт её наличия: чип без разбивки
     * отправлял бы читателя смотреть корзины, а корзины множителей не показывают —
     * арифметика колонки перестала бы быть проверяемой.
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-chip-204")).toBeInTheDocument());

    // 204 — ПЕРВАЯ колонка выборки (`signed_date DESC`), и расхождение множителей
    // фикстура кладёт именно на неё: порядок колонок задаёт сервер, и тест обязан
    // брать тот же, а не угаданный.
    const chip = screen.getByTestId("inflation-chip-204");
    expect(chip).toHaveTextContent("разные");
    expect(chip).toHaveAttribute("title", "ДГП × 1.1744 · ДС №1 × 1.0000");
  });

  it("смена ряда меняет И подпись, И числа (DoD 31)", async () => {
    /*
     * Оба утверждения в одном тесте намеренно: подпись, следующая за выбором при
     * одинаковых числах, означала бы, что селектор меняет надпись, не меняя
     * расчёта, — та же ложь, только незаметнее. Это реальный дефект макета (§7).
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-chip-202")).toBeInTheDocument());

    const firstCaption = screen.getByTestId("comparison-caption").textContent ?? "";
    const firstChip = screen.getByTestId("inflation-chip-202").getAttribute("title");
    expect(firstCaption).toContain("Росстат, ИПЦ, декабрь к декабрю");

    await selectSeries("Внутренняя оценка ПЭО");
    await userEvent.click(screen.getByRole("button", { name: "Привести" }));

    await waitFor(() =>
      expect(screen.getByTestId("comparison-caption").textContent).toContain(
        "Внутренняя оценка ПЭО"
      )
    );
    expect(screen.getByTestId("comparison-caption").textContent).not.toContain("Росстат");
    expect(screen.getByTestId("inflation-chip-202").getAttribute("title")).not.toBe(firstChip);
  });

  it("отказ по недостающим годам: баннер с кнопкой, номинальные числа, адрес сохранён", async () => {
    handlerState.inflationOutcome = "missing-years";
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    const banner = await waitFor(() => screen.getByTestId("inflation-refusal"));
    expect(banner).toHaveTextContent("Не заданы коэффициенты за годы: 2024, 2026.");
    expect(banner).toHaveTextContent("Росстат, ИПЦ, декабрь к декабрю");
    expect(
      within(banner).getByRole("button", { name: "Заполнить недостающие годы" })
    ).toBeInTheDocument();

    // Ошибочные параметры URL СОХРАНЕНЫ: человек обязан видеть, что не сработало.
    expect(search()).toContain("inflation_series_id=1");
    expect(search()).toContain("target_month=2026-08");
    // Переключатель визуально выключен, числа номинальные, полосы уровней нет.
    expect(screen.getByRole("button", { name: "Номинал" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.queryByTestId("inflation-levels")).not.toBeInTheDocument();
    expect(screen.queryByTestId("inflation-chip-202")).not.toBeInTheDocument();
  });

  it("отказ по дате ДС: кнопки НЕТ — правкой ряда это не лечится", async () => {
    /*
     * Предлагать «заполнить годы» там, где не хватает даты ДС, значит звать
     * человека делать работу, которая ничего не исправит (§2.9). Отсутствие кнопки
     * — самостоятельное требование, а не следствие текста.
     */
    handlerState.inflationOutcome = "amendment-date";
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    const banner = await waitFor(() => screen.getByTestId("inflation-refusal"));
    expect(banner).toHaveTextContent("У допсоглашений нет собственной даты подготовки");
    expect(banner).toHaveTextContent("ГП-0007 ДС №1");
    expect(
      within(banner).queryByRole("button", { name: "Заполнить недостающие годы" })
    ).not.toBeInTheDocument();
    // Машинный контекст человеку не показывается.
    expect(banner).not.toHaveTextContent("estimate_ids");
  });

  it("member видит ряд и приведение, но кнопок правки у него НЕТ (DoD 35)", async () => {
    /*
     * Проверяется ОТСУТСТВИЕМ узла, а не его заблокированностью: показывать
     * контрол, который упадёт в 403, нечестно (§2.10).
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`, "member");

    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));
    expect(bar).toHaveTextContent("Росстат, ИПЦ, декабрь к декабрю");
    expect(within(bar).queryByRole("button", { name: "Изменить ряд" })).not.toBeInTheDocument();

    handlerState.inflationOutcome = "missing-years";
  });

  it("member не получает кнопку и в баннере отказа (DoD 35)", async () => {
    handlerState.inflationOutcome = "missing-years";
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`, "member");

    const banner = await waitFor(() => screen.getByTestId("inflation-refusal"));
    expect(
      within(banner).queryByRole("button", { name: "Заполнить недостающие годы" })
    ).not.toBeInTheDocument();
  });

  it("два входа в окно правки дают ОДИН экземпляр (DoD 34)", async () => {
    /*
     * Счётчик `dialog` в DOM здесь — проверка «двух окон одновременно не бывает», а
     * НЕ доказательство переиспользования: один узел возможен и при двух независимо
     * написанных формах. Доказательство — то, что оба входа открывают компонент с
     * ОДНИМ И ТЕМ ЖЕ состоянием, и вход из баннера приносит в него недостающие годы.
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));

    await userEvent.click(within(bar).getByRole("button", { name: "Изменить ряд" }));
    await waitFor(() => expect(screen.getByText("Изменить ряд индексов")).toBeInTheDocument());
    expect(document.querySelectorAll("[role=dialog]")).toHaveLength(1);
    // Из полосы окно открывается БЕЗ недостающих годов: их некому передать.
    expect(screen.queryByLabelText("Коэффициент за 2027")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));

    handlerState.inflationOutcome = "missing-years";
    await userEvent.click(screen.getByRole("button", { name: "Номинал" }));
    await userEvent.click(screen.getByRole("button", { name: "Привести" }));

    const banner = await waitFor(() => screen.getByTestId("inflation-refusal"));
    await userEvent.click(
      within(banner).getByRole("button", { name: "Заполнить недостающие годы" })
    );

    await waitFor(() => expect(screen.getByText("Изменить ряд индексов")).toBeInTheDocument());
    expect(document.querySelectorAll("[role=dialog]")).toHaveLength(1);
  });

  it("после сохранения ряда сравнение перезапрашивается (DoD 36)", async () => {
    /*
     * Иначе на экране остались бы числа по прежним коэффициентам при уже новой
     * подписи — ровно то расхождение подписи с числами, против которого написан
     * §2.8.
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));
    const requestsBefore = handlerState.inflationRequests;

    await userEvent.click(within(bar).getByRole("button", { name: "Изменить ряд" }));
    await waitFor(() => expect(screen.getByLabelText("Коэффициент за 2024")).toBeInTheDocument());

    const field = screen.getByLabelText("Коэффициент за 2024");
    await userEvent.clear(field);
    await userEvent.type(field, "1.0800");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(handlerState.inflationRequests).toBeGreaterThan(requestsBefore));
  });

  it("сброс ряда в placeholder выключает приведение", async () => {
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-levels")).toBeInTheDocument());

    await selectSeries("Выберите ряд");

    await waitFor(() => expect(search()).not.toContain("inflation_series_id"));
    expect(screen.getByRole("button", { name: "Привести" })).toBeDisabled();
    expect(screen.queryByTestId("inflation-levels")).not.toBeInTheDocument();
  });
});

async function selectSeries(name: string) {
  await userEvent.click(screen.getByLabelText("Ряд индексов"));
  await userEvent.click(await screen.findByRole("option", { name }));
}
