import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { draftFixture, draftsViewFixture, handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { FamilyCategory } from "@/types/domain";

import { DraftCard } from "./DraftCard";

/** Состояния ожидания окон черновика: «Сохранить» в «Править…» и «Слить» в «Слить с…». */

const CATEGORIES: FamilyCategory[] = [
  { id: 1, title: "Работа", definition: "Работа.", seed_key: "work", family_count: 3 },
];
const SIBLING = draftFixture({ id: 302, ordinal: 2, title: "Кондиционирование", rows: 43 });

function renderCard() {
  const draft = draftFixture();
  handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draft, SIBLING] });
  renderWithProviders(
    <DraftCard
      draft={draft}
      unitId={3}
      checked
      onCheckedChange={vi.fn()}
      categories={CATEGORIES}
      otherDrafts={[SIBLING]}
    />
  );
}

describe("DraftCard — ожидание в окнах", () => {
  it("«Сохранить» в «Править…»: на время запроса кнопка занята, второе нажатие не уходит", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole("button", { name: "Править…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    const title = within(dialog).getByLabelText("Имя");
    await user.clear(title);
    await user.type(title, "Вентиляция приточно-вытяжная");
    const hold = holdRequests("patch", "/api/v1/semantic/discovery/drafts/:id");
    const save = () => within(dialog).getByRole("button", { name: "Сохранить" });

    await user.click(save());

    await waitFor(() => expect(save()).toHaveAttribute("aria-busy", "true"));
    expect(save()).toBeDisabled();
    await user.click(save());
    hold.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(hold.seen.count).toBe(1);
  });

  it("«Слить» в «Слить с…»: на время запроса кнопка занята", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole("button", { name: "Слить с…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.click(within(dialog).getByRole("combobox", { name: "Цель слияния" }));
    await user.click(await screen.findByRole("option", { name: "Черновик «Кондиционирование»" }));
    const hold = holdRequests("post", "/api/v1/semantic/discovery/drafts/:id/merge");

    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Слить" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(dialog).getByRole("button", { name: "Слить" })).toBeDisabled();
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });
});
