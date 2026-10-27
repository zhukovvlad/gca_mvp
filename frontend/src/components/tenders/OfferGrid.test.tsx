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
 * Решётка и отметка победителя (спека Б2 §2.8, макет экраны 1–2; спека 4а
 * §2.13): меню «⋯» у каждой ячейки с КП и сметой, в нём «Против нашей цены»;
 * пункт «Отметить победителем» — только `admin`, только в финальном этапе и
 * только пока действующей отметки нет; строка победителя подсвечена, в его
 * финальной ячейке — значок «победитель».
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
const AWARD_ITEM = "Отметить победителем";
const OUR_PRICE_ITEM = "Против нашей цены";

/** Пункты меню «⋯»: открыть, прочитать подписи, закрыть. */
async function itemsOf(user: ReturnType<typeof userEvent.setup>, trigger: HTMLElement): Promise<string[]> {
  await user.click(trigger);
  const names = (await screen.findAllByRole("menuitem")).map((item) => item.textContent ?? "");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("menuitem")).toBeNull());
  return names;
}

describe("OfferGrid: меню отметки победителя", () => {
  it("пункт «Отметить победителем» есть только в ячейке финального этапа, в этапе 1 того же участника его нет", async () => {
    const user = userEvent.setup();
    renderGrid(bothFinal);

    const alphaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const cells = within(alphaRow).getAllByRole("cell");
    const stage1 = within(cells[1]).getByRole("button", { name: /Действия с КП/ });
    const stage2 = within(cells[2]).getByRole("button", { name: /Действия с КП/ });
    expect(await itemsOf(user, stage1)).toEqual([OUR_PRICE_ITEM]);
    expect(await itemsOf(user, stage2)).toEqual([OUR_PRICE_ITEM, AWARD_ITEM]);
  });

  it("финальный этап — по наибольшему stage_no, а не по порядку в списке", async () => {
    const user = userEvent.setup();
    renderGrid({ ...bothFinal, rounds: [bothFinal.rounds[1], bothFinal.rounds[0]] });

    const alphaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const stage2Cell = within(alphaRow).getByText(/1\s100,00/).closest("td") as HTMLElement;
    const stage1Cell = within(alphaRow).getByText(/1\s200,00/).closest("td") as HTMLElement;
    expect(await itemsOf(user, within(stage2Cell).getByRole("button", { name: /Действия с КП/ }))).toContain(AWARD_ITEM);
    expect(await itemsOf(user, within(stage1Cell).getByRole("button", { name: /Действия с КП/ }))).not.toContain(AWARD_ITEM);
  });

  it("пустая ячейка («—») и «нет сметы» меню не получают, ячейка с КП — получает", () => {
    // Исходная фикстура: у Альфы этап 1 с КП, финал пуст; у Беты этап 1 пуст,
    // в финале предложение есть, а сметы нет.
    renderGrid(sampleTenderCard);

    expect(triggers()).toHaveLength(1);
    const alphaCells = within(screen.getByText("ООО Альфа").closest("tr") as HTMLElement).getAllByRole("cell");
    const betaCells = within(screen.getByText("ООО Бета").closest("tr") as HTMLElement).getAllByRole("cell");
    expect(within(alphaCells[1]).getByRole("button", { name: /Действия с КП/ })).toBeInTheDocument();
    expect(within(alphaCells[2]).queryByRole("button", { name: /Действия с КП/ })).toBeNull();
    expect(within(betaCells[1]).queryByRole("button", { name: /Действия с КП/ })).toBeNull();
    expect(within(betaCells[2]).queryByRole("button", { name: /Действия с КП/ })).toBeNull();
  });

  it("при действующей отметке пункта «Отметить победителем» нет ни в одном меню", async () => {
    const user = userEvent.setup();
    renderGrid(withAward(bothFinal, sampleTenderAward));

    const all = triggers();
    expect(all).toHaveLength(3);
    for (const trigger of all) {
      expect(await itemsOf(user, trigger)).toEqual([OUR_PRICE_ITEM]);
    }
  });

  it("member пункта «Отметить победителем» не видит", async () => {
    const user = userEvent.setup();
    renderGrid(bothFinal, { initialUser: MEMBER });

    const all = triggers();
    expect(all).toHaveLength(3);
    for (const trigger of all) {
      expect(await itemsOf(user, trigger)).toEqual([OUR_PRICE_ITEM]);
    }
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

    // У Альфы КП в двух этапах: меню финала — последнее из её меню.
    await user.click(screen.getAllByRole("button", { name: "Действия с КП участника «ООО Альфа»" }).at(-1) as HTMLElement);
    await user.click(await screen.findByRole("menuitem", { name: "Отметить победителем" }));

    expect(await screen.findByText(/В тендере уже отмечен победитель — ТОО Монолит/)).toBeInTheDocument();
  });
});

