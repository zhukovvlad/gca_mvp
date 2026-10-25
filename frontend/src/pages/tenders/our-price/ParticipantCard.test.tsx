import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ParticipantCard } from "./ParticipantCard";
import { ourPriceRoundFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { OurPriceParticipant } from "@/types/domain";

import { CHEAPEST_PER_SQM_LABEL, PER_SQM_REASON_LABEL, WAITING_ADMIN_LABEL } from "./ourPriceCopy";
import type { VatView } from "./money";

const [OK, NO_ESTIMATE, UNKNOWN_BASE] = ourPriceRoundFixture().participants;

function renderCard(
  participant: OurPriceParticipant,
  over: { view?: VatView; cheapest?: boolean; href?: string | null } = {}
) {
  return renderWithProviders(
    <ParticipantCard
      participant={participant}
      view={over.view ?? "net"}
      cheapest={over.cheapest ?? false}
      href={over.href === undefined ? "/o/7001" : over.href}
    />
  );
}

describe("ParticipantCard", () => {
  it("участник с КП в нетто: ₽/м², медиана класса и пять сумм карточки", () => {
    renderCard(OK);
    const card = screen.getByTestId("card-7001");
    expect(card).toHaveTextContent("Подрядчик А");
    expect(card).toHaveTextContent("110 000,00");
    expect(card).toHaveTextContent("+10,0 % к медиане наших");
    expect(card).toHaveTextContent(/Можно выторговать\s*120 000,00/);
    expect(card).toHaveTextContent(/Ниже всего, что платили\s*30 000,00\s*₽\s*·\s*2 стр\./);
    expect(card).toHaveTextContent(/Разные единицы учёта\s*1 стр\./);
    expect(card).toHaveTextContent(/Не с чем сравнить\s*8 000,00/);
    expect(card).toHaveTextContent("без НДС");
  });

  it("в режиме shown суммы берутся из поля shown, подпись — ставка участника", () => {
    renderCard(OK, { view: "shown" });
    const card = screen.getByTestId("card-7001");
    expect(card).toHaveTextContent("134 200,00");
    expect(card).not.toHaveTextContent("110 000,00");
    expect(card).toHaveTextContent(/Можно выторговать\s*146 400,00/);
    expect(card).toHaveTextContent("с НДС 22 %");
  });

  it("знак медианы: минус остаётся, ноль без плюса", () => {
    const { unmount } = renderCard({ ...OK, class_median_pct: "-5.5" });
    expect(screen.getByTestId("card-7001")).toHaveTextContent("-5,5 % к медиане наших");
    unmount();
    renderCard({ ...OK, class_median_pct: "0.0" });
    expect(screen.getByTestId("card-7001")).toHaveTextContent("0,0 % к медиане наших");
    expect(screen.getByTestId("card-7001")).not.toHaveTextContent("+0,0");
  });

  it.each([
    [0, null],
    [1, "у нас 1 договор с ним"],
    [2, "у нас 2 договора с ним"],
    [5, "у нас 5 договоров с ним"],
    [11, "у нас 11 договоров с ним"],
    [21, "у нас 21 договор с ним"],
  ])("«у нас N договоров с ним»: N = %i", (n, text) => {
    renderCard({ ...OK, ours_with_contractor: n });
    const card = screen.getByTestId("card-7001");
    if (text === null) expect(card).not.toHaveTextContent(/у нас \d/);
    else expect(card).toHaveTextContent(text);
  });

  it("«дешевле всех на м²» — только когда cheapest", () => {
    const { unmount } = renderCard(OK, { cheapest: true });
    expect(screen.getByTestId("card-7001")).toHaveTextContent(CHEAPEST_PER_SQM_LABEL);
    unmount();
    renderCard(OK, { cheapest: false });
    expect(screen.getByTestId("card-7001")).not.toHaveTextContent(CHEAPEST_PER_SQM_LABEL);
  });

  it("кнопка «Разобрать по строкам →» ведёт по адресу; без адреса её нет", () => {
    const { unmount } = renderCard(OK, { href: "/o/7001?vat=shown" });
    expect(within(screen.getByTestId("card-7001")).getByRole("link")).toHaveAttribute(
      "href",
      "/o/7001?vat=shown"
    );
    unmount();
    renderCard(OK, { href: null });
    expect(within(screen.getByTestId("card-7001")).queryByRole("link")).not.toBeInTheDocument();
  });

  it("no_estimate: «КП не загружено», чисел и кнопки нет", () => {
    renderCard(NO_ESTIMATE, { href: null });
    const card = screen.getByTestId("card-7002");
    expect(card).toHaveTextContent("КП не загружено");
    expect(card).not.toHaveTextContent("Можно выторговать");
    expect(card).not.toHaveTextContent("₽/м²");
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
  });

  it("unknown_vat_base: причина полем и подписью, суммы не рисуются, кнопка есть", () => {
    renderCard(UNKNOWN_BASE, { view: "shown", href: "/o/7003" });
    const card = screen.getByTestId("card-7003");
    expect(card).toHaveAttribute("data-reason", "unknown_vat_base");
    expect(card).toHaveTextContent(PER_SQM_REASON_LABEL.unknown_vat_base);
    expect(card).not.toHaveTextContent("Можно выторговать");
    expect(within(card).getByRole("link")).toBeInTheDocument();
  });

  it("per_sqm_reason = no_area у участника с КП: подпись причины вместо ₽/м²", () => {
    renderCard({ ...OK, per_sqm: null, per_sqm_reason: "no_area", class_median_pct: null });
    const card = screen.getByTestId("card-7001");
    expect(card).toHaveAttribute("data-reason", "no_area");
    expect(card).toHaveTextContent(PER_SQM_REASON_LABEL.no_area);
    expect(card).not.toHaveTextContent("110 000,00");
  });

  it("слова про администратора карточка не говорит: это подпись строк, а не участника", () => {
    renderCard(OK);
    expect(screen.getByTestId("card-7001")).not.toHaveTextContent(WAITING_ADMIN_LABEL);
  });
});
