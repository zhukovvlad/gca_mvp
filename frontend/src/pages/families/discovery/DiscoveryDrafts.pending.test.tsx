import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { draftFixture, draftsViewFixture, handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";
import type { DiscoveryDraftsView } from "@/types/domain";

import { DiscoveryDrafts } from "./DiscoveryDrafts";

/** Состояния ожидания экрана черновиков: кнопки на время запроса, первая загрузка, перечитывание. */

const VENT = draftFixture({ id: 301, ordinal: 1, title: "Вентиляция общеобменная", rows: 66 });
const COND = draftFixture({ id: 302, ordinal: 2, title: "Кондиционирование", rows: 43 });
const CABLE = draftFixture({
  id: 303,
  ordinal: 3,
  title: "Кабельные линии",
  rows: 21,
  similar_family_id: 43,
  similar_family_title: "Кровельные работы",
  similar_family_status: "active",
});

function seed(overrides: Partial<DiscoveryDraftsView> = {}) {
  handlerState.discovery.units = [
    {
      unit_id: 3,
      unit_code: "M3",
      systems: 0,
      new_family: 5,
      bare: 0,
      names: 5,
      uncategorized_families: 0,
      active_families: 1,
      reserve_usd: "0.1",
      expected_cached_usd: "0.05",
      last_discovery: { job_id: 500, status: "done", at: "2026-10-09T10:00:00", open_drafts: 3, drafts_job_id: 500, actionable: true },
    },
  ];
  handlerState.discovery.drafts["3"] = draftsViewFixture({
    drafts: [VENT, COND, CABLE].map((d) => ({ ...d })),
    not_work: {
      id: 9,
      names: [{ title: "Примечание к смете", contexts: 2, context_ids: [901, 902] }],
    },
    category_proposals: [
      {
        family_id: 43,
        family_title: "Кровельные работы",
        family_definition: "Определение: Кровельные работы.",
        family_category_id: 1,
        family_category_title: "Работа",
      },
    ],
    ...overrides,
  });
}

function renderDrafts() {
  renderWithProviders(
    <DiscoveryDrafts unitId={3} draftsJobId={500} unitLabel="м³" onActivated={vi.fn()} onRelaunch={vi.fn()} />
  );
}

function cardOf(title: string) {
  const card = screen
    .getAllByTestId("draft-card")
    .find((c) => within(c).queryByText(title) !== null);
  if (!card) throw new Error(`нет черновика ${title}`);
  return card;
}

const activateButton = () =>
  screen.getByRole("button", { name: "Активировать отмеченные и перезапросить «м³»…" });

describe("DiscoveryDrafts — ожидание действий", () => {
  it("«Активировать»: кнопка занята, отметки и действия экрана заблокированы; ответ пришёл — снова доступны", async () => {
    const user = userEvent.setup();
    seed();
    const hold = holdRequests("post", "/api/v1/semantic/discovery/:jobId/activate");
    renderDrafts();
    await screen.findAllByTestId("draft-card");

    await user.click(activateButton());

    await waitFor(() => expect(activateButton()).toHaveAttribute("aria-busy", "true"));
    expect(activateButton()).toBeDisabled();
    const draftBox = within(cardOf("Вентиляция общеобменная")).getByRole("checkbox", {
      name: /Отметить черновик/,
    });
    expect(draftBox).toHaveAttribute("aria-disabled", "true");
    expect(within(cardOf("Вентиляция общеобменная")).getByRole("button", { name: "Отбросить" })).toBeDisabled();
    expect(within(cardOf("Вентиляция общеобменная")).getByRole("button", { name: "Править…" })).toBeDisabled();
    expect(within(cardOf("Вентиляция общеобменная")).getByRole("button", { name: "Слить с…" })).toBeDisabled();
    expect(within(cardOf("Вентиляция общеобменная")).getByRole("combobox")).toHaveAttribute("data-disabled");
    expect(
      within(cardOf("Кабельные линии")).getByRole("button", { name: /Слить с «Кровельные работы»/ })
    ).toBeDisabled();
    expect(
      within(screen.getAllByTestId("not-work-row")[0]).getByRole("checkbox", { name: "Отметить строку" })
    ).toHaveAttribute("aria-disabled", "true");
    expect(within(screen.getByTestId("category-proposal")).getByRole("combobox")).toHaveAttribute("data-disabled");
    expect(screen.getByRole("checkbox", { name: "Отметить все строки «Не работа»" })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
    expect(screen.getByRole("button", { name: "Открыть семьи заново…" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Применить категорию семье «Кровельные работы»" })).toHaveAttribute(
      "aria-disabled",
      "true"
    );

    hold.release();
    await waitFor(() => expect(activateButton()).not.toHaveAttribute("aria-busy"));
    expect(screen.getByRole("button", { name: "Открыть семьи заново…" })).toBeEnabled();
    expect(
      screen.getAllByRole("checkbox", { name: /Отметить черновик/ }).every(
        (box) => box.getAttribute("aria-disabled") !== "true"
      )
    ).toBe(true);
  });

  it("«Отбросить»: крутится кнопка нажатого черновика, у соседа — доступна", async () => {
    const user = userEvent.setup();
    seed();
    const hold = holdRequests("post", "/api/v1/semantic/discovery/drafts/:id/discard");
    renderDrafts();
    await screen.findAllByTestId("draft-card");

    await user.click(within(cardOf("Кондиционирование")).getByRole("button", { name: "Отбросить" }));

    const own = within(cardOf("Кондиционирование")).getByRole("button", { name: "Отбросить" });
    await waitFor(() => expect(own).toHaveAttribute("aria-busy", "true"));
    expect(own).toBeDisabled();
    const neighbour = within(cardOf("Вентиляция общеобменная")).getByRole("button", { name: "Отбросить" });
    expect(neighbour).toBeEnabled();
    expect(neighbour).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(2));
  });

  it("«Слить с «…»» (похожая активная семья): кнопка занята на время запроса", async () => {
    const user = userEvent.setup();
    seed();
    const hold = holdRequests("post", "/api/v1/semantic/discovery/drafts/:id/merge");
    renderDrafts();
    await screen.findAllByTestId("draft-card");

    await user.click(
      within(cardOf("Кабельные линии")).getByRole("button", { name: /Слить с «Кровельные работы»/ })
    );

    const own = within(cardOf("Кабельные линии")).getByRole("button", { name: /Слить с «Кровельные работы»/ });
    await waitFor(() => expect(own).toHaveAttribute("aria-busy", "true"));
    expect(own).toBeDisabled();

    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(2));
  });

  it("«Вернуть»: кнопка слитого занята на время запроса", async () => {
    const user = userEvent.setup();
    seed({
      drafts: [VENT].map((d) => ({ ...d })),
      folded: [draftFixture({ id: 305, ordinal: 5, status: "discarded", title: "Сплит-системы" })],
    });
    const hold = holdRequests("post", "/api/v1/semantic/discovery/drafts/:id/restore");
    renderDrafts();
    await screen.findAllByTestId("draft-card");
    await user.click(screen.getByText("Слитые и отброшенные (1)"));

    await user.click(within(await screen.findByTestId("folded-draft")).getByRole("button", { name: "Вернуть" }));

    const own = within(screen.getByTestId("folded-draft")).getByRole("button", { name: "Вернуть" });
    await waitFor(() => expect(own).toHaveAttribute("aria-busy", "true"));
    expect(own).toBeDisabled();

    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(2));
  });
});

describe("DiscoveryDrafts — первая загрузка", () => {
  it("до ответа заглушки без текста «пусто»; пустой ответ — текст без заглушек", async () => {
    handlerState.discovery.drafts = {};
    const hold = holdRequests("get", "/api/v1/semantic/discovery/drafts");
    renderDrafts();

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Открытий не было")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Открытий не было")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("непустой ответ: черновики вместо заглушек", async () => {
    seed();
    const hold = holdRequests("get", "/api/v1/semantic/discovery/drafts");
    renderDrafts();
    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();

    hold.release();
    expect(await screen.findAllByTestId("draft-card")).toHaveLength(3);
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });
});

describe("DiscoveryDrafts — перечитывание", () => {
  it("после действия старые черновики на месте, полоса видна, затем пропадает", async () => {
    const user = userEvent.setup();
    seed();
    renderDrafts();
    await screen.findAllByTestId("draft-card");
    expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument();

    const hold = holdRequests("get", "/api/v1/semantic/discovery/drafts");
    await user.click(within(cardOf("Кондиционирование")).getByRole("button", { name: "Отбросить" }));

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getAllByTestId("draft-card")).toHaveLength(3);
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(2));
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });
});

describe("DiscoveryDrafts — одна мутация на несколько слитых", () => {
  it("«Вернуть»: крутится только кнопка нажатой строки, соседняя заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    seed({
      drafts: [VENT].map((d) => ({ ...d })),
      folded: [
        draftFixture({ id: 305, ordinal: 5, status: "discarded", title: "Сплит-системы" }),
        draftFixture({ id: 306, ordinal: 6, status: "discarded", title: "Приточные установки" }),
      ],
    });
    const hold = holdRequests("post", "/api/v1/semantic/discovery/drafts/:id/restore");
    renderDrafts();
    await screen.findAllByTestId("draft-card");
    await user.click(screen.getByText("Слитые и отброшенные (2)"));
    const rows = await screen.findAllByTestId("folded-draft");

    await user.click(within(rows[0]).getByRole("button", { name: "Вернуть" }));

    await waitFor(() =>
      expect(within(rows[0]).getByRole("button", { name: "Вернуть" })).toHaveAttribute("aria-busy", "true")
    );
    const neighbour = within(rows[1]).getByRole("button", { name: "Вернуть" });
    expect(neighbour).not.toHaveAttribute("aria-busy");
    expect(neighbour).toBeDisabled();
    hold.release();
  });
});
