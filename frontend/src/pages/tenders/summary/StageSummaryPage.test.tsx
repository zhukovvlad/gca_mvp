import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import StageSummaryPage from "./StageSummaryPage";
import { handlerState } from "@/test/handlers";
import { stageSummaryAllUnknown, stageSummaryNet } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";

/**
 * Страница свода по этапам (спека 2026-08-27-stage-summary-design.md §2.2,
 * §2.11, §2.14; задача 8 плана). Числа и подписи — из фикстур
 * `frontend/src/test/fixtures.ts` (`sampleStageSummary` и её варианты),
 * которые прошли инвариантный набор `fixtures.test.ts`: где брифовый пример
 * расходится с фикстурой, здесь взята фикстура.
 */
function renderSummary(route = "/tenders/300/summary?offers=7002&offers=7001&offers=7004") {
  return renderWithProviders(
    <Routes>
      <Route path="/tenders/:tenderId/summary" element={<StageSummaryPage />} />
    </Routes>,
    { initialRoute: route }
  );
}

describe("Свод по этапам — страница (спека §2.2, §2.11, §2.14)", () => {
  it("шапка: участник, «3 из 4», исключённый этап из participant.stages, подпись валовой оси и номинального уровня", async () => {
    renderSummary();
    expect(await screen.findByRole("heading", { name: /Свод по этапам · ООО Альфа/ })).toBeInTheDocument();
    expect(screen.getByText(/этапов в своде 3 из 4/)).toBeInTheDocument();
    expect(screen.getByText(/исключён выбором: этап 3/)).toBeInTheDocument();
    expect(screen.getByText(/Все суммы — с НДС 20 %/)).toBeInTheDocument();
    expect(screen.getByText(/номинальные/i)).toBeInTheDocument();
  });

  it("KPI целиком — пять карточек по полям kpi, не по вычислению клиента", async () => {
    renderSummary();
    await screen.findByRole("table");
    const kpi = screen.getByTestId("kpi");
    expect(within(kpi).getByText("Этапов в своде").parentElement).toHaveTextContent("3 из 4");
    expect(within(kpi).getByText("Позиций в последнем").parentElement).toHaveTextContent("1");
    expect(within(kpi).getByText("Статей с суммой").parentElement).toHaveTextContent("1 из 2");
    expect(within(kpi).getByText("Ставка НДС").parentElement).toHaveTextContent("20 %");
    expect(within(kpi).getByText("Последний к первому").parentElement).toHaveTextContent("-50,0%");
  });

  /**
   * Управляется `stageSummaryNet()` (`frontend/src/test/fixtures.ts`): та же
   * фикстура, что отдаёт MSW-обработчик на `stageSummaryOutcome = "net"`
   * (`frontend/src/test/handlers.ts`). Ожидаемый список ставок читается из
   * `stageSummaryNet().display.rates_by_column` — не переписан литералом:
   * если фикстура изменит порядок или число ставок, тест последует за ней, а
   * не разойдётся молча.
   */
  it("KPI «Ставка НДС» на нетто-оси называет причину и перечисляет ставки (stageSummaryNet)", async () => {
    handlerState.stageSummaryOutcome = "net";
    renderSummary();
    await screen.findByRole("table");
    const kpi = screen.getByTestId("kpi");
    const rates = stageSummaryNet().display.rates_by_column;
    expect(rates).not.toBeNull();
    const ratesText = `(${rates!.map((rate) => rate ?? "—").join(", ")})`;
    const value = within(kpi).getByText("Ставка НДС").parentElement as HTMLElement;
    // Причина — та же фраза, что несёт налоговая ось (`TAX_LABEL.net`): КПИ и
    // ось не заводят две разные формулировки одного факта.
    expect(value).toHaveTextContent(/ставки этапов расходятся/);
    expect(value).toHaveTextContent(ratesText);
  });

  /**
   * Управляется `stageSummaryAllUnknown()` — та же фикстура, что отдаёт
   * MSW-обработчик на `stageSummaryOutcome = "track_no_comparable"`
   * (`frontend/src/test/handlers.ts`): у неё `display.tax_basis = "none"`,
   * все колонки `vat_state = "unknown_vat_base"`.
   */
  it("KPI «Ставка НДС» без единой известной базы называет причину, а не прочерк без объяснения (stageSummaryAllUnknown)", async () => {
    // Предпосылка: причина этого случая — именно «нет ни одной известной ставки»
    // (§2.8 спеки), а не что-то другое, что могло бы дать те же симптомы.
    expect(stageSummaryAllUnknown().display.tax_basis).toBe("none");
    handlerState.stageSummaryOutcome = "track_no_comparable";
    renderSummary();
    await screen.findByRole("table");
    const kpi = screen.getByTestId("kpi");
    const value = within(kpi).getByText("Ставка НДС").parentElement as HTMLElement;
    // Та же фраза, что несёт хвост `TAX_LABEL.none`.
    expect(value).toHaveTextContent("база НДС неизвестна у всех выбранных этапов");
  });

  it("нетто-ось: подпись с перечислением ставок и пометка о сходимости в исходных деньгах", async () => {
    handlerState.stageSummaryOutcome = "net";
    renderSummary();
    expect(
      await screen.findByText(/Все суммы — без НДС: ставки этапов расходятся \(20, 0, 20\)/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Δ сходимости измерена в исходных деньгах файла/)).toBeInTheDocument();
  });

  it("track.reason=no_comparable_totals → своя причина, ось «нет сопоставимых сумм»", async () => {
    handlerState.stageSummaryOutcome = "track_no_comparable";
    renderSummary();
    expect(await screen.findByText(/ни у одного этапа нет сопоставимого итога/)).toBeInTheDocument();
    expect(screen.getByText(/Сопоставимых сумм нет/)).toBeInTheDocument();
  });

  /**
   * Прогон через полную страницу (сеть/провайдеры/маршрут) — проверяет
   * ПРОВОДКУ, что реальный ответ действительно доходит до столбика как
   * `bar_height_pct`. Эта проверка САМА ПО СЕБЕ НЕ различает «взято из поля»
   * от «посчитано делением total/max клиентом»: в фикстуре `bar_height_pct`
   * пропорционален `total` (инвариант `fixtures.test.ts`), и оба способа
   * дали бы тут одно и то же число. Различающий тест — на заведомо
   * невозможном для сервера входе — в `StageSummaryTrack.test.tsx` (fix
   * round 2, п.1).
   */
  it("трасса: высота столбика — из bar_height_pct (проводка страницы; различение источника — в StageSummaryTrack.test.tsx)", async () => {
    renderSummary();
    const bars = await screen.findAllByTestId("track-bar");
    expect(bars.map((b) => b.style.height)).toEqual(["100%", "66.7%", "50%"]);
  });

  it("track.available=false → объяснение вместо столбиков, таблица остаётся", async () => {
    handlerState.stageSummaryOutcome = "track_non_positive";
    renderSummary();
    expect(await screen.findByText(/столбики не строятся/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("track-bar")).toHaveLength(0);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("колонка unknown_vat_base: слот без числа, ячейки без сумм с причиной, ось остаётся валовой", async () => {
    handlerState.stageSummaryOutcome = "unknown_vat";
    renderSummary();
    await screen.findByRole("table");
    expect(screen.getByTestId("track-slot-unknown")).toBeInTheDocument();
    expect(screen.getByText(/Все суммы — с НДС 20 %/)).toBeInTheDocument();
    const six = screen.getByText("Фасадные работы").closest("tr") as HTMLElement;
    // видимая подпись, не только title: читатель обязан видеть причину без наведения
    expect(within(six).getAllByRole("cell")[2]).toHaveTextContent("нет базы НДС");
    expect(within(six).getAllByRole("cell")[2]).not.toHaveTextContent(/\d/);
    expect(screen.getByTestId("track-slot-unknown")).toHaveTextContent("нет базы НДС");
  });

  it.each([
    ["tender_not_found", /Тендер не найден/],
    ["offer_not_found", /Предложение не найдено/],
    ["too_few_offers", /хотя бы два/],
    ["one_offer_per_round", /одно предложение/],
    ["single_participant", /по одному участнику/],
    ["offer_has_no_estimate", /нет сметы/],
  ])("отказ %s → пустое состояние с причиной и ссылкой на решётку (все шесть кодов)", async (outcome, text) => {
    handlerState.stageSummaryOutcome = outcome as typeof handlerState.stageSummaryOutcome;
    renderSummary();
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /К решётке тендера/ })).toHaveAttribute("href", "/tenders/300");
  });

  it("без ?offers — пустое состояние без запроса", async () => {
    renderSummary("/tenders/300/summary");
    expect(await screen.findByText(/Выберите предложения на решётке/)).toBeInTheDocument();
  });
});
