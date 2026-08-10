import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { delay, http, HttpResponse } from "msw";

import ProjectPassportPage from "./ProjectPassportPage";
import { sampleProjectPassport } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { ProjectPassport } from "@/types/domain";

/**
 * Паспорт проекта по статьям классификатора (Ф6 фазы 7, спека §2.9 пп. 1-4, 10,
 * 11, 14; план, задача 7).
 *
 * Покрывается только шапка документа: титульная полоса, линейка показателей,
 * оговорки коммерческих условий, строка сверки, баннер порчи данных. Таблица по
 * статьям (задача 8) и кольцо структуры (задача 9) сюда не входят — этот файл
 * их не строит и не проверяет.
 *
 * Экран ещё НЕ подключён к маршруту (задача 11 переключит `/contracts/:contractId/
 * passport`): маршрут пока занят экраном фазы 6. Монтируем компонент напрямую
 * внутри `Routes` с тем же шаблоном пути — тот же приём, что у `PassportPage.test.tsx`.
 */
function renderPassport() {
  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:contractId/passport" element={<ProjectPassportPage />} />
    </Routes>,
    { initialRoute: "/contracts/12/passport" }
  );
}

const PROJECT_PASSPORT_URL = "/api/v1/analytics/project-passport/:contractId";

/** Собственный текст пустого состояния — проверяется его ОТСУТСТВИЕ на отказе
 *  (иначе отказ и пустота были бы неразличимы, урок Ф5 §4a). */
const EMPTY_TEXT = /Свод по статьям классификатора появится после первой загрузки сметы/;

function withPassport(overrides: (base: ProjectPassport) => ProjectPassport) {
  server.use(
    http.get(PROJECT_PASSPORT_URL, () => HttpResponse.json(overrides(sampleProjectPassport)))
  );
}

