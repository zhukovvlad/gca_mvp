import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";

import ProjectPassportPage from "./ProjectPassportPage";
import { RATE_COVERAGE_LABEL, RATE_NOTE_LABEL, RATE_STATE_LABEL } from "./rateLabels";
import { sampleProjectPassport } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { MoneyShareState, ProjectPassport, RateNote } from "@/types/domain";

/**
 * Три новые колонки таблицы статей — единица, объём, ставка ₽/ед. (спека
 * 2026-08-24, §2.1, §2.5, §2.9, §2.10; план, задача 6).
 *
 * Помощники не импортируются из `ProjectPassportPage.test.tsx` — тот файл их
 * не экспортирует (брифинг задачи 6), поэтому здесь их копия.
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

function withPassport(overrides: (base: ProjectPassport) => ProjectPassport) {
  server.use(
    http.get(PROJECT_PASSPORT_URL, () => HttpResponse.json(overrides(sampleProjectPassport)))
  );
}

/**
 * Все подписи пилюль состояния ставки, ОДНОЙ строкой из обеих карт — а не
 * переписанные в тесте списком (брифинг задачи 6: разошедшийся список молча
 * ослабил бы проверку `no_carrier`). Считает сама себя: сколько их —
 * решают карты, а не эта строка.
 */
const ALL_RATE_CAPTIONS = [
  ...Object.values(RATE_STATE_LABEL),
  ...Object.values(RATE_NOTE_LABEL),
].filter((caption): caption is string => caption !== null);

describe("Таблица статей: единица, объём, ставка ₽/ед.", () => {
  it("шапка несёт три новые подписи дословно", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    expect(screen.getByRole("columnheader", { name: "Ед." })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Объём" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ставка, ₽/ед." })).toBeInTheDocument();
  });

  it("узел rate показывает единицу, объём и ставку — с точным значением в подсказке", async () => {
    // Фикстура: «01.01» «Разработка грунта» — единственный узел с rate_state
    // "rate" (unit "м³", volume "3000.00", unit_rate — деление с длинным
    // хвостом, см. комментарий у фикстуры). Child узла «01» — нужно раскрыть
    // родителя, чтобы увидеть строку.
    renderPassport();
    await screen.findByText("ГП-0212");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть статью 01" }));

    const unit = await screen.findByTestId("unit-cat-01.01");
    expect((unit.textContent ?? "").trim()).toBe("м³");

    const volume = await screen.findByTestId("volume-cat-01.01");
    expect((volume.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("3 000,00");

    const rate = await screen.findByTestId("rate-cat-01.01");
    expect((rate.textContent ?? "").replace(/\s+/g, " ").trim()).toBe("166,67 ₽/ед.");

    const exact = within(rate).getByTitle(/Точное значение/);
    expect(exact.getAttribute("title")).toContain("166,66666666666666666667");
  });

  it("volume_missing: единица показана, объём — прочерк, пилюля «объём в смете не указан»", async () => {
    // Фикстура: «04.01» «Внутренние сети водоснабжения» — child узла «04»,
    // нужно раскрыть родителя, чтобы увидеть строку.
    renderPassport();
    await screen.findByText("ГП-0212");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));

    const unit = await screen.findByTestId("unit-cat-04.01");
    expect((unit.textContent ?? "").trim()).toBe("м");

    const volume = await screen.findByTestId("volume-cat-04.01");
    expect((volume.textContent ?? "").trim()).toBe("—");

    const rate = await screen.findByTestId("rate-cat-04.01");
    expect(within(rate).getByText("объём в смете не указан")).toBeInTheDocument();
  });

  it("no_carrier: ни одной пилюли и ни одного текста состояния в строке узла", async () => {
    // Фикстура: «05» «Фасадные работы» — корень с rate_state "no_carrier",
    // виден без разворота.
    renderPassport();
    await screen.findByText("ГП-0212");

    const row = screen.getByTestId("row-cat-05");
    for (const caption of ALL_RATE_CAPTIONS) {
      expect(within(row).queryByText(caption)).not.toBeInTheDocument();
    }
  });

  it("unit_not_scalable → «не нормируется»", async () => {
    // Фикстура: «04» «Инженерные сети» — своя строка есть, единица «компл».
    renderPassport();
    await screen.findByText("ГП-0212");

    const rate = await screen.findByTestId("rate-cat-04");
    expect(within(rate).getByText("не нормируется")).toBeInTheDocument();
  });

  it("additional_works → «дополнительные работы»", async () => {
    // Фикстура: «04.02» «Пусконаладочные работы» — узел с extras, child «04».
    renderPassport();
    await screen.findByText("ГП-0212");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));

    const rate = await screen.findByTestId("rate-cat-04.02");
    expect(within(rate).getByText("дополнительные работы")).toBeInTheDocument();
  });

  it.each([
    { note: "mixed_units" as RateNote, caption: "объём смешан" },
    { note: "overshoot" as RateNote, caption: "объём не сходится" },
    { note: "unverifiable" as RateNote, caption: "сходимость не проверить" },
  ])("volume_inconsistent + rate_note = $note → «$caption»", async ({ note, caption }) => {
    withPassport((base) => ({
      ...base,
      categories: base.categories.map((c) =>
        c.code === "01.01" ? { ...c, rate_state: "volume_inconsistent", rate_note: note } : c
      ),
    }));
    renderPassport();
    await screen.findByText("ГП-0212");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть статью 01" }));

    const rate = await screen.findByTestId("rate-cat-01.01");
    expect(within(rate).getByText(caption)).toBeInTheDocument();
  });

  it("строка допработ, служебная строка, «Нераспределённое» и «Итого по договору» несут по три ячейки-прочерка", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть статью 04" }));
    await user.click(await screen.findByRole("button", { name: "Развернуть статью 04.02" }));

    const extraRow = await screen.findByTestId("row-extra-04.02-401");
    const ownRow = await screen.findByTestId("row-own-04");
    const unallocatedRow = screen.getByTestId("row-unallocated");
    const grandTotalRow = screen.getByTestId("row-grand-total");

    for (const row of [extraRow, ownRow, unallocatedRow, grandTotalRow]) {
      expect(row.querySelectorAll("td")).toHaveLength(8);
    }
  });

  it("строка панели разноса перекрывает всю ширину восьмиколоночной таблицы", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть нераспределённое" }));

    const panelRow = await screen.findByTestId("row-unallocated-panel");
    const cell = panelRow.querySelector("td");
    expect(cell).not.toBeNull();
    expect((cell as HTMLTableCellElement).colSpan).toBe(8);
  });
});

