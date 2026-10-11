import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import type { CategoryProposalView, FamilyCategory } from "@/types/domain";

import { proposalCategoryId } from "./proposalCategory";

interface CategoryProposalsProps {
  proposals: CategoryProposalView[];
  categories: FamilyCategory[] | undefined;
  /** Снятые пары (по id семьи); непереданная пара отмечена. */
  unchecked: ReadonlySet<number>;
  /** Категория, которую человек выбрал вместо предложенной (по id семьи). */
  chosen: Readonly<Record<number, number>>;
  onToggle: (familyId: number, checked: boolean) => void;
  onChoose: (familyId: number, categoryId: number) => void;
}

/**
 * «Категории активных семей» (спека 3б §2.5, п. 3): открытие предложило категорию семьям
 * единицы, у которых её ещё нет. Каждая пара — галочка и выбор категории из справочника
 * (человек мог сменить предложенную); применяются только отмеченные пары.
 */
export function CategoryProposals({
  proposals,
  categories,
  unchecked,
  chosen,
  onToggle,
  onChoose,
}: CategoryProposalsProps) {
  if (proposals.length === 0) return null;
  return (
    <div data-testid="category-proposals" className="border-t border-border-subtle px-4 py-3.5">
      <div className="text-[15px] font-semibold text-fg">Категории активных семей</div>
      <p className="mt-0.5 text-[13px] text-fg-secondary">
        У этих активных семей категории ещё нет: модель предложила их по определениям. Применятся
        только отмеченные.
      </p>
      <div className="mt-2 overflow-hidden rounded-lg border border-border-subtle bg-surface">
        {proposals.map((proposal) => {
          const isOff = unchecked.has(proposal.family_id);
          return (
            <div
              key={proposal.family_id}
              data-testid="category-proposal"
              className={cn(
                "flex items-center gap-3.5 border-b border-border-subtle px-4 py-2.5 text-[13px] last:border-b-0",
                isOff && "opacity-60"
              )}
            >
              <Checkbox
                aria-label={`Применить категорию семье «${proposal.family_title}»`}
                checked={!isOff}
                onCheckedChange={(checked) => onToggle(proposal.family_id, checked === true)}
              />
              <span className="min-w-0 flex-1">
                <span className="font-medium text-fg">{proposal.family_title}</span>
                {proposal.family_definition && (
                  <span className="mt-0.5 block text-xs text-fg-secondary">
                    {proposal.family_definition}
                  </span>
                )}
              </span>
              <EntitySelect
                className="w-56"
                items={categories}
                value={proposalCategoryId(proposal, chosen)}
                onChange={(id) => id !== null && onChoose(proposal.family_id, id)}
                getLabel={(c) => c.title}
                placeholder="Выбрать категорию"
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