describe("Паспорт проекта: шапка документа", () => {
  /*
   * Тест 1 из 12: четыре состояния блока данных, собранные ОДНИМ параметризованным
   * вызовом (it.each) — четыре кейса, а не четыре отдельные функции.
   */
  it.each([
    {
      name: "загрузка — показан индикатор, а не пустой экран",
      arrange: () => {
        server.use(
          http.get(PROJECT_PASSPORT_URL, async () => {
            await delay(50);
            return HttpResponse.json(sampleProjectPassport);
          })
        );
      },
      assert: async () => {
        expect(await screen.findByText(/Загрузка паспорта проекта/)).toBeInTheDocument();
      },
    },
    {
      name: "отказ — названа причина, а не факт о данных",
      arrange: () => {
        handlerState.projectPassportOutcome = "error";
      },
      assert: async () => {
        expect(
          await screen.findByText(/Не удалось построить паспорт проекта/)
        ).toBeInTheDocument();
        // Отказ — это не «пусто»: при 500 мы не знаем, загружена ли смета вообще.
        expect(screen.queryByText(EMPTY_TEXT)).not.toBeInTheDocument();
      },
    },
    {
      name: "пусто — смета к договору не загружена",
      arrange: () => {
        handlerState.projectPassportOutcome = "no-estimate";
      },
      assert: async () => {
        expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument();
      },
    },
    {
      name: "заполнено — показаны реквизиты договора",
      arrange: () => {
        handlerState.projectPassportOutcome = "full";
      },
      assert: async () => {
        expect(await screen.findByText("ГП-0212")).toBeInTheDocument();
      },
    },
  ])("четыре состояния блока данных: $name", async ({ arrange, assert }) => {
    arrange();
    renderPassport();
    await assert();
  });

  // Тест 2.
  it(
    "«ТЭП не заведены» вместо чисел, вся колонка ₽/м² в прочерках",
    async () => {
      /*
       * ДОКСТРОКА ЧЕСТНО: колонка ₽/м² самой ТАБЛИЦЫ по статьям — предмет
       * задачи 8 (CategoryTable), сюда не входит. Этот тест проверяет только
       * часть шапки — метрику «Стоимость за м²».
       */
      handlerState.projectPassportOutcome = "no-tep";
      renderPassport();

      expect(await screen.findByText("ТЭП не заведены")).toBeInTheDocument();
      expect(screen.getByText("нет ТЭП")).toBeInTheDocument();
    }
  );

  // Тест 3.
  it("ставка НДС «не заявлена в файле», а не ноль", async () => {
    withPassport((base) => ({
      ...base,
      estimate: base.estimate ? { ...base.estimate, vat_rate: null } : null,
    }));
    renderPassport();

    expect(await screen.findByText(/не заявлена в файле/)).toBeInTheDocument();
  });

  // Тест 4.
  it("заявленный 0 % показан как ноль, а не как отсутствие", async () => {
    withPassport((base) => ({
      ...base,
      estimate: base.estimate ? { ...base.estimate, vat_rate: "0" } : null,
    }));
    renderPassport();

    expect(await screen.findByText(/ставка НДС 0\s*%/)).toBeInTheDocument();
    expect(screen.queryByText(/не заявлена в файле/)).not.toBeInTheDocument();
  });

  // Тест 5.
  it("бейдж «у объекта N договоров» при значении больше единицы", async () => {
    withPassport((base) => ({
      ...base,
      contract: { ...base.contract, object_contracts_count: 3 },
    }));
    renderPassport();

    expect(await screen.findByText(/у объекта 3 договора/)).toBeInTheDocument();
  });

  // Тест 6.
  it("бейджа нет при единственном договоре", async () => {
    // sampleProjectPassport.contract.object_contracts_count === 1 (фикстура).
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(screen.queryByText(/у объекта \d+ договор/)).not.toBeInTheDocument();
  });

  // Тест 7.
  it("строка сверки появляется при расхождении", async () => {
    handlerState.projectPassportOutcome = "corrupted"; // delta_to_file_total: "-50000.00"
    renderPassport();

    expect(await screen.findByText(/не сходится с ИТОГО сметы/)).toBeInTheDocument();
  });

  // Тест 8.
  it("строки сверки нет при нулевой дельте", async () => {
    // "full": delta_to_file_total === "0.00" — тишина это нормальный вид.
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(screen.queryByText(/не сходится с ИТОГО сметы/)).not.toBeInTheDocument();
  });

  // Тест 9.
  it("строки сверки нет при неизвестной дельте", async () => {
    // "empty-total": delta_to_file_total === null — известен только один операнд.
    handlerState.projectPassportOutcome = "empty-total";
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(screen.queryByText(/не сходится с ИТОГО сметы/)).not.toBeInTheDocument();
  });

  // Тест 10.
  it("баннер порчи появляется при ненулевом счётчике", async () => {
    handlerState.projectPassportOutcome = "corrupted"; // positions_rows_not_finite: 5
    renderPassport();

    expect(await screen.findByText(/5 позиций/)).toBeInTheDocument();
    expect(screen.getByText(/итог неполон/i)).toBeInTheDocument();
  });

  // Тест 11.
  it("баннера порчи нет при нулевом счётчике", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(screen.queryByText(/итог неполон/i)).not.toBeInTheDocument();
  });

  // Тест 12.
  it("оговорки зажаты по высоте и не несут класса block", async () => {
    const { container } = renderPassport();
    await screen.findByText("ГП-0212");

    const clamped = container.querySelectorAll('[data-print="clamp"]');
    expect(clamped.length).toBeGreaterThan(0);
    clamped.forEach((node) => {
      const classes = Array.from(node.classList);
      expect(classes.some((c) => /^line-clamp-\d+$/.test(c))).toBe(true);
      expect(classes).not.toContain("block");
    });
  });

  /**
   * Заведён по находке внешнего круга. Пустое состояние уходило ранним
   * возвратом ДО шапки и при этом писало «реквизиты договора заведены и не
   * пострадали», не показывая ни одного из них: текст утверждал ровно то, что
   * экран скрывал. Спека §2.4 объясняет ответ `200` без сметы именно тем, что
   * «реквизиты уже есть что показать», — значит пустым состоянием заменяются
   * только блоки, зависящие от сметы.
   */
  it("реквизиты договора видны и тогда, когда смета не загружена", async () => {
    handlerState.projectPassportOutcome = "no-estimate";
    renderPassport();

    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument();
    expect(screen.getByText("ГП-0212")).toBeInTheDocument();
    expect(screen.getByText(/ЖК Заречный/)).toBeInTheDocument();
    expect(screen.getByText(/СтройГарант/)).toBeInTheDocument();
    expect(screen.getByText(/Смирнов А\.В\./)).toBeInTheDocument();
  });

  /**
   * Заведён по находке финального ревью ветки: требование §2.9 п. 2 — САМ
   * СОСТАВ шапки — исполнителя не имело. Состояние «заполнено» из теста 1
   * утверждало только номер договора, то есть выпадение подписанта, класса,
   * подрядчика, даты или раскладки площади не уронило бы ни одного теста.
   * Требование спеки без исполнителя либо получает тест, либо объявляется
   * границей; здесь выбран тест.
   */
  it("шапка несёт весь состав §2.9 п. 2", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    // Реквизиты договора.
    expect(screen.getByText(/ЖК Заречный/)).toBeInTheDocument();
    expect(screen.getByText(/СтройГарант/)).toBeInTheDocument();
    expect(screen.getByText(/Жилые дома/)).toBeInTheDocument();
    expect(screen.getByText(/Смирнов А\.В\./)).toBeInTheDocument();
    expect(screen.getByText(/15\.05\.2025/)).toBeInTheDocument();

    // Линейка показателей: общая площадь с раскладкой на подземную и надземную.
    const sheet = screen.getByTestId("passport-metrics");
    expect(sheet).toHaveTextContent(/47\s*000/);
    expect(sheet).toHaveTextContent(/40\s*000/);
    expect(sheet).toHaveTextContent(/7\s*000/);
    // Стоимость с НДС и ставка подписью, стоимость за м², три условия договора.
    expect(sheet).toHaveTextContent(/ставка НДС 20\s*%/);
    expect(sheet).toHaveTextContent(/30/);
    expect(sheet).toHaveTextContent(/10/);
    expect(sheet).toHaveTextContent(/5/);
  });
});

