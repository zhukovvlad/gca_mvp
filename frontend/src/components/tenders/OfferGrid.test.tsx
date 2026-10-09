import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { OfferGrid } from "./OfferGrid";
import { sampleTenderAward, sampleTenderCard } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { TenderAward, TenderCard } from "@/types/domain";

/**
 * Решётка и отметка победителя (спека Б2 §2.8, макет экраны 1–2): меню «⋯»
 * только у КП финального этапа и только пока действующей отметки нет; строка
 * победителя подсвечена, в его финальной ячейке — значок «победитель».
 */
const [round1, round2] = sampleTenderCard.rounds;

/** Оба участника подали КП и в финале (этап 2), и в этапе 1 — у Беты этап 1 пуст. */
const bothFinal: TenderCard = {
  ...sampleTenderCard,
  rounds: [round1, { ...round2, unallocated_pending_sections: 0 }],
  cells: [
    { round_id: 3001, package_id: 501, offer_id: 7001, estimate_id: 8001, total_including_vat: "1200.00" },
    { round_id: 3001, package_id: 502, offer_id: null, estimate_id: null, total_including_vat: null },
    { round_id: 3002, package_id: 501, offer_id: 7003, estimate_id: 8003, total_including_vat: "1100.00" },
    { round_id: 3002, package_id: 502, offer_id: 7002, estimate_id: 8002, total_including_vat: "1300.00" },
  ],
};

function withAward(card: TenderCard, award: TenderAward | null): TenderCard {
  return { ...card, award };
}

function renderGrid(card: TenderCard, options?: Parameters<typeof renderWithProviders>[1]) {
  return renderWithProviders(
    <OfferGrid
      card={card}
      selectedRoundId={undefined}
      onSelectRound={() => {}}
      selectedOfferIds={new Set()}
      onToggleOffer={() => {}}
      onSelectParticipant={() => {}}
      onOpenUnallocated={() => {}}
    />,
    options
  );
}

const MEMBER = { id: 2, email: "member@example.com", role: "member" } as const;
const triggers = () => screen.queryAllByRole("button", { name: /Действия с КП/ });

