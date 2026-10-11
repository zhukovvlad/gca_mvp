import type { CategoryProposalView } from "@/types/domain";

/** Категория пары: выбранная человеком, иначе предложенная моделью. */
export function proposalCategoryId(
  proposal: CategoryProposalView,
  chosen: Readonly<Record<number, number>>
): number {
  return chosen[proposal.family_id] ?? proposal.family_category_id;
}