/**
 * Таблица по статьям классификатора (задача 8, спека §2.9 пп. 5-9, 12, 15).
 *
 * `sampleProjectPassport.categories` — плоский список, глубина обхода (правило 2
 * §2.6). Корни (`parent_id: null`): 01 Земляные работы, 99 Кровельные работы
 * (корзина), 03 Отделочные работы (отсутствует — `total: null`), 04 Инженерные
 * сети (свои деньги + два раздела в `own_sections`), 05 Фасадные, 06 Устройство
 * кровли, 07 Электромонтажные, 08 Слаботочные системы, 09 Благоустройство,
 * 10 Прочие работы. У 01 — дети 01.01 (Разработка грунта, деньги) и 01.02
 * (Водопонижение, `total: "0.00"`). У 04 — дети 04.01 (свои деньги, лист) и
 * 04.02 (лист без своих денег, но со строкой допработ). «Нераспределённое»:
 * `chapters: 2`, `rows_outside_structure: 1` — обе причины ненулевые сразу.
 */
describe("Паспорт проекта: таблица по статьям", () => {
  // Тест 1.
  it("по умолчанию видны только корни, «Нераспределённое» и итог", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    // Запросы ограничены таблицей: задача 9 добавила на ту же страницу легенду
    // кольца структуры, которая дословно повторяет названия топ-8 статей и
    // «Нераспределённое» — глобальный `screen.getByText` стал бы неоднозначен.
    const table = screen.getByRole("table");
    const rootTitles = sampleProjectPassport.categories
      .filter((c) => c.parent_id === null)
      .map((c) => c.title);
    for (const title of rootTitles) {
      expect(within(table).getByText(title)).toBeInTheDocument();
    }
    expect(within(table).getByText("Нераспределённое")).toBeInTheDocument();
    expect(within(table).getByText("Итого по договору")).toBeInTheDocument();

    // Известный дочерний узел («Разработка грунта», ребёнок «Земляных работ»)
    // не показан, пока родитель свёрнут.
    expect(within(table).queryByText("Разработка грунта")).not.toBeInTheDocument();
  });

  // Тест 2.
  it("раскрытие показывает подстатьи", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть статью 01" }));

    expect(await screen.findByText("Разработка грунта")).toBeInTheDocument();
  });

  // Тест 3 (3 собрано): прочерк, «цена не заполнена» и ноль — три разных вида.
  // Каждый кейс утверждает ОТСУТСТВИЕ двух других видов — иначе они были бы
  // взаимозаменимы (спека §2.3, правило 1).
  it.each([
    {
      name: "прочерк — статьи нет в смете (total: null, rows: 0)",
      code: "03",
      override: (base: ProjectPassport) => base,
      expect: "dash" as const,
    },
    {
      name: "«без цены: N» — сумма частична (rows_priced < rows)",
      code: "01",
      override: (base: ProjectPassport) => ({
        ...base,
        categories: base.categories.map((c) =>
          c.code === "01"
            ? { ...c, total: "300000.00", rows: 51, rows_priced: 41, rows_not_finite: 0 }
            : c
        ),
      }),
      expect: "incomplete" as const,
    },
    {
      name: "0,00 как число — сумма ровно ноль, но позиции есть",
      code: "09",
      override: (base: ProjectPassport) => ({
        ...base,
        categories: base.categories.map((c) =>
          c.code === "09"
            ? { ...c, total: "0.00", rows: 40, rows_priced: 40, rows_not_finite: 0 }
            : c
        ),
      }),
      expect: "zero" as const,
    },
  ])("прочерк, «цена не заполнена» и ноль — три разных вида: $name", async (testCase) => {
    withPassport(testCase.override);
    renderPassport();
    await screen.findByText("ГП-0212");

    const amountCell = screen.getByTestId(`amount-cat-${testCase.code}`);
    // Точное сравнение текста, а не подстрокой: "300 000,00 ₽" тоже содержит
    // "0,00" как подстроку, и regex-поиск спутал бы частичную сумму с нулевой.
    const amountText = (amountCell.textContent ?? "").replace(/\u00A0/g, " ").trim();
    const hasDash = amountText === "—";
    const hasZero = amountText === "0,00 ₽";
    const incompleteness = screen.queryByTestId(`incompleteness-cat-${testCase.code}`);

    if (testCase.expect === "dash") {
      expect(hasDash).toBe(true);
      expect(hasZero).toBe(false);
      expect(incompleteness).not.toBeInTheDocument();
    } else if (testCase.expect === "zero") {
      expect(hasZero).toBe(true);
      expect(hasDash).toBe(false);
      expect(incompleteness).not.toBeInTheDocument();
    } else {
      expect(hasDash).toBe(false);
      expect(hasZero).toBe(false);
      expect(incompleteness).toHaveTextContent("без цены: 10");
    }
  });

  // Тест 4.
  it("«Без подстатьи» есть, когда у статьи есть дети и свои деньги", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    // 04 «Инженерные сети»: есть дети (04.01, 04.02) и own: "100000.00" > 0.
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));

    expect(await screen.findByText("Без подстатьи")).toBeInTheDocument();
  });

  // Тест 5.
  it("«Без подстатьи» отсутствует при own_rows = 0", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    // 04.02 «Пусконаладочные работы»: own_rows: 0 — раскрытие даёт только
    // строку допработ, никакой служебной строки собственных денег. Проверяем
    // ОТСУТСТВИЕ именно строки own-04.02: у 04 своя служебная строка есть
    // (own_rows: 10 > 0), и глобальный поиск текста спутал бы их.
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));
    await user.click(await screen.findByRole("button", { name: "Развернуть статью 04.02" }));

    expect(screen.queryByTestId("row-own-04.02")).not.toBeInTheDocument();
  });

  // Тест 6.
  it("у листа с допработами служебная строка называется «Позиции сметы»", async () => {
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) =>
        c.code === "04.02"
          ? { ...c, own: "5000.00", own_rows: 1, own_rows_priced: 1, own_rows_not_finite: 0 }
          : c
      ),
    }));
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    // 04.02 — лист (детей нет), раскрытие вызвано own И extras одновременно;
    // без детей название обязано быть «Позиции сметы», а не «Без подстатьи».
    // Проверка — внутри СВОЕЙ строки own-04.02: у родителя 04 своя строка
    // «Без подстатьи» есть и законно видна рядом (own_rows: 10 > 0).
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));
    await user.click(await screen.findByRole("button", { name: "Развернуть статью 04.02" }));

    const ownRow = await screen.findByTestId("row-own-04.02");
    expect(within(ownRow).getByText("Позиции сметы")).toBeInTheDocument();
    expect(within(ownRow).queryByText("Без подстатьи")).not.toBeInTheDocument();
  });

  // Тест 7.
  it("подпись служебной строки называет раздел сметы", async () => {
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) =>
        c.code === "04" ? { ...c, own_sections: [{ id: 1, number: "6.5", title: "Прочее" }] } : c
      ),
    }));
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));

    expect(await screen.findByText(/раздел сметы 6\.5 «Прочее»/)).toBeInTheDocument();
    // Подпись не выведена из кода статьи (04) — файловая нумерация и коды
    // классификатора разные оси (§1.5 спеки).
    expect(screen.queryByText(/раздел сметы 04\b/)).not.toBeInTheDocument();
  });

  // Тест 8.
  it("подпись называет оба раздела, когда их два", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    // Фикстура: 04 несёт own_sections из ДВУХ разделов (4.1 и 4.2) как есть.
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));

    const caption = await screen.findByTestId("own-caption-04");
    expect(caption).toHaveTextContent("4.1");
    expect(caption).toHaveTextContent("4.2");
  });

  // Тест 9.
  it("строка допработ с бейджем внутри своей статьи", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));
    await user.click(await screen.findByRole("button", { name: "Развернуть статью 04.02" }));

    expect(
      await screen.findByText("Пусконаладочные работы по инженерным сетям (доп. соглашение к смете)")
    ).toBeInTheDocument();
    expect(screen.getByText("доп. работы")).toBeInTheDocument();
  });

  // Тест 10.
  it("«Нераспределённое» видимо и названо", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    // Ограничено таблицей — легенда кольца структуры (задача 9) тоже называет
    // «Нераспределённое» отдельной строкой, глобальный поиск был бы неоднозначен.
    const table = screen.getByRole("table");
    expect(within(table).getByText("Нераспределённое")).toBeInTheDocument();
    expect(within(table).getByText(/2 раздела сметы без статьи классификатора/)).toBeInTheDocument();
  });

  // Тест 11.
  it("строки вне структуры названы отдельной причиной", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    // Фикстура: chapters: 2, rows_outside_structure: 1 — оба счётчика
    // одновременно ненулевые и различны; ни один не подменяет другой.
    const caption = screen.getByTestId("unallocated-caption");
    expect(caption).toHaveTextContent(/2 раздела сметы без статьи классификатора/);
    expect(caption).toHaveTextContent(/1 позиция вне структуры сметы/);
  });

  // Тест 12.
  it("корзина показана с бейджем", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    // 99 «Кровельные работы» — is_bucket: true, корень, виден по умолчанию.
    const row = screen.getByTestId("row-cat-99");
    expect(within(row).getByText("корзина")).toBeInTheDocument();
  });

  // Тест 13.
  it("переключатель нулевых по умолчанию выключен", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    const toggle = screen.getByRole("switch", { name: /показывать нулевые подстатьи/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    // 01.02 «Водопонижение»: total "0.00", глубина > 0 — скрыт, пока
    // переключатель выключен, даже после раскрытия родителя.
    await user.click(screen.getByRole("button", { name: "Развернуть статью 01" }));
    expect(screen.queryByText("Водопонижение")).not.toBeInTheDocument();
  });

  // Тест 14.
  it("включение переключателя показывает нулевые подстатьи", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть статью 01" }));
    await user.click(screen.getByRole("switch", { name: /показывать нулевые подстатьи/i }));

    expect(await screen.findByText("Водопонижение")).toBeInTheDocument();
  });

  // Тест 15.
  it("подпись неполноты на смешанном узле", async () => {
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) =>
        c.code === "07" ? { ...c, rows: 10, rows_priced: 9, rows_not_finite: 0 } : c
      ),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(await screen.findByTestId("incompleteness-cat-07")).toHaveTextContent("без цены: 1");
  });

  // Тест 16.
  it("подпись называет обе причины, когда обе есть", async () => {
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) =>
        c.code === "08" ? { ...c, rows: 10, rows_priced: 7, rows_not_finite: 2 } : c
      ),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const caption = await screen.findByTestId("incompleteness-cat-08");
    // Обе причины — разными числами, не одна вместо другой (урок Ф4a).
    expect(caption).toHaveTextContent("без цены: 1");
    expect(caption).toHaveTextContent("с ошибкой: 2");
  });

  // Тест 17.
  it("наименование статьи зажато по высоте и не несёт класса block", async () => {
    const { container } = renderPassport();
    await screen.findByText("ГП-0212");

    const clamped = container.querySelectorAll('[data-print="clamp"]');
    // 3 из шапки (аванс/гарантия/удержание) + хотя бы один заголовок статьи.
    expect(clamped.length).toBeGreaterThan(3);
    clamped.forEach((node) => {
      const classes = Array.from(node.classList);
      expect(classes.some((c) => /^line-clamp-\d+$/.test(c))).toBe(true);
      expect(classes).not.toContain("block");
    });
  });

  /**
   * Сверх списка задачи 8, решением оркестратора. Спека §2.3 последним абзацем
   * распространяет все три правила на СОБСТВЕННЫЕ деньги статьи — по `own_rows`,
   * `own_rows_priced` и `own_rows_not_finite`, — а список тестов плана этого не
   * закрывал: служебная строка показывала частичную сумму числом и без единого
   * признака неполноты, то есть ровно то, что §2.3 объявляет недопустимым для
   * узла. Требование спеки без исполнителя либо получает тест, либо объявляется
   * границей; здесь выбран тест.
   */
  it("подпись неполноты стоит и на служебной строке собственных денег", async () => {
    const user = userEvent.setup();
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) =>
        c.code === "04"
          ? { ...c, own_rows: 10, own_rows_priced: 7, own_rows_not_finite: 2 }
          : c
      ),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));

    const caption = await screen.findByTestId("own-incompleteness-04");
    // Обе причины разными числами — не одна вместо другой (урок Ф4a).
    expect(caption).toHaveTextContent("без цены: 1");
    expect(caption).toHaveTextContent("с ошибкой: 2");
  });

  /**
   * Заведён по находке финального ревью ветки. Доля строки «Итого по договору»
   * была записана литералом «100,00 %» и потому утверждала «сто процентов от
   * неизвестной суммы» при пустом итоге и «сто процентов от нуля» при нулевом —
   * ровно то, что правило 5 §2.6 запрещает всем прочим строкам, чей `share_pct`
   * в этих случаях приходит `null`. Ни один тест ячейку не стерёг.
   */
  it.each([
    { name: "итог не определён", outcome: "empty-total" as const },
    { name: "итог равен нулю", outcome: "zero-total" as const },
  ])("доля итога — прочерк, когда $name", async ({ outcome }) => {
    handlerState.projectPassportOutcome = outcome;
    renderPassport();
    await screen.findByText("ГП-0212");

    const grand = await screen.findByTestId("row-grand-total");
    expect(grand).not.toHaveTextContent("100,00 %");
    expect(grand).toHaveTextContent("—");
  });
});

