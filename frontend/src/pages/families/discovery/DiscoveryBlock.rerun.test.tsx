import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  discoveryUnitFixture,
  draftFixture,
  draftsViewFixture,
  handlerState,
} from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { DiscoveryRunInfo } from "@/types/domain";

import { DiscoveryBlock } from "./DiscoveryBlock";

/**
 * Повторное открытие, завершившееся при показанном экране черновиков: локальный выбор человека
 * (отметки «Не работа», пары и выбранные категории) принадлежит показанному открытию и на новое
 * не переходит. Отдельный файл: выпадающий список категорий в нём не делит глобальное состояние
 * окон с тестами запуска.
 */

const RUN = (overrides: Partial<DiscoveryRunInfo> = {}): DiscoveryRunInfo => ({
  job_id: 500,
  status: "done",
  at: "2026-10-09T10:00:00",
  open_drafts: 0,
  drafts_job_id: 500,
  actionable: false,
  ...overrides,
});

afterEach(() => {
  vi.useRealTimers();
});

function renderBlock() {
  renderWithProviders(<DiscoveryBlock unitLabel={(code) => (code === "M3" ? "м³" : "без единицы")} />);
}

describe("DiscoveryBlock — выбор человека и смена открытия", () => {
  it("повторное открытие: отметки и выбор категории прежнего открытия не переходят на новое", async () => {
    const user = userEvent.setup();
    const proposal = (familyId: number, title: string, categoryId: number, category: string) => ({
      family_id: familyId,
      family_title: title,
      family_definition: `Определение: ${title}.`,
      family_category_id: categoryId,
      family_category_title: category,
    });
    handlerState.discovery.units = [
      discoveryUnitFixture({
        last_discovery: RUN({ job_id: 500, drafts_job_id: 500, open_drafts: 1, actionable: true }),
      }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({
      job_id: 500,
      drafts: [draftFixture({ title: "Черновик прошлого открытия" })],
      not_work: {
        id: 9,
        names: [
          { title: "Итого по разделу", contexts: 1, context_ids: [901] },
          { title: "Оговорка о материалах", contexts: 1, context_ids: [902] },
        ],
      },
      category_proposals: [
        proposal(43, "Кровельные работы", 1, "Работа"),
        proposal(44, "Банковская гарантия", 3, "Затраты и услуги"),
      ],
    });
    renderBlock();
    await screen.findByText("Черновик прошлого открытия");

    // В A: снимаем строку «Не работа» и пару семьи 44, у семьи 43 выбираем другую категорию.
    await user.click(
      within(screen.getAllByTestId("not-work-row")[0]).getByRole("checkbox", { name: "Отметить строку" })
    );
    // Справочник категорий загружен: подпись выбранной категории взята из него.
    await waitFor(() =>
      expect(within(screen.getAllByTestId("category-proposal")[0]).getByRole("combobox")).toHaveTextContent("Работа")
    );
    await user.click(within(screen.getAllByTestId("category-proposal")[0]).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Затраты и услуги" }));
    await user.click(
      screen.getByRole("checkbox", { name: "Применить категорию семье «Банковская гарантия»" })
    );

    // Опрос блока идёт на подменённом интервале: включаем его перед повторным запуском.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await user.click(screen.getByRole("button", { name: "Открыть семьи заново…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));
    await waitFor(() => expect(handlerState.discovery.launchRequests).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByTestId("discovery-latest-job")).toHaveTextContent(/открывается…/)
    );
    // Пока B грузится, на экране A и его выбор.
    expect(
      within(screen.getAllByTestId("not-work-row")[0]).getByRole("checkbox", { name: "Отметить строку" })
    ).not.toBeChecked();

    // B: те же наименования и те же семьи, но другие контексты и свои предложения.
    handlerState.discovery.units = [
      discoveryUnitFixture({
        last_discovery: RUN({ job_id: 501, drafts_job_id: 501, open_drafts: 1, actionable: true }),
      }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({
      job_id: 501,
      drafts: [draftFixture({ id: 401, title: "Черновик нового открытия" })],
      not_work: {
        id: 19,
        names: [
          { title: "Итого по разделу", contexts: 1, context_ids: [911] },
          { title: "Оговорка о материалах", contexts: 1, context_ids: [912] },
        ],
      },
      category_proposals: [
        proposal(43, "Кровельные работы", 1, "Работа"),
        proposal(44, "Банковская гарантия", 3, "Затраты и услуги"),
      ],
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_500);
    });
    await screen.findByText("Черновик нового открытия");

    for (const row of screen.getAllByTestId("not-work-row")) {
      expect(within(row).getByRole("checkbox", { name: "Отметить строку" })).toBeChecked();
    }
    expect(
      screen.getByRole("checkbox", { name: "Применить категорию семье «Банковская гарантия»" })
    ).toBeChecked();
    expect(within(screen.getAllByTestId("category-proposal")[0]).getByRole("combobox")).toHaveTextContent(
      "Работа"
    );
    await user.click(
      screen.getByRole("button", { name: "Активировать отмеченные и перезапросить «м³»…" })
    );
    await waitFor(() => expect(handlerState.discovery.activateRequests).toHaveLength(1));
    expect(handlerState.discovery.activateRequests[0]).toEqual({
      jobId: 501,
      body: {
        draft_ids: [401],
        not_work_context_ids: [911, 912],
        family_categories: [
          { family_id: 43, family_category_id: 1 },
          { family_id: 44, family_category_id: 3 },
        ],
      },
    });
  });
});