/**
 * Строка охвата под таблицей статей (спека 2026-08-24, §2.8; план, задача 7).
 *
 * `RATE_COVERAGE_LABEL` (`./rateLabels`) импортируется, а не переписывается
 * здесь списком (та же причина, что у `ALL_RATE_CAPTIONS` выше): проверки
 * DoD 32-34 читают саму карту, а не её копию.
 */
describe("Строка охвата под таблицей статей (§2.8)", () => {
  it("непустой охват несёт K, N и M дословно (фикстура стенда: complete, N=1, M=11)", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const coverage = await screen.findByTestId("rate-coverage");
    expect(coverage.textContent).toBe(
      "Покрыто 10,64 % цены договора — ставка есть у 1 статьи из 11 в неперекрывающемся наборе."
    );
  });

  describe("RATE_COVERAGE_LABEL: пять состояний — пять подписей (DoD 32, 33, 34)", () => {
    const states = Object.keys(RATE_COVERAGE_LABEL) as MoneyShareState[];

    it.each(states)("%s несёт собственный непустой текст", (state) => {
      expect(RATE_COVERAGE_LABEL[state].length).toBeGreaterThan(0);
    });

    it("подписи всех пяти состояний различны дословно", () => {
      const captions = states.map((state) => RATE_COVERAGE_LABEL[state]);
      expect(new Set(captions).size).toBe(states.length);
    });

    it("partial несёт оговорку «по известным суммам» (DoD 33)", () => {
      expect(RATE_COVERAGE_LABEL.partial).toContain("По известным суммам");
    });

    it("out_of_range несёт «суммы сметы требуют проверки» и НЕ несёт «итог паспорта неизвестен» (DoD 32)", () => {
      expect(RATE_COVERAGE_LABEL.out_of_range).toContain("суммы сметы требуют проверки");
      expect(RATE_COVERAGE_LABEL.out_of_range).not.toContain("итог паспорта неизвестен");
    });

    it("no_articles не называет ни N, ни M", () => {
      expect(RATE_COVERAGE_LABEL.no_articles).not.toMatch(/\{N\}|\{M\}/);
    });
  });

  describe("DoD 25 — два случая по отдельности (склейка была дефектом прежней редакции)", () => {
    it("набор непуст, ставок нет, суммы полные (complete) → печатает «покрыто 0,00 %» и называет M = 1", async () => {
      withPassport((base) => ({
        ...base,
        rate_coverage: {
          articles_with_rate: 0,
          articles_total: 1,
          money_share: "0",
          money_share_state: "complete",
        },
      }));
      renderPassport();
      await screen.findByText("ГП-0212");

      const coverage = await screen.findByTestId("rate-coverage");
      expect(coverage.textContent).toBe(
        "Покрыто 0,00 % цены договора — ставка есть у 0 статей из 1 в неперекрывающемся наборе."
      );
    });

    it("набор пуст (no_articles) → подпись про отсутствие названных статей, ни N, ни M не названы", async () => {
      withPassport((base) => ({
        ...base,
        rate_coverage: {
          articles_with_rate: 0,
          articles_total: 0,
          money_share: null,
          money_share_state: "no_articles",
        },
      }));
      renderPassport();
      await screen.findByText("ГП-0212");

      const coverage = await screen.findByTestId("rate-coverage");
      expect(coverage.textContent).toBe("В смете нет названных статей классификатора — охват не определён.");
      expect(coverage.textContent).not.toMatch(/\d/);
    });
  });

  it.each([
    {
      money_share_state: "total_unavailable" as MoneyShareState,
      articles_with_rate: 3,
      articles_total: 7,
      expected:
        "Ставка есть у 3 статей из 7 в неперекрывающемся наборе; доля цены договора не определена — итог паспорта неизвестен.",
    },
    {
      money_share_state: "out_of_range" as MoneyShareState,
      articles_with_rate: 2,
      articles_total: 5,
      expected:
        "Ставка есть у 2 статей из 5 в неперекрывающемся наборе; доля цены договора не определена — суммы сметы требуют проверки.",
    },
    {
      money_share_state: "no_articles" as MoneyShareState,
      articles_with_rate: 0,
      articles_total: 0,
      expected: "В смете нет названных статей классификатора — охват не определён.",
    },
  ])(
    "$money_share_state с money_share: null не печатает процента — проверка по состоянию, не по null",
    async ({ money_share_state, articles_with_rate, articles_total, expected }) => {
      withPassport((base) => ({
        ...base,
        rate_coverage: {
          articles_with_rate,
          articles_total,
          money_share: null,
          money_share_state,
        },
      }));
      renderPassport();
      await screen.findByText("ГП-0212");

      const coverage = await screen.findByTestId("rate-coverage");
      expect(coverage.textContent).toBe(expected);
      expect(within(coverage).queryByText(/%/)).not.toBeInTheDocument();
    }
  );

  it.each([
    { n: 1, word: "статьи" },
    { n: 2, word: "статей" },
  ])("согласование числа: articles_with_rate $n → «у $n $word»", async ({ n, word }) => {
    withPassport((base) => ({
      ...base,
      rate_coverage: {
        articles_with_rate: n,
        articles_total: 9,
        money_share: "40",
        money_share_state: "complete",
      },
    }));
    renderPassport();
    await screen.findByText("ГП-0212");

    const coverage = await screen.findByTestId("rate-coverage");
    expect(coverage.textContent).toContain(`у ${n} ${word} из 9`);
  });

  it("строка не несёт data-print=\"hide\" — печатное обещание §2.8", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");

    const coverage = await screen.findByTestId("rate-coverage");
    expect(coverage.getAttribute("data-print")).not.toBe("hide");
  });

  it("число раскрытых ставок на экране не попадает в строку: разворот узла «01» не меняет её текст ни на символ", async () => {
    renderPassport();
    await screen.findByText("ГП-0212");
    const coverage = await screen.findByTestId("rate-coverage");
    const before = coverage.textContent;

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Развернуть статью 01" }));
    await screen.findByTestId("unit-cat-01.01");

    expect(coverage.textContent).toBe(before);
  });
});
