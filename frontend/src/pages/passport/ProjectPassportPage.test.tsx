import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
 * Экран занимает маршрут `/contracts/:contractId/passport` (`App.tsx`); экран
 * фазы 6 удалён этой же веткой. Компонент монтируется напрямую внутри `Routes`
 * с тем же шаблоном пути — без него `useParams` пуст, запрос не уходит вовсе, и
 * экран навсегда остаётся скелетоном.
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
   * Заведён по второму кругу внешнего ревью. Оставив шапку при отсутствующей
   * смете, первая правка перенесла в это состояние подпись «ставка НДС не
   * заявлена в файле · исходная смета договора» — два утверждения о документе,
   * которого ещё нет. «Ставка не заявлена в ЗАГРУЖЕННОМ файле» и «файла нет
   * вовсе» — разные состояния, и смешивать их значит повторять ошибку, за
   * которую фаза уже платила на «нет норматива» против «0 %».
   */
  it("подпись стоимости не говорит о файле, которого нет", async () => {
    handlerState.projectPassportOutcome = "no-estimate";
    renderPassport();
    await screen.findByText("ГП-0212");

    const metrics = screen.getByTestId("passport-metrics");
    expect(metrics).toHaveTextContent(/смета не загружена/);
    expect(metrics).not.toHaveTextContent(/не заявлена в файле/);
    expect(metrics).not.toHaveTextContent(/исходная смета договора/);
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
        c.code === "04"
          ? { ...c, own_sections: [{ id: 1, number: "6.5", title: "Прочее", source: "file" }] }
          : c
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

  /**
   * Ф6a, задача 2: доля печатается РОВНО двумя знаками (`minimumFractionDigits:
   * 2` макета гейта 1).
   *
   * До этой фичи колонку «Доля» не стерёг НИ ОДИН тест — у ячейки не было даже
   * `data-testid` (замер плана §1.1), и оба дефекта показа поэтому пережили 52
   * снятия защиты Ф6. Сравнение — ТОЧНОЕ, а не подстрокой: «11,10 %» содержит
   * «1,10 %» подстрокой, и `toHaveTextContent` спутал бы одну долю с другой.
   */
  it.each([
    {
      name: "ноль — «0,00 %», а не «0 %»",
      // Форма стенда после правки §2.1: ноль от деления приезжает "0".
      code: "09",
      share: "0",
      expected: "0,00 %",
    },
    {
      name: "хвостовой ноль второго знака добивается",
      code: "01",
      share: "1.1",
      expected: "1,10 %",
    },
    {
      // Этот случай зелен и ДО правки: округление длинной дроби `roundDecimal`
      // делал и раньше. Он стоит здесь как граница — чтобы «ровно два знака» не
      // оказалось реализовано обрезанием или, наоборот, показом всех знаков.
      name: "длинная дробь округляется до двух знаков",
      code: "05",
      share: "19.14893617021276595744680851",
      expected: "19,15 %",
    },
  ])("доля в таблице — ровно два знака: $name", async ({ code, share, expected }) => {
    withPassport((base) => ({
      ...base,
      // Меняется ТОЛЬКО доля: сумма остаётся прежней, поэтому строка видна и
      // вход нарушает ровно одно (GC 22 — иначе фильтр нулевых скрыл бы строку,
      // и тест измерял бы фильтр, а не формат).
      categories: base.categories.map((c) => (c.code === code ? { ...c, share_pct: share } : c)),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const cell = await screen.findByTestId(`share-cat-${code}`);
    expect((cell.textContent ?? "").replace(/\s+/g, " ").trim()).toBe(expected);
  });

  /**
   * Ф6a, задача 3: ₽/м² округляется до двух знаков во всех местах показа.
   *
   * `_per_sqm` делит `Decimal` на `Decimal`, и контекст Python даёт 28 значащих
   * цифр: на стенде в ячейку уезжало 22 знака после запятой. `MoneyCell` вызван
   * без `maxFractionDigits`, а `formatDecimalMoney` без него значащие цифры не
   * округляет намеренно (утверждённую ставку округлять нельзя). Правило «только
   * для вычисленных величин» записано в докстроке самого `MoneyCell` — оно не
   * новое, просто не было прогнано по новым точкам вызова.
   *
   * Фикстура несёт ФОРМУ СТЕНДА (26-28 знаков), а не короткую десятичную —
   * иначе тест судил бы не о том (план §1.2).
   */
  it.each([
    { name: "строка статьи", testid: "per-sqm-cat-01", expected: "10,64 ₽" },
    { name: "«Нераспределённое»", testid: "per-sqm-unallocated", expected: "2,66 ₽" },
    { name: "«Итого по договору»", testid: "per-sqm-grand-total", expected: "123,46 ₽" },
  ])("₽/м² — два знака: $name", async ({ testid, expected }) => {
    withPassport((base) => ({
      ...base,
      // Итог фикстуры делится на площадь БЕЗ остатка (4 700 000 / 47 000 = 100),
      // поэтому у строки итога длинной дроби нет вовсе — округлять было бы
      // нечего, и случай оказался бы вакуозным. Здесь подставлена форма стенда.
      totals: { ...base.totals, per_sqm: "123.4567890123456789012345679" },
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const cell = await screen.findByTestId(testid);
    expect((cell.textContent ?? "").replace(/\s+/g, " ").trim()).toBe(expected);
  });

  // Ф6a, задача 3: показатель шапки — четвёртая точка показа ₽/м² (спека §2.2).
  it("₽/м² в шапке — два знака", async () => {
    withPassport((base) => ({
      ...base,
      totals: { ...base.totals, per_sqm: "123.4567890123456789012345679" },
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const metric = await screen.findByTestId("metric-per-sqm");
    expect((metric.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("123,46 ₽");
  });

  /**
   * Ф6a, задача 3, вторая половина DoD: округление на слое показа НЕ теряет
   * точную величину — `MoneyCell` кладёт её в `title` сам, и только когда
   * округление действительно что-то изменило. Сервер продолжает отдавать точное
   * значение (§3 спеки: на сервере не квантуем).
   */
  it("точное значение ₽/м² остаётся доступным в подсказке", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const cell = await screen.findByTestId("per-sqm-cat-01");
    const exact = within(cell).getByTitle(/Точное значение/);
    // Все 26 знаков фикстуры, а не округлённые два: подсказка, повторяющая
    // видимое, ничего не сохраняла бы.
    expect(exact.getAttribute("title")).toContain("10,63829787234042553191489362");
  });

  // Ф6a, задача 2: у «Нераспределённого» своя ячейка доли и свой testid.
  it("доля «Нераспределённого» — ровно два знака", async () => {
    withPassport((base) => ({
      ...base,
      unallocated: { ...base.unallocated, share_pct: "0" },
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const cell = await screen.findByTestId("share-unallocated");
    expect((cell.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("0,00 %");
  });
});

/**
 * Панель-верстак разноса (задача 8): шеврон строки «Нераспределённое»
 * разворачивает `UnallocatedPanel` под ней. Содержимое панели (дерево,
 * поиск статьи, «разнесено вручную») проверяет `UnallocatedPanel.test.tsx` —
 * он монтирует панель напрямую с синтетическими `contractId`/`estimateId`.
 * Здесь — только разводка: панель обязана всплыть по клику ровно там, где
 * `CategoryTable` её монтирует, и получить id маршрута и id сметы РЕАЛЬНОЙ
 * фикстуры, а не выдуманные (task-8-controller-notes: два источника id —
 * ровно то, из-за чего инвалидация тихо перестаёт совпадать).
 */
describe("Паспорт проекта: панель-верстак разноса", () => {
  it("шеврон «Нераспределённого» разворачивает и сворачивает панель", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(screen.queryByTestId("unallocated-panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Развернуть нераспределённое" }));
    expect(await screen.findByTestId("unallocated-panel")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Свернуть нераспределённое" }));
    expect(screen.queryByTestId("unallocated-panel")).not.toBeInTheDocument();
  });

  it("строка панели не попадает в печатный поток", async () => {
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть нераспределённое" }));
    const panelRow = await screen.findByTestId("row-unallocated-panel");
    expect(panelRow).toHaveAttribute("data-print", "hide");
  });

  it("мутация разноса несёт id раздела и id сметы фикстуры, а не выдуманные", async () => {
    // sampleProjectPassport: estimate.id 600. Раздел 5001 — корень дерева
    // нераспределённого, category_options id 8 / код "04.02" — «Пусконаладочные
    // работы», не участвующая иначе в этом файле. `contractId` эта проверка
    // НЕ покрывает: эндпоинт `PUT .../category-overrides/:positionItemId` его
    // не несёт вовсе (он идёт только в инвалидацию) — см. тест провенанса ниже.
    const received: { estimateId: string; positionItemId: string } = {
      estimateId: "",
      positionItemId: "",
    };
    server.use(
      http.put(
        "/api/v1/estimates/:estimateId/category-overrides/:positionItemId",
        ({ params }) => {
          received.estimateId = String(params.estimateId);
          received.positionItemId = String(params.positionItemId);
          return HttpResponse.json({
            chapters_updated: 1,
            additional_works_updated: 0,
            chapters_manual: 1,
          });
        }
      )
    );
    const user = userEvent.setup();
    renderPassport();
    await screen.findByText("ГП-0212");

    await user.click(screen.getByRole("button", { name: "Развернуть нераспределённое" }));
    await user.click(await screen.findByTestId("pick-category-5001"));
    await user.click(await screen.findByText("Пусконаладочные работы"));

    await waitFor(() => expect(received.positionItemId).toBe("5001"));
    expect(received.estimateId).toBe("600");
  });

  /**
   * Finding I-2 (ревью 1). Прежний тест назывался «по id маршрута», но не мог
   * это проверить: эндпоинт `PUT .../category-overrides/:positionItemId` не
   * несёт `contractId` вовсе — тот идёт ТОЛЬКО в
   * `qc.invalidateQueries({ queryKey: qk.passport.project(contractId) })`
   * (`services/queries.ts`). Хуже: маршрут этого файла — `/contracts/12/…`,
   * а `sampleProjectPassport.contract.id` — тоже `12`, и подмена
   * `contractId={passport.contract.id}` в `CategoryTable` (ровно то, что
   * запрещают controller-notes) проходила бы этим тестом незамеченной — оба
   * источника совпадали.
   *
   * Пробный сценарий: маршрут `/contracts/77/passport`, а «сервер» отвечает
   * паспортом, чей `contract.id` — 12 (фикстура не трогается). Это НЕ
   * состояние, которое отдаёт бэкенд в реальной работе (паспорт по маршруту
   * `:contractId` всегда несёт `contract.id` того же договора) — это
   * намеренный зонд, разводящий два источника id, которые совпадают
   * ВСЮДУ ЕЩЁ в этом файле. Наблюдаем не запрос (в нём id нет), а
   * ИНВАЛИДАЦИЮ: если `contractId` мутации — id маршрута (77), успешный PUT
   * инвалидирует активный запрос паспорта, и `GET
   * .../project-passport/77` уходит повторно. Если бы `contractId` брали из
   * `passport.contract.id` (12), инвалидация целила бы в ключ, на который
   * никто не подписан, и повторного запроса не было бы вовсе — тест увис бы
   * на `waitFor` и покраснел по таймауту.
   */
  it("мутация инвалидирует паспорт по id МАРШРУТА, а не по contract.id из ответа", async () => {
    let hitsForRoute77 = 0;
    server.use(
      http.get("/api/v1/analytics/project-passport/:contractId", ({ params }) => {
        if (String(params.contractId) === "77") hitsForRoute77 += 1;
        // contract.id фикстуры остаётся 12 — намеренное несовпадение с
        // маршрутом 77, см. докстроку теста.
        return HttpResponse.json(sampleProjectPassport);
      }),
      http.put(
        "/api/v1/estimates/:estimateId/category-overrides/:positionItemId",
        () =>
          HttpResponse.json({
            chapters_updated: 1,
            additional_works_updated: 0,
            chapters_manual: 1,
          })
      )
    );
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/contracts/:contractId/passport" element={<ProjectPassportPage />} />
      </Routes>,
      { initialRoute: "/contracts/77/passport" }
    );
    await screen.findByText("ГП-0212");
    await waitFor(() => expect(hitsForRoute77).toBe(1));

    await user.click(screen.getByRole("button", { name: "Развернуть нераспределённое" }));
    await user.click(await screen.findByTestId("pick-category-5001"));
    await user.click(await screen.findByText("Пусконаладочные работы"));

    await waitFor(() => expect(hitsForRoute77).toBe(2));
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

  /**
   * Ф6a, задача 2: проценты ЛЕГЕНДЫ — ровно два знака.
   *
   * Легенда — второй показ доли, и до Ф6a у неё был СВОЙ форматтер с тем же
   * телом (`formatShareText`). Пробел нашло внешнее ревью, а породила его сама
   * дубликация: добивание до двух знаков появилось бы только в таблице (спека
   * §1.3). Замер Ф6 проценты легенды не смотрел вовсе — поэтому живой дефект и
   * доехал до стенда.
   */
  it("процент статьи в легенде — ровно два знака", async () => {
    const facade = sampleProjectPassport.categories.find((c) => c.code === "05")!;
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) => (c.code === "05" ? { ...c, share_pct: "0" } : c)),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const share = await screen.findByTestId(`legend-share-cat-${facade.id}`);
    expect((share.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("0,00 %");
  });

  it("процент «Остальных статей» в легенде — ровно два знака", async () => {
    /*
      Третий путь §1.3 спеки, живой БЕЗ всяких правок: долю «Остальных» кольцо
      считает суммой через `addDecimalStrings`, а тот срезает хвостовые нули —
      1,10 приезжает строкой "1.1" и печаталось «1,1 %» рядом с «12,72 %».
      «Инженерные сети» (04) — единственная свёрнутая статья фикстуры.
    */
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) => (c.code === "04" ? { ...c, share_pct: "1.10" } : c)),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const share = await screen.findByTestId("legend-share-rest");
    expect((share.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("1,10 %");
  });

  /**
   * Ф6a, задача 4: живой дефект §1.4 спеки — процент «Остальных статей» гаснет
   * ЦЕЛИКОМ.
   *
   * `addDecimalStrings` не разбирает `"0E+2"` и возвращает `null`, а свёртка в
   * `buildSlices` по первому же `null` гасит всю сумму — легенда опускает
   * процент. На стенде его не было показано прямо сейчас, до всяких правок.
   *
   * **Дефект живёт НА СТЫКЕ, и одним тестом не закрывается** (граница, названная
   * в плане, а не недоделка): сервер отдавал форму, которой фронт не разбирает.
   * Закрывают двое — этот и критерии задачи 1 на бэкенде; ни один не заменяет
   * другого. Пара тестов ниже показывает обе стороны стыка.
   */
  function withZeroRootInTheRest(base: ProjectPassport, zeroShare: string): ProjectPassport {
    return {
      ...base,
      categories: [
        ...base.categories,
        {
          // Корень с суммой РОВНО ноль — состояние стенда (§1.2 плана: три
          // корневые статьи с нулевой суммой). Доля согласована с суммой:
          // 0 / 4 700 000 = 0. По сумме статья уходит за топ-8, то есть попадает
          // в «Остальные» — рядом с «Инженерными сетями».
          id: 90,
          code: "11",
          title: "Демонтажные работы",
          parent_id: null,
          is_bucket: false,
          sort_order: 110,
          total: "0.00",
          rows: 3,
          rows_priced: 3,
          rows_not_finite: 0,
          // Меняется РОВНО одно — форма нулевой доли (GC 22).
          share_pct: zeroShare,
          per_sqm: "0",
          own: "0.00",
          own_rows: 3,
          own_rows_priced: 3,
          own_rows_not_finite: 0,
          extras: [],
          own_sections: [],
        },
      ],
    };
  }

  it("нулевая доля в «Остальных» не гасит их процент", async () => {
    withPassport((base) => withZeroRootInTheRest(base, "0"));
    renderPassport();
    await screen.findByText("ГП-0212");

    const legend = screen.getByTestId("structure-ring-legend");
    // Нулевая статья действительно ВНУТРИ «Остальных» — иначе тест был бы зелен
    // ни о чём: ноль отличается от неизвестной суммы, которую правило 2 §2.10 в
    // «Остальные» не пускает (тест 6 ниже — про неизвестную).
    expect(within(legend).getByText("Остальные статьи (2)")).toBeInTheDocument();

    const share = await screen.findByTestId("legend-share-rest");
    // Доля «Инженерных сетей» (3,7234…) плюс ноль — процент есть и он верен.
    expect((share.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("3,72 %");
  });

  it("форму, которую фронт не разбирает, легенда не выдумывает — она молчит", async () => {
    /*
      ВТОРАЯ сторона стыка, и этот тест зелен и ДО правки — он фиксирует границу,
      а не защиту. Прежняя форма сервера ("0E+2") здесь подана во ВХОД: фронт её
      не разбирает и по сознательному правилу 5 §2.10 не показывает выдуманной
      суммы. Отсюда следует, что дефект §1.4 чинится на бэкенде (правка §2.1), а
      фронтовый тест выше без неё был бы бессилен.
    */
    withPassport((base) => withZeroRootInTheRest(base, "0E+2"));
    renderPassport();
    await screen.findByText("ГП-0212");

    const legend = screen.getByTestId("structure-ring-legend");
    expect(within(legend).getByText("Остальные статьи (2)")).toBeInTheDocument();
    // Строка есть, процента у неё нет — ровно то, что видно на стенде сегодня.
    expect(screen.queryByTestId("legend-share-rest")).not.toBeInTheDocument();
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
