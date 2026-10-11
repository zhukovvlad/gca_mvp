import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  ACTIVATION_DRAFT_REFUSALS,
  apiErrorCode,
  apiErrorContext,
  apiErrorDetail,
  useActivateDiscovery,
  useDiscoveryDrafts,
  useFamilyCategories,
  useRestoreDraft,
} from "@/services/queries";
import type {
  ActivationOutcome,
  DiscoveryDraftsView,
  DraftView,
  ExistingGroupView,
} from "@/types/domain";

import { discoveryRefusalLabel, FAMILY_STATUS_LABEL, pluralRu } from "../labels";
import { CategoryProposals } from "./CategoryProposals";
import { proposalCategoryId } from "./proposalCategory";
import { DraftCard } from "./DraftCard";
import { NotWorkGroup } from "./NotWorkGroup";

const SKIPPED_NOTICE_MS = 20_000;

function formatDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleDateString("ru-RU");
}

function rowsLabel(n: number): string {
  return `${n.toLocaleString("ru-RU")} ${pluralRu(n, "строка", "строки", "строк")}`;
}

/** Черновик отмечен по умолчанию, если у него есть категория и он не похож на активную семью (макет, экран 3). */
function defaultChecked(draft: DraftView): boolean {
  return draft.family_category_id !== null && draft.similar_family_id === null;
}

function foldedLabel(draft: DraftView, all: DraftView[]): string {
  if (draft.status === "discarded") return "отброшен";
  if (draft.merged_into_family_id !== null) {
    const status = draft.merged_into_family_status;
    const suffix = status !== null && status !== "active" ? ` (${FAMILY_STATUS_LABEL[status]})` : "";
    return `слит с семьёй «${draft.merged_into_family_title}»${suffix}`;
  }
  const target = all.find((d) => d.id === draft.merged_into_draft_id);
  return `слит с черновиком «${target?.title ?? `№ ${draft.merged_into_draft_id}`}»`;
}