/**
 * Кольцо структуры (задача 9, спека §2.10).
 *
 * `sampleProjectPassport` уже даёт ДЕВЯТЬ корней с известной суммой (01, 99, 04,
 * 05, 06, 07, 08, 09, 10) и ОДИН с неизвестной («03 Отделочные работы»,
 * `total: null`) — этого хватает и на топ-8, и на свёрнутую девятую статью
 * («Инженерные сети», 175 000 — меньше всех восьми), и на статью, которая не
 * входит никуда. Отдельного `server.use()` для композиции не нужно (по сумме,
 * убыванием): 05 (900 000), 06 (800 000), 07 (700 000), 08 (600 000),
 * 01 (500 000), 09 (400 000), 99 (300 000), 10 (200 000) — топ-8; 04 (175 000) —
 * «Остальные статьи (1)»; 03 (null) — не в кольце и не в «Остальных».
 *
 * Проверки легенды идут ЧЕРЕЗ `within(legend)`: те же названия статей рендерит
 * и таблица (задача 8) на той же странице — глобальный `screen.getByText` был
 * бы неоднозначен.
 */
describe("Паспорт проекта: кольцо структуры", () => {
  // Тест 1.
  it("легенда несёт восемь крупнейших статей", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const legend = screen.getByTestId("structure-ring-legend");
    const top8Titles = [
      "Фасадные работы",
      "Устройство кровли",
      "Электромонтажные работы",
      "Слаботочные системы",
      "Земляные работы",
      "Благоустройство",
      "Кровельные работы",
      "Прочие работы",
    ];
    for (const title of top8Titles) {
      expect(within(legend).getByText(title)).toBeInTheDocument();
    }
    // Девятая по сумме статья («Инженерные сети», 175 000) отдельной строкой не
    // показана — она свёрнута в «Остальные» (тест ниже).
    expect(within(legend).queryByText("Инженерные сети")).not.toBeInTheDocument();
  });

  // Тест 2.
  it("девятая и дальше сведены в «Остальные статьи (N)»", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const legend = screen.getByTestId("structure-ring-legend");
    expect(within(legend).getByText("Остальные статьи (1)")).toBeInTheDocument();
  });

  // Тест 3.
  it("«Нераспределённое» названо в легенде отдельно", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const legend = screen.getByTestId("structure-ring-legend");
    expect(within(legend).getByText("Нераспределённое")).toBeInTheDocument();
  });

  // Тест 4.
  it("при неопределённой сумме кольца нет и сказано, что сумма не определена", async () => {
    handlerState.projectPassportOutcome = "empty-total";
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(
      await screen.findByText(/структура не строится: сумма по смете не определена/)
    ).toBeInTheDocument();
    expect(screen.queryByTestId("structure-ring-legend")).not.toBeInTheDocument();
  });

  // Тест 5.
  it("при нулевой сумме кольца нет и сказано, что сумма равна нулю", async () => {
    handlerState.projectPassportOutcome = "zero-total";
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(
      await screen.findByText(/структура не строится: сумма по смете равна нулю/)
    ).toBeInTheDocument();
    // Формулировка ДРУГОГО случая («сумма не определена») здесь появиться не
    // должна — два разных факта о смете не взаимозаменимы (правило 6 §2.10).
    expect(
      screen.queryByText(/структура не строится: сумма по смете не определена/)
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("structure-ring-legend")).not.toBeInTheDocument();
  });

  // Тест 6.
  it("статья с неизвестной суммой не входит в «Остальные»", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    // Фикстура: «Отделочные работы» (03) — total: null, корень, рядом с девятью
    // известными. Если бы неизвестная сумма молча сворачивалась в «Остальные»
    // нулём, счётчик стал бы (2), а не (1).
    const legend = screen.getByTestId("structure-ring-legend");
    expect(within(legend).queryByText("Отделочные работы")).not.toBeInTheDocument();
    expect(within(legend).getByText("Остальные статьи (1)")).toBeInTheDocument();
  });
});

