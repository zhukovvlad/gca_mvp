import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";

import ProjectPassportPage from "./ProjectPassportPage";
import { RATE_NOTE_LABEL, RATE_STATE_LABEL } from "./rateLabels";
import { sampleProjectPassport } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { ProjectPassport, RateNote } from "@/types/domain";

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