function FoldedDrafts({ folded, all }: { folded: DraftView[]; all: DraftView[] }) {
  const restore = useRestoreDraft();
  return (
    <Collapsible className="border-t border-border-subtle px-4 py-3">
      <CollapsibleTrigger className="flex items-center gap-1.5 text-[13px] font-medium text-fg-secondary">
        <ChevronRight className="size-4" />
        Слитые и отброшенные ({folded.length})
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-2 overflow-hidden rounded-lg border border-border-subtle bg-surface">
          {folded.map((draft) => (
            <div
              key={draft.id}
              data-testid="folded-draft"
              className="flex items-center gap-3 border-b border-border-subtle px-4 py-2 text-[13px] last:border-b-0"
            >
              <span className="min-w-0 flex-1 text-fg">
                «{draft.title}» — <span className="text-fg-secondary">{foldedLabel(draft, all)}</span>
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={restore.isPending}
                onClick={() => restore.mutate({ draftId: draft.id, name: draft.title })}
              >
                Вернуть
              </Button>
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function ExistingGroups({ groups }: { groups: ExistingGroupView[] }) {
  const total = groups.reduce((sum, g) => sum + g.rows, 0);
  return (
    <Collapsible className="border-t border-border-subtle px-4 py-3">
      <CollapsibleTrigger className="flex items-center gap-1.5 text-[13px] font-medium text-fg-secondary">
        <ChevronRight className="size-4" />
        В активные семьи: {rowsLabel(total)} — придут предложениями при перезапросе
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-2 overflow-hidden rounded-lg border border-border-subtle bg-surface">
          {groups.map((group) => (
            <div
              key={group.id}
              data-testid="existing-group"
              className="border-b border-border-subtle px-4 py-2 text-[13px] text-fg last:border-b-0"
            >
              {rowsLabel(group.rows)} модель отнесла к «{group.family_title}»
              {group.family_status !== null && group.family_status !== "active" && (
                <span className="text-fg-tertiary"> ({FAMILY_STATUS_LABEL[group.family_status]})</span>
              )}{" "}
              — придут предложениями при перезапросе
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

interface DiscoveryDraftsProps {
  unitId: number | null;
  /** Подпись единицы (`компл`) для заголовка, кнопки и подписей отказов. */
  unitLabel: string;
  /** Активация прошла: блок открывает окно перезапроса единицы `reask_unit_id`. */
  onActivated: (outcome: ActivationOutcome) => void;
  /** «Открыть семьи заново…»: то же окно запуска, что у строки единицы (экран не должен лишать единицу кнопки). */
  onRelaunch: () => void;
}

/**
 * Черновики последнего выполненного открытия единицы (экран 3 макета): группы с именем,
 * определением, категорией, «похоже на…», числом строк и тремя примерами; «Не работа»
 * построчно; «в активные семьи» и «без группы» — строками; свёрнутые слитые и отброшенные с
 * «Вернуть»; «Категории активных семей». Отметки — локальное состояние экрана: сервер получает
 * списки отмеченного (спека 3б §2.5). Отказ активации печатается подписью у черновика и
 * отметок не сбрасывает.
 */
export function DiscoveryDrafts({ unitId, unitLabel, onActivated, onRelaunch }: DiscoveryDraftsProps) {
  const draftsQ = useDiscoveryDrafts(unitId);
  const categoriesQ = useFamilyCategories();
  const activate = useActivateDiscovery();

  // Переопределения отметок поверх умолчаний: перечитанные данные не сбрасывают выбор человека.
  const [draftChecks, setDraftChecks] = useState<Readonly<Record<number, boolean>>>({});
  const [notWorkOff, setNotWorkOff] = useState<ReadonlySet<string>>(new Set());
  const [proposalOff, setProposalOff] = useState<ReadonlySet<number>>(new Set());
  const [chosen, setChosen] = useState<Readonly<Record<number, number>>>({});
  const [refusals, setRefusals] = useState<Readonly<Record<number, string>>>({});

  if (draftsQ.isPending) return <Skeleton className="h-40 w-full" />;
  if (draftsQ.isError) {
    return (
      <EmptyState title="Ошибка загрузки" description="Не удалось получить черновики открытия." />
    );
  }
  const loaded: DiscoveryDraftsView | null = draftsQ.data.drafts;
  if (loaded === null) {
    return (
      <EmptyState
        title="Открытий не было"
        description={`У единицы «${unitLabel}» нет выполненного открытия семей.`}
      />
    );
  }
  const view: DiscoveryDraftsView = loaded;

  const isChecked = (draft: DraftView) => draftChecks[draft.id] ?? defaultChecked(draft);
  const checkedDrafts = view.drafts.filter(isChecked);
  const checkedDraftRows = checkedDrafts.reduce((sum, d) => sum + d.rows, 0);
  const notWorkNames = view.not_work?.names ?? [];
  const checkedNotWork = notWorkNames.filter((n) => !notWorkOff.has(n.title));
  const notWorkContextIds = checkedNotWork.flatMap((n) => n.context_ids);
  const checkedPairs = view.category_proposals.filter((p) => !proposalOff.has(p.family_id));
  const nothingChecked =
    checkedDrafts.length === 0 && notWorkContextIds.length === 0 && checkedPairs.length === 0;
  const allDrafts = [...view.drafts, ...view.folded];

  function skippedNotice(outcome: ActivationOutcome) {
    const parts: string[] = [];
    if (outcome.not_work_skipped.length > 0) {
      const names = notWorkNames
        .filter((n) => n.context_ids.some((id) => outcome.not_work_skipped.includes(id)))
        .map((n) => `«${n.title}»`);
      parts.push(
        `строк «Не работа» пропущено: ${outcome.not_work_skipped.length}${
          names.length > 0 ? ` (${names.join(", ")})` : ""
        } — они изменились после открытия`
      );
    }
    if (outcome.categories_skipped.length > 0) {
      const names = view.category_proposals
        .filter((p) => outcome.categories_skipped.includes(p.family_id))
        .map((p) => `«${p.family_title}»`);
      parts.push(
        `категорий пропущено: ${outcome.categories_skipped.length}${
          names.length > 0 ? ` (${names.join(", ")})` : ""
        } — у семьи уже есть категория или она не активна`
      );
    }
    if (parts.length > 0) toast.warning(parts.join("; "), { duration: SKIPPED_NOTICE_MS });
  }

  function submit() {
    setRefusals({});
    activate.mutate(
      {
        jobId: view.job_id,
        input: {
          draft_ids: checkedDrafts.map((d) => d.id),
          not_work_context_ids: notWorkContextIds,
          family_categories: checkedPairs.map((p) => ({
            family_id: p.family_id,
            family_category_id: proposalCategoryId(p, chosen),
          })),
        },
      },
      {
        onSuccess: (outcome) => {
          skippedNotice(outcome);
          onActivated(outcome);
        },
        onError: (error) => {
          const code = apiErrorCode(error);
          if (code === undefined || !ACTIVATION_DRAFT_REFUSALS.includes(code)) return;
          const draftId = apiErrorContext<{ draft_id?: number }>(error)?.draft_id;
          const draft = view.drafts.find((d) => d.id === draftId);
          const label = discoveryRefusalLabel(
            code,
            { name: draft?.title, unit: unitLabel },
            apiErrorDetail(error)
          );
          if (draft === undefined) {
            toast.error(label);
            return;
          }
          setRefusals({ [draft.id]: label });
        },
      }
    );
  }

  return (
    <div
      data-testid="discovery-drafts"
      className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
    >
      <div className="flex flex-wrap items-center gap-3 border-b border-border-subtle px-4 py-3.5">
        <span className="text-[15px] font-semibold text-fg">Черновики семей · {unitLabel}</span>
        <span className="text-[13px] text-fg-tertiary">
          {view.drafts.length} {pluralRu(view.drafts.length, "черновик", "черновика", "черновиков")}
          {" · открыто "}
          {formatDate(view.opened_at)}
        </span>
        <span className="ml-auto text-[13px] text-fg-secondary">по числу строк</span>
        <Button variant="outline" size="sm" onClick={onRelaunch}>
          Открыть семьи заново…
        </Button>
      </div>

      {view.drafts.map((draft) => (
        <DraftCard
          key={draft.id}
          draft={draft}
          unitId={unitId}
          checked={isChecked(draft)}
          onCheckedChange={(next) => setDraftChecks((prev) => ({ ...prev, [draft.id]: next }))}
          categories={categoriesQ.data}
          otherDrafts={view.drafts.filter((d) => d.id !== draft.id)}
          refusal={refusals[draft.id]}
        />
      ))}

      {view.not_work !== null && notWorkNames.length > 0 && (
        <NotWorkGroup
          group={view.not_work}
          unchecked={notWorkOff}
          onToggle={(title, on) =>
            setNotWorkOff((prev) => {
              const next = new Set(prev);
              if (on) next.delete(title);
              else next.add(title);
              return next;
            })
          }
          onToggleAll={(on) =>
            setNotWorkOff(on ? new Set() : new Set(notWorkNames.map((n) => n.title)))
          }
        />
      )}

      {view.existing.length > 0 && <ExistingGroups groups={view.existing} />}

      {view.rest > 0 && (
        <div
          data-testid="discovery-rest"
          className="border-t border-border-subtle px-4 py-3 text-[13px] text-fg-secondary"
        >
          Без группы: {rowsLabel(view.rest)} — останутся без семьи и уйдут в перезапрос обычным
          порядком.
        </div>
      )}

      {view.folded.length > 0 && <FoldedDrafts folded={view.folded} all={allDrafts} />}

      {view.activated.length > 0 && (
        <div className="border-t border-border-subtle px-4 py-3 text-[13px] text-fg-secondary">
          Уже активировано: {view.activated.map((d) => `«${d.title}»`).join(", ")}
        </div>
      )}

      <CategoryProposals
        proposals={view.category_proposals}
        categories={categoriesQ.data}
        unchecked={proposalOff}
        chosen={chosen}
        onToggle={(familyId, on) =>
          setProposalOff((prev) => {
            const next = new Set(prev);
            if (on) next.delete(familyId);
            else next.add(familyId);
            return next;
          })
        }
        onChoose={(familyId, categoryId) => setChosen((prev) => ({ ...prev, [familyId]: categoryId }))}
      />

      <div className="flex flex-wrap items-center gap-3 border-t border-border bg-surface-hover px-4 py-3.5 text-[13px] text-fg-secondary">
        <span data-testid="discovery-counter">
          Отмечено{" "}
          <b className="text-fg">
            {checkedDrafts.length}{" "}
            {pluralRu(checkedDrafts.length, "черновик", "черновика", "черновиков")}
          </b>{" "}
          · {rowsLabel(checkedDraftRows)}
          {notWorkContextIds.length > 0 && <> · «Не работа»: {rowsLabel(notWorkContextIds.length)}</>}
          {checkedPairs.length > 0 && <> · категорий семьям: {checkedPairs.length}</>}
          {" · неотмеченные остаются черновиками"}
        </span>
        <Button className="ml-auto" disabled={nothingChecked || activate.isPending} onClick={submit}>
          Активировать отмеченные и перезапросить «{unitLabel}»…
        </Button>
      </div>
    </div>
  );
}
