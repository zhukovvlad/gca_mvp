import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
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
const EMPTY_TEXT = /Данных нет: паспорт по статьям классификатора появится/;

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
});
