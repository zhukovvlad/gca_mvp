import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import type { CategoryProposalView, FamilyCategory } from "@/types/domain";

import { CategoryProposals } from "./CategoryProposals";
import { proposalCategoryId } from "./proposalCategory";

/** «Категории активных семей»: пара «семья — категория», галочка и выбор категории из справочника. */

const CATEGORIES: FamilyCategory[] = [
  { id: 1, title: "Работа", definition: "Работа.", seed_key: "work", family_count: 3 },
  { id: 3, title: "Затраты и услуги", definition: "Затраты.", seed_key: null, family_count: 1 },
];

const PROPOSALS: CategoryProposalView[] = [
  { family_id: 43, family_title: "Кровельные работы", family_definition: "Определение: Кровельные работы.", family_category_id: 1, family_category_title: "Работа" },
  { family_id: 44, family_title: "Банковская гарантия", family_definition: "Определение: Банковская гарантия.", family_category_id: 3, family_category_title: "Затраты и услуги" },
];

function renderProposals(props: Partial<Parameters<typeof CategoryProposals>[0]> = {}) {
  const onToggle = vi.fn();
  const onChoose = vi.fn();
  renderWithProviders(
    <CategoryProposals
      proposals={PROPOSALS}
      categories={CATEGORIES}
      unchecked={new Set()}
      chosen={{}}
      onToggle={onToggle}
      onChoose={onChoose}
      {...props}
    />
  );
  return { onToggle, onChoose };
}

describe("CategoryProposals", () => {
  it("каждая пара: семья, предложенная категория, галочка; все отмечены", () => {
    renderProposals();

    const rows = screen.getAllByTestId("category-proposal");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Кровельные работы")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("combobox")).toHaveTextContent("Работа");
    expect(within(rows[1]).getByRole("combobox")).toHaveTextContent("Затраты и услуги");
    expect(
      screen.getByRole("checkbox", { name: "Применить категорию семье «Банковская гарантия»" })
    ).toBeChecked();
  });

  it("выбранная человеком категория показывается вместо предложенной и берётся в пару", () => {
    renderProposals({ chosen: { 43: 3 } });

    expect(within(screen.getAllByTestId("category-proposal")[0]).getByRole("combobox")).toHaveTextContent(
      "Затраты и услуги"
    );
    expect(proposalCategoryId(PROPOSALS[0], { 43: 3 })).toBe(3);
    expect(proposalCategoryId(PROPOSALS[0], {})).toBe(1);
  });

  it("смена категории и снятие галочки сообщаются наверх", async () => {
    const user = userEvent.setup();
    const { onToggle, onChoose } = renderProposals({ unchecked: new Set([44]) });

    expect(
      screen.getByRole("checkbox", { name: "Применить категорию семье «Банковская гарантия»" })
    ).not.toBeChecked();

    await user.click(
      screen.getByRole("checkbox", { name: "Применить категорию семье «Кровельные работы»" })
    );
    expect(onToggle).toHaveBeenCalledWith(43, false);

    await user.click(within(screen.getAllByTestId("category-proposal")[0]).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Затраты и услуги" }));
    expect(onChoose).toHaveBeenCalledWith(43, 3);
  });

  it("у пары показано определение семьи — по нему проверяют предложенную категорию", () => {
    renderProposals();

    const rows = screen.getAllByTestId("category-proposal");
    expect(within(rows[0]).getByText("Определение: Кровельные работы.")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Определение: Банковская гарантия.")).toBeInTheDocument();
  });

  it("предложений нет — блока нет", () => {
    renderProposals({ proposals: [] });

    expect(screen.queryByTestId("category-proposals")).not.toBeInTheDocument();
  });
});
