import { beforeEach, describe, expect, it } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
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

// ---------------------------------------------------------------------------
//  Действующая ставка показа в адресе
//  (спека диаграммы стоимости §2.6, подраздел «Ставка показа не имеет права
//   меняться от фильтра»; §2.10; DoD 33)
// ---------------------------------------------------------------------------

describe("Сравнение договоров — действующая ставка показа в адресе", () => {
  /** Запросы сравнения, дошедшие до мока: по ним видно, ЧТО отправил клиент. */
  let requests: Record<string, string>[] = [];

  beforeEach(() => {
    requests = [];
  });

  /**
   * Обработчик, повторяющий проводку СЕРВЕРА, а не общий мок.
   *
   * Копируется `effective_single_rate` (`backend/crud/comparison.py`): ответ
   * отражает ПОЛУЧЕННУЮ ставку при любом режиме, а предвыбор подставляет
   * только в «Единой» и только когда ставка не пришла. Копировать это важно:
   * общий мок отдаёт `single_rate: null` вне «Единой» САМ, то есть держит
   * рядом вторую защиту, и снятие клиентской проводки на нём ничего не уронило
   * бы (`docs/insights/verifying-guards.md`, слой 8).
   *
   * `preselected` задаётся тестом, чтобы действующая ставка и предвыбор могли
   * РАЗОЙТИСЬ: на фикстуре они совпадают, и тест на их совпадении не различает,
   * какое из двух полей читает экран.
   *
   * Подпись состава помечена режимом и ставкой — это единственный сигнал
   * ПРИМЕНЁННОГО ответа, а без него утверждение об отсутствии записи мерило бы
   * старый кадр. Настоящий сервер подпись по режиму тоже различает
   * (`_mode_caption`), общий мок — нет, и граница по времени вместо этого
   * сигнала зависела бы от загрузки машины (devlog §4.3б).
   */
  function echoServerRate(preselected = "20.00") {
    server.use(
      http.get("/api/v1/analytics/comparison", ({ request }) => {
        const url = new URL(request.url);
        requests.push(Object.fromEntries(url.searchParams));
        const mode = url.searchParams.get("vat_mode") ?? "own";
        const asked = url.searchParams.get("single_rate");
        const effective = asked ?? (mode === "single" ? preselected : null);
        return HttpResponse.json({
          ...sampleComparison,
          vat_mode: mode,
          rate_preselected: preselected,
          single_rate: effective,
          caption: `mode=${mode} rate=${effective ?? "none"}`,
        });
      })
    );
  }

  function renderWithProbe(query: string) {
    renderWithProviders(
      <>
        <ComparePage />
        <LocationProbe />
      </>,
      { initialRoute: `/compare?${query}` }
    );
  }

  function search(): URLSearchParams {
    return new URLSearchParams(screen.getByTestId("location-search").textContent ?? "");
  }

  /** Ждёт ПРИМЕНЁННОГО ответа: подпись состава несёт режим и ставку ответа. */
  function appliedCaption(mode: string, rate: string) {
    return screen.findByText(`mode=${mode} rate=${rate}`);
  }

  it("предпосылка: своей ставки в фикстуре нет, предвыбор — 20.00", () => {
    // Обе величины тест ниже подразумевает. Фикстура правится, предпосылка
    // молча меняется, и «дописал в адрес» стало бы «там уже было».
    expect(sampleComparison.single_rate).toBeNull();
    expect(sampleComparison.rate_preselected).toBe("20.00");
  });

  it("открытие «Единой» без ставки дописывает её в адрес (DoD 33)", async () => {
    echoServerRate();
    renderWithProbe(`${SELECTION}&vat_mode=single`);

    // Утверждается ЗНАЧЕНИЕ, а не факт появления параметра: «появился»
    // прошло бы и при записи чего угодно.
    await waitFor(() => expect(search().get("single_rate")).toBe("20.00"));
  });

  it("в адрес идёт ДЕЙСТВУЮЩАЯ ставка, а не предвыбор", async () => {
    /*
      Ставка в адресе и предвыбор РАЗВЕДЕНЫ: сервер показал числа в 22.00,
      предвыбрал бы 20.00. Это и есть смысл записи — после неё предвыбор в игру
      больше не входит (спека диаграммы стоимости §2.6, DoD 34). Сужения по
      классу здесь нет: единственный контрол сужения — чипы классов, их заводит
      задача 10, и переход через чип закрывается там.
    */
    echoServerRate("20.00");
    renderWithProbe(`${SELECTION}&vat_mode=single&single_rate=22.00`);

    await appliedCaption("single", "22.00");
    expect(search().get("single_rate")).toBe("22.00");
    expect(requests.every((r) => r.single_rate === "22.00")).toBe(true);
  });

  it("выход из «Единой» не возвращает ставку в адрес", async () => {
    echoServerRate();
    renderWithProbe(`${SELECTION}&vat_mode=single`);

    // Дождаться, что эффект уже записал ставку — иначе переход в «Без НДС»
    // ничего не отменял бы: параметра и не было изначально.
    await waitFor(() => expect(search().get("single_rate")).toBe("20.00"));

    await userEvent.click(screen.getByRole("button", { name: "Без НДС" }));

    // Уходит вместе с режимом — это делает `updateVatMode`.
    await waitFor(() => expect(search().get("vat_mode")).toBe("net"));

    /*
      И НЕ ВОЗВРАЩАЕТСЯ. Утверждение об отсутствии требует границы, и граница
      взята сигналом ПРИМЕНЁННОГО ответа, а не таймером: дописать параметр
      обратно эффект может только после разбора ответа на новый запрос, а
      подпись состава этот разбор и означает. Таймер здесь мерил бы сетевой
      круг и на загруженной машине истекал бы раньше него — прогон стал бы
      молча зелёным (devlog §4.3б).
    */
    await appliedCaption("net", "none");
    // Плюс один такт: сам эффект сети не ждёт — он сработал бы уже на этом
    // кадре, а `act` дожидается очереди обновлений детерминированно, не
    // таймером. Достаточность проверена снятием (см. devlog).
    await act(async () => {});
    expect(search().get("single_rate")).toBeNull();
  });

  it("вне «Единой» клиент ставку серверу не отправляет", async () => {
    /*
      Пин на проводку, из которой эффект не имеет отдельного условия на режим:
      `params` отдаёт `single_rate` ТОЛЬКО в «Единой». Обработчик здесь
      отражает полученную ставку при любом режиме, как настоящий сервер, —
      значит, начни клиент её отправлять, ответ вернул бы её, эффект записал бы
      её в адрес, и человек увидел бы ставку, которой не выбирал.
    */
    echoServerRate();
    renderWithProbe(`${SELECTION}&vat_mode=net&single_rate=22.00`);

    await appliedCaption("net", "none");
    expect(requests.length).toBeGreaterThan(0);
    expect(requests.every((r) => r.single_rate === undefined)).toBe(true);
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

  /** Рендер БЕЗ ожидания подписи: при неизвестной ошибке её не будет вовсе. */
  function renderCompareRaw(route: string) {
    renderWithProviders(
      <>
        <ComparePage />
        <LocationProbe />
      </>,
      { initialRoute: route }
    );
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

  it("штатный отказ НЕ показывает общий «не удалось загрузить»", async () => {
    /*
     * Найдено внешним ревью. Доменный отказ приведения ошибкой загрузки не
     * является: сравнение показано, номинальное, и причину называет баннер. Пока
     * общий `EmptyState` рисовался по `isError`, поверхность сама себе
     * противоречила — «не удалось загрузить сравнение» над загруженной таблицей, и
     * читатель не знал, каким из двух сообщений верить.
     */
    handlerState.inflationOutcome = "missing-years";
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    await waitFor(() => expect(screen.getByTestId("inflation-refusal")).toBeInTheDocument());
    expect(screen.queryByText("Не удалось загрузить сравнение")).not.toBeInTheDocument();
    // Таблица на месте: показаны номинальные числа, а не пустое состояние.
    expect(screen.getByTestId("comparison-caption")).toBeInTheDocument();
  });

  it("НЕИЗВЕСТНАЯ ошибка загрузки по-прежнему даёт общий EmptyState", async () => {
    // Парой к предыдущему: правка не имеет права спрятать настоящий сбой.
    server.use(
      http.get("/api/v1/analytics/comparison", () => new HttpResponse(null, { status: 500 }))
    );
    await renderCompareRaw(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    expect(await screen.findByText("Не удалось загрузить сравнение")).toBeInTheDocument();
    expect(screen.queryByTestId("inflation-refusal")).not.toBeInTheDocument();
  });

  it("отказ ПЛЮС падение номинального запроса: баннер причины и явное состояние", async () => {
    /*
     * Третий круг внешнего ревью. Экран делает второй, номинальный запрос — и он
     * тоже умеет падать. Пока баннер жил внутри блока `comparison &&`, эта пара
     * давала страницу с ОДНИМ ЗАГОЛОВКОМ: чисел нет, общий EmptyState подавлен
     * условием `!refused`, скелета нет — `nominalQ` не в `isPending`, а в `isError`.
     * Молчание здесь хуже любого текста: причина отказа приведения ИЗВЕСТНА из
     * первого ответа и от чисел не зависит.
     *
     * Хендлер различает запросы по наличию инфляционных параметров: первый обязан
     * ответить штатным `422`, второй — сбоем.
     */
    server.use(
      http.get("/api/v1/analytics/comparison", ({ request }) => {
        if (new URL(request.url).searchParams.get("inflation_series_id")) {
          return HttpResponse.json(
            {
              detail: {
                code: "missing_inflation_years",
                message: "Не заданы коэффициенты за годы: 2024, 2026.",
                missing_years: [2024, 2026],
              },
            },
            { status: 422 }
          );
        }
        return new HttpResponse(null, { status: 500 });
      })
    );
    await renderCompareRaw(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    // Отсутствие чисел названо ОТДЕЛЬНО: это второй факт, а не тот же самый. Ждать
    // приходится именно его: баннер появляется раньше — на отказе ПЕРВОГО запроса,
    // когда номинальный ещё в полёте и на экране законно стоит скелет.
    expect(await screen.findByText("Номинальные числа получить не удалось")).toBeInTheDocument();
    const banner = screen.getByTestId("inflation-refusal");
    expect(banner).toHaveTextContent("Не заданы коэффициенты за годы: 2024, 2026.");
    // Общий «не удалось загрузить сравнение» по-прежнему подавлен: отказ штатный, и
    // два сообщения об одном событии оставили бы читателя выбирать, какому верить.
    expect(screen.queryByText("Не удалось загрузить сравнение")).not.toBeInTheDocument();
    // Баннер ОДИН: точек монтирования две, но условия взаимоисключающие.
    expect(screen.getAllByTestId("inflation-refusal")).toHaveLength(1);
    // Ошибочные параметры остаются в адресе и здесь (§2.9).
    expect(search()).toContain("inflation_series_id=1");
  });

  it("баннер отказа при ПОКАЗАННЫХ числах остаётся ровно одним узлом", async () => {
    /*
     * Парный к предыдущему, и он про механизм: баннер — один элемент с ДВУМЯ точками
     * монтирования (числа есть / чисел нет). Условия взаимоисключающие, и если кто-то
     * их разведёт, баннер удвоится — читатель увидит два одинаковых предупреждения.
     */
    handlerState.inflationOutcome = "missing-years";
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    await waitFor(() => expect(screen.getAllByTestId("inflation-refusal")).toHaveLength(1));
    expect(screen.queryByText("Номинальные числа получить не удалось")).not.toBeInTheDocument();
  });

  it("упавший список рядов: селектор называет ряд из ответа, а не «Выберите ряд»", async () => {
    /*
     * Находка сверх двух названных ревью. Список рядов — отдельный запрос, и на его
     * падении `find` промахивался: селектор печатал «Выберите ряд» при работающем
     * приведении, то есть говорил неправду о том, чем приведены показанные числа, —
     * дефект того же рода, что захардкоженное название в подписи оси (DoD 31).
     * Название при этом ИЗВЕСТНО: его вернул сервер вместе с числами.
     *
     * Второе утверждение — про молчание: пустой список опций без объяснения читается
     * как «рядов не заведено».
     */
    server.use(
      http.get("/api/v1/inflation-series", () => new HttpResponse(null, { status: 500 }))
    );
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    const trigger = screen.getByLabelText("Ряд индексов");
    await waitFor(() => expect(trigger).toHaveTextContent("Росстат, ИПЦ, декабрь к декабрю"));
    expect(trigger).not.toHaveTextContent("Выберите ряд");
    expect(screen.getByText(/Список рядов не загрузился/)).toBeInTheDocument();
    // Приведение продолжает работать: считает его сервер, и список ему не нужен.
    expect(screen.getByTestId("inflation-levels")).toBeInTheDocument();
  });

  it("упавший список рядов: окно правки даёт отказ, а не бесконечную загрузку", async () => {
    /*
     * Тот же упавший запрос, вторая его жертва — и это ПРИЧИНА находки ревью про
     * окно. Режим окна уже не зависел от промаха поиска, но «объект не получен» и
     * «объект не будет получен» оставались одним значением, поэтому окно обещало
     * загрузку, которая никогда не кончится.
     */
    server.use(
      http.get("/api/v1/inflation-series", () => new HttpResponse(null, { status: 500 }))
    );
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));
    await userEvent.click(within(bar).getByRole("button", { name: "Изменить ряд" }));

    expect(await screen.findByText(/Не удалось загрузить ряд и его годы/)).toBeInTheDocument();
    expect(screen.queryByText(/Загружаем ряд и его годы/)).not.toBeInTheDocument();
    expect(screen.queryByText("Новый ряд индексов")).not.toBeInTheDocument();
  });

  it("«Повторить» перезапрашивает СПИСОК, которым владеет страница, и доводит до формы", async () => {
    /*
     * Найдено третьим кругом ревью, отдельным замечанием: окно звало «закройте и
     * попробуйте снова», а закрытие ничего не перезапрашивает — список рядов
     * смонтирован на СТРАНИЦЕ, `refetchOnWindowFocus` выключен, `staleTime` минута.
     * Обещание было неисполнимо, и это тот же класс, что весь этот круг: поверхность
     * утверждала то, чего не умеет.
     *
     * Тест доказывает РАБОТУ, а не наличие кнопки: список отвечает `500` один раз, и
     * форма может появиться только если повтор действительно ушёл на сервер. Он же
     * закрепляет, что повтор идёт через владельца запроса: своим `refetch` окно
     * список не достаёт.
     */
    let attempts = 0;
    // Второй ответ за воротами, а не за таймером: утверждение о ПРОМЕЖУТОЧНОМ
    // состоянии, стоящее на угаданном времени, — ложная предпосылка (§12). Пока
    // повтор в полёте, окно обязано показывать загрузку, а не продолжать утверждать
    // отказ. Обещание тут поверхности, а исполняет его сам запрос: `fetchState()` в
    // `@tanstack/query-core` ставит `pending` и гасит `error`, когда данных ещё не
    // было, — поэтому в `buildDialogTarget` и стоит один `isPending`. Тест закрепляет
    // обещание, а не механизм: сменится механизм — сюда и придёт красное.
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/v1/inflation-series", async () => {
        attempts += 1;
        if (attempts === 1) return new HttpResponse(null, { status: 500 });
        await gate;
        return HttpResponse.json(handlerState.inflationSeries);
      })
    );
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);

    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));
    await userEvent.click(within(bar).getByRole("button", { name: "Изменить ряд" }));
    await screen.findByText(/Не удалось загрузить ряд и его годы/);

    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText(/Загружаем ряд и его годы/)).toBeInTheDocument();
    expect(screen.queryByText(/Не удалось загрузить ряд и его годы/)).not.toBeInTheDocument();
    release();

    // Окно доезжает до настоящей формы правки — с названием ряда и его годами.
    expect(await screen.findByText("Изменить ряд индексов")).toBeInTheDocument();
    expect(await screen.findByLabelText("Коэффициент за 2024")).toBeInTheDocument();
    expect(screen.queryByText("Новый ряд индексов")).not.toBeInTheDocument();
    expect(attempts).toBe(2);
    // Сообщение у селектора уходит вместе с отказом: список загружен.
    expect(screen.queryByText(/Список рядов не загрузился/)).not.toBeInTheDocument();
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

  it("архивный ряд по ссылке открывает окно ПРАВКИ, а не создания", async () => {
    /*
     * Найдено финальным ревью ветки. Архивный ряд по прямой ссылке — поддержанный
     * путь (§2.10, DoD 20): приведение по нему считается, полоса рисуется. Пока
     * список рядов шёл БЕЗ архивных, поиск по нему давал `undefined`, а `undefined
     * ?? null` в контракте окна означает РЕЖИМ СОЗДАНИЯ: admin, думая что правит
     * ряд, заводил дубликат либо упирался в 409 по занятому имени.
     *
     * Ряд 3 в фикстурах архивный.
     */
    await renderCompare(`${SELECTION}&inflation_series_id=3&target_month=2026-08`);
    const bar = await waitFor(() => screen.getByTestId("inflation-levels"));

    await userEvent.click(within(bar).getByRole("button", { name: "Изменить ряд" }));

    await waitFor(() => expect(screen.getByText("Изменить ряд индексов")).toBeInTheDocument());
    expect(screen.queryByText("Новый ряд индексов")).not.toBeInTheDocument();
    // Окно показывает своё же состояние «заморожен» и не даёт сохранить.
    expect(screen.getByText(/сначала верните его в активные/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("архивный ряд не предлагается в опциях, но выбранный виден в селекторе", async () => {
    /*
     * Два утверждения об одном списке: «не предлагаются для нового выбора» (§2.10) и
     * «селектор не может не показывать то, что в нём стоит». Второе — следствие
     * первого, доведённое до конца: убрать выбранный архивный из опций значило бы
     * оставить триггер с названием, которого в списке нет.
     */
    await renderCompare(`${SELECTION}&inflation_series_id=3&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-levels")).toBeInTheDocument());

    expect(screen.getByLabelText("Ряд индексов")).toHaveTextContent(
      "Ряд 2024 года, выведен из обращения"
    );

    await userEvent.click(screen.getByLabelText("Ряд индексов"));
    const options = await screen.findAllByRole("option");
    const labels = options.map((option) => option.textContent ?? "");
    // Выбранный архивный — есть, и помечен; второго архивного в фикстурах нет.
    expect(labels.some((label) => label.includes("(в архиве)"))).toBe(true);
    expect(labels.filter((label) => label.includes("(в архиве)"))).toHaveLength(1);
  });

  it("смена ряда при ВКЛЮЧЁННОМ приведении применяется сразу", async () => {
    /*
     * Найдено финальным ревью ветки. Прежняя редакция оставляла адрес нетронутым, и
     * жило состояние «в селекторе один ряд, на всей остальной поверхности другой»:
     * применить новый можно было только повторным кликом по уже НАЖАТОЙ «Привести»,
     * а нажатая кнопка к клику не приглашает. Так решает согласованный макет (§7
     * спеки: «смена ряда меняет числа, а не только подпись»).
     */
    await renderCompare(`${SELECTION}&inflation_series_id=1&target_month=2026-08`);
    await waitFor(() => expect(screen.getByTestId("inflation-chip-202")).toBeInTheDocument());
    const firstChip = screen.getByTestId("inflation-chip-202").getAttribute("title");

    await selectSeries("Внутренняя оценка ПЭО");

    // Ни одного лишнего клика: адрес, подпись и числа следуют за селектором.
    await waitFor(() => expect(search()).toContain("inflation_series_id=2"));
    await waitFor(() =>
      expect(screen.getByTestId("comparison-caption").textContent).toContain(
        "Внутренняя оценка ПЭО"
      )
    );
    await waitFor(() =>
      expect(screen.getByTestId("inflation-chip-202").getAttribute("title")).not.toBe(firstChip)
    );
    // Целевой месяц СОХРАНЁН: его выбрал человек либо разрешил сервер.
    expect(search()).toContain("target_month=2026-08");
  });

  it("смена ряда при ВЫКЛЮЧЕННОМ приведении по-прежнему числа не меняет", async () => {
    /*
     * Парой к предыдущему: правка §4 не имеет права включить приведение сама.
     * Вторая строка таблицы §2.12 остаётся в силе — выбор ряда сам числа не меняет.
     */
    await renderCompare();
    await selectSeries("Росстат, ИПЦ, декабрь к декабрю");
    const before = screen.getByTestId("comparison-caption").textContent;

    await selectSeries("Внутренняя оценка ПЭО");

    expect(search()).not.toContain("inflation_series_id");
    expect(screen.getByTestId("comparison-caption").textContent).toBe(before);
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