describe("OfferGrid: меню отметки победителя", () => {
  it("меню у КП финального этапа обоих участников, у ячеек этапа 1 меню нет", () => {
    renderGrid(bothFinal);

    expect(triggers().map((t) => t.getAttribute("aria-label"))).toEqual([
      "Действия с КП участника «ООО Альфа»",
      "Действия с КП участника «ООО Бета»",
    ]);
    // Этап 1 у Альфы с КП есть — но он не финальный: меню не в его ячейке.
    const alphaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const cells = within(alphaRow).getAllByRole("cell");
    expect(within(cells[1]).queryByRole("button", { name: /Действия с КП/ })).toBeNull();
    expect(within(cells[2]).getByRole("button", { name: /Действия с КП/ })).toBeInTheDocument();
  });

  it("финальный этап — по наибольшему stage_no, а не по порядку в списке", () => {
    renderGrid({ ...bothFinal, rounds: [bothFinal.rounds[1], bothFinal.rounds[0]] });

    expect(triggers()).toHaveLength(2);
    const alphaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const stage2Cell = within(alphaRow).getByText(/1\s100,00/).closest("td") as HTMLElement;
    expect(within(stage2Cell).getByRole("button", { name: /Действия с КП/ })).toBeInTheDocument();
  });

  it("пустая ячейка финала («—») и «нет сметы» меню не получают", () => {
    // Исходная фикстура: у Альфы финал пуст, у Беты предложение есть, а сметы нет.
    renderGrid(sampleTenderCard);

    expect(triggers()).toHaveLength(0);
  });

  it("при действующей отметке меню нет нигде", () => {
    renderGrid(withAward(bothFinal, sampleTenderAward));

    expect(triggers()).toHaveLength(0);
  });

  it("member меню не видит", () => {
    renderGrid(bothFinal, { initialUser: MEMBER });

    expect(triggers()).toHaveLength(0);
  });

  it("пункт «Отметить победителем» подписан КП и этапом и шлёт offer_id этой ячейки", async () => {
    const user = userEvent.setup();
    renderGrid(bothFinal);

    await user.click(screen.getByRole("button", { name: "Действия с КП участника «ООО Бета»" }));

    expect(await screen.findByText("КП ООО Бета · этап 2")).toBeInTheDocument();
    await user.click(await screen.findByRole("menuitem", { name: "Отметить победителем" }));

    await screen.findByText("Победитель отмечен");
    expect(handlerState.lastAwardCommand).toEqual({
      kind: "award",
      tenderId: 300,
      awardId: null,
      body: { offer_id: 7002 },
    });
  });

  // Ревью задачи 9: пока команда идёт, повторно открытое меню не даёт отметить ещё раз.
  it("пока отметка уходит на сервер, пункт «Отметить победителем» недоступен", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/tenders/:id/awards", async () => {
        await gate;
        return HttpResponse.json(bothFinal, { status: 201 });
      })
    );
    const user = userEvent.setup();
    renderGrid(bothFinal);
    try {
      const trigger = screen.getByRole("button", { name: "Действия с КП участника «ООО Бета»" });
      await user.click(trigger);
      await user.click(await screen.findByRole("menuitem", { name: "Отметить победителем" }));
      await waitFor(() => expect(screen.queryByRole("menuitem")).toBeNull());

      await user.click(trigger);
      expect(await screen.findByRole("menuitem", { name: "Отметить победителем" })).toHaveAttribute(
        "aria-disabled",
        "true"
      );
    } finally {
      release();
    }
  });

  it("отказ сервера показан его текстом", async () => {
    server.use(
      http.post("/api/v1/tenders/:id/awards", () =>
        HttpResponse.json(
          { detail: "В тендере уже отмечен победитель — ТОО Монолит. Чтобы отметить другого, снимите отметку или отметьте, что договор не заключён." },
          { status: 409 }
        )
      )
    );
    const user = userEvent.setup();
    renderGrid(bothFinal);

    await user.click(screen.getByRole("button", { name: "Действия с КП участника «ООО Альфа»" }));
    await user.click(await screen.findByRole("menuitem", { name: "Отметить победителем" }));

    expect(await screen.findByText(/В тендере уже отмечен победитель — ТОО Монолит/)).toBeInTheDocument();
  });
});

describe("OfferGrid: строка победителя", () => {
  // Отметка стоит на КП Беты (пакет 502, этап 2).
  it("строка победителя подсвечена, значок «победитель» — только в его финальной ячейке", () => {
    renderGrid(withAward(bothFinal, sampleTenderAward));

    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const alphaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    expect(betaRow).toHaveAttribute("data-winner", "true");
    expect(alphaRow).not.toHaveAttribute("data-winner");

    const betaCells = within(betaRow).getAllByRole("cell");
    expect(within(betaCells[2]).getByText("победитель")).toBeInTheDocument();
    expect(within(betaRow).getAllByText("победитель")).toHaveLength(1);
    expect(within(alphaRow).queryByText("победитель")).toBeNull();
  });

  // Ревью задачи 9: у Беты в фикстуре КП есть только в финале, поэтому значок
  // без сверки этапа и подсветка без класса проходили тест выше. Здесь
  // победитель — Альфа, у которой КП есть и в этапе 1.
  it("значок — только в ячейке этапа отметки, хотя КП победителя есть и в этапе 1; подсветка — классом строки", () => {
    const alphaAward: TenderAward = {
      ...sampleTenderAward,
      offer_id: 7003,
      package_id: 501,
      contractor_title: "ООО Альфа",
      round_id: 3002,
      estimate_id: 8003,
    };
    renderGrid(withAward(bothFinal, alphaAward));

    const alphaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const alphaCells = within(alphaRow).getAllByRole("cell");
    expect(within(alphaCells[1]).queryByText("победитель")).toBeNull();
    expect(within(alphaCells[2]).getByText("победитель")).toBeInTheDocument();
    expect(alphaRow).toHaveClass("bg-accent-soft");
    expect(betaRow).not.toHaveClass("bg-accent-soft");
  });

  it("без отметки подсветки и значка нет", () => {
    renderGrid(bothFinal);

    expect(screen.queryByText("победитель")).toBeNull();
    expect(document.querySelector("[data-winner]")).toBeNull();
  });
});