describe("OfferGrid: вход «Против нашей цены» в меню ячейки", () => {
  it("меню есть у каждой ячейки с КП и сметой — во всех этапах и у всех ролей", () => {
    renderGrid(bothFinal);
    expect(triggers().map((t) => t.getAttribute("aria-label"))).toEqual([
      "Действия с КП участника «ООО Альфа»",
      "Действия с КП участника «ООО Альфа»",
      "Действия с КП участника «ООО Бета»",
    ]);
  });

  it("у member и при действующей отметке пункт — та же ссылка на разбор", async () => {
    const user = userEvent.setup();
    renderGrid(withAward(bothFinal, sampleTenderAward), { initialUser: MEMBER });

    const betaCells = within(screen.getByText("ООО Бета").closest("tr") as HTMLElement).getAllByRole("cell");
    await user.click(within(betaCells[2]).getByRole("button", { name: /Действия с КП/ }));
    expect(await screen.findByRole("menuitem", { name: OUR_PRICE_ITEM })).toHaveAttribute(
      "href",
      "/tenders/300/rounds/3002/our-price/offers/7002"
    );
  });

  it("пункт ведёт в разбор оффера ЭТОЙ ячейки этого этапа, без параметров экрана", async () => {
    const user = userEvent.setup();
    renderGrid(bothFinal);

    const alphaCells = within(screen.getByText("ООО Альфа").closest("tr") as HTMLElement).getAllByRole("cell");
    await user.click(within(alphaCells[1]).getByRole("button", { name: /Действия с КП/ }));
    expect(await screen.findByRole("menuitem", { name: OUR_PRICE_ITEM })).toHaveAttribute(
      "href",
      "/tenders/300/rounds/3001/our-price/offers/7001"
    );
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menuitem")).toBeNull());

    await user.click(within(alphaCells[2]).getByRole("button", { name: /Действия с КП/ }));
    expect(await screen.findByRole("menuitem", { name: OUR_PRICE_ITEM })).toHaveAttribute(
      "href",
      "/tenders/300/rounds/3002/our-price/offers/7003"
    );
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

describe("OfferGrid: выравнивание колонок этапов", () => {
  // Шапка этапа и все три вида ячеек этапа стоят у одного — правого — края
  // колонки; иначе широкая колонка финала (значок «победитель») оставляет
  // шапку и прочерки слева, а суммы справа. jsdom раскладку не считает, поэтому
  // проверяется контракт классов, а замер правых краёв — в браузере.
  it("шапка этапа и ячейки «сумма», «—», «нет сметы» выровнены вправо, колонка участника — нет", () => {
    // sampleTenderCard: этап 1 — сумма и «—»; этап 2 — «—» и «нет сметы».
    renderGrid(sampleTenderCard);

    expect(screen.getByRole("button", { name: /Этап 1/ }).parentElement).toHaveClass("items-end");
    expect(screen.getByRole("button", { name: /Этап 2/ }).parentElement).toHaveClass("items-end");
    expect(screen.getByRole("columnheader", { name: "Участник" })).not.toHaveClass("text-right");

    const alphaCells = within(screen.getByText("ООО Альфа").closest("tr") as HTMLElement).getAllByRole("cell");
    const betaCells = within(screen.getByText("ООО Бета").closest("tr") as HTMLElement).getAllByRole("cell");
    expect(within(alphaCells[1]).getByRole("button", { pressed: false })).toBeInTheDocument(); // сумма
    expect(alphaCells[1]).toHaveClass("text-right");
    expect(within(betaCells[1]).getByText("—")).toBeInTheDocument();
    expect(betaCells[1]).toHaveClass("text-right");
    expect(within(betaCells[2]).getByText("нет сметы")).toBeInTheDocument();
    expect(betaCells[2]).toHaveClass("text-right");
    // Колонка участника выравнивание не получает.
    expect(alphaCells[0]).not.toHaveClass("text-right");
  });
});