/**
 * Печать (задача 10, спека §2.11): разметка `data-print`, которую читает
 * `@media print` в `frontend/src/index.css`.
 *
 * ГРАНИЦА НАБЛЮДАЕМОСТИ, честно: jsdom не применяет `@media print` вовсе —
 * эти тесты проверяют ТОЛЬКО разметку (атрибуты и классы), а не саму печатную
 * раскладку — не ширины колонок, не повтор шапки таблицы на каждом листе, не
 * уменьшенное кольцо. Раскладку проверяет замер в браузере (план, задача 12).
 * Четыре зелёных теста здесь означают «разметка на месте», а не «печать
 * доказана».
 */
describe("Паспорт проекта: печать", () => {
  // Тест 1.
  it('лист документа помечен data-print="sheet"', async () => {
    const { container } = renderPassport();
    await screen.findByText("ГП-0212");

    const sheets = container.querySelectorAll('[data-print="sheet"]');
    expect(sheets).toHaveLength(1);

    // Обёртывает документ целиком — несёт и шапку, и таблицу по статьям, а не
    // только один из блоков.
    const sheet = sheets[0] as HTMLElement;
    expect(within(sheet).getByText("ГП-0212")).toBeInTheDocument();
    expect(within(sheet).getByRole("table")).toBeInTheDocument();
  });

  // Тест 2 (2 собрано): обе служебные метки — переключатель и кнопка печати —
  // документом не являются и обязаны нести data-print="hide".
  it.each([
    {
      name: "переключатель нулевых подстатей",
      find: () => screen.getByRole("switch", { name: /показывать нулевые подстатьи/i }),
    },
    {
      name: "кнопка печати",
      find: () => screen.getByRole("button", { name: /печать/i }),
    },
    /*
      Два случая ниже заведены по находке внешнего круга. Метка стояла на самом
      `Switch`, а не на его полосе, поэтому на бумагу уезжала осиротевшая
      подпись «показывать нулевые подстатьи» с разделительной чертой; у кнопок
      раскрытия дерева метки не было вовсе, и шевроны печатались, хотя на
      бумаге раскрывать нечего. §2.11 требует, чтобы служебные элементы
      уходили, а CSS скрывает ровно то, что помечено.
    */
    {
      name: "подпись переключателя нулевых подстатей",
      find: () => screen.getByText(/показывать нулевые подстатьи/i),
    },
    {
      name: "кнопка раскрытия статьи",
      find: () => screen.getAllByRole("button", { name: /Развернуть статью/i })[0],
    },
  ])('служебные элементы помечены data-print="hide": $name', async ({ find }) => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const el = find();
    expect(el.closest('[data-print="hide"]')).not.toBeNull();
  });

  // Тест 3.
  it("зажатые по высоте узлы внутри листа не несут класса block", async () => {
    const { container } = renderPassport();
    await screen.findByText("ГП-0212");

    // Область поиска — ВНУТРИ листа: печатные правила действуют на то, что
    // находится внутри `[data-print="sheet"]`, а не на страницу целиком —
    // зажатый узел снаружи листа ими бы не управлялся.
    const clamped = container.querySelectorAll('[data-print="sheet"] [data-print="clamp"]');
    expect(clamped.length).toBeGreaterThan(0);
    clamped.forEach((node) => {
      const classes = Array.from(node.classList);
      expect(classes.some((c) => /^line-clamp-\d+$/.test(c))).toBe(true);
      expect(classes).not.toContain("block");
    });
  });
});
