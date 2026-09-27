import { useState } from "react";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Pager } from "@/components/domain/Pager";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDate } from "@/lib/format";
import { semanticApi } from "@/services/api/domain";
import {
  useAcceptStaleTransfer,
  useAcceptTargetDecision,
  useArchiveContext,
  useAssignFamily,
  useConfirmKind,
  useContextCard,
  useContextGroupMembers,
  useMergeContexts,
  useMoveMembers,
  useSetNameRole,
  useSplitContext,
  useTransferStaleGroup,
  useWorkFamilies,
  toastApiError,
} from "@/services/queries";
import type {
  BucketContextOption,
  ContextMemberRow,
  GroupSelector,
  MemberPath,
  MembershipState,
  NameRole,
  SemanticKind,
} from "@/types/domain";

import {
  CATEGORY_SOURCE_LABEL,
  DECISION_SOURCE_LABEL,
  FAMILY_SOURCE_LABEL,
  NAME_ROLE_LABEL,
  pluralRu,
  ROUTING_RULE_KIND_LABEL,
  ROUTING_RULE_KIND_VALUES,
  SEMANTIC_KIND_LABEL,
  SEMANTIC_STATE_LABEL,
  comparabilityLabel,
  eventLabel,
} from "./labels";
import { SourceChip } from "./SourceChip";

const KIND_OPTIONS: SemanticKind[] = ["WORK", "SYSTEM", "UNKNOWN"];
const ROLE_OPTIONS: NameRole[] = ["WORK", "LOCATION_ONLY", "GENERIC_WORK"];
const NO_RULE = "no-rule";
const WITH_RULE = "with-rule";
/** Размер страницы членств группы (спека §2.5: «постраничным запросом группы»). */
const MEMBER_PAGE_SIZE = 20;

const MEMBERSHIP_STATE_LABEL: Record<MembershipState, string> = {
  CURRENT: "текущее",
  STALE: "устаревшее",
};

function bucketTargetLabel(option: BucketContextOption): string {
  return option.is_default ? `контекст #${option.id} (по умолчанию)` : `контекст #${option.id}`;
}

/** Ключ группы членств — `chapter_item_id`, `"null"` для группы «без раздела». */
function groupKeyOf(chapterItemId: number | null): string {
  return chapterItemId === null ? "null" : String(chapterItemId);
}

/** Селектор группы (спека §2.8 п. 3) из её ключа карточки — обратное `groupKeyOf`. */
function groupSelectorOf(chapterItemId: number | null): GroupSelector {
  return chapterItemId === null
    ? { chapter_item_id: null, no_chapter: true }
    : { chapter_item_id: chapterItemId, no_chapter: false };
}

/** Путь группы текстом — «без раздела» у группы без `chapter_item_id`. */
function groupPathLabel(group: Pick<MemberPath, "chapter_item_id" | "path">): string {
  return group.chapter_item_id === null ? "без раздела" : group.path.join(" › ");
}

interface ContextCardProps {
  contextId: number | null;
}

/**
 * Карточка контекста и его операции (спека
 * `2026-09-25-families-screen-design.md` §2.5, §2.6, §2.8). Панель СПРАВА ОТ
 * СПИСКА (§2.1), не отдельная вкладка «Операции» — та упразднена. Членства
 * карточка не несёт поштучно (`members`/`members_truncated` удалены §2.8
 * п. 5): вкладка «Членства» группирует их по ближайшему разделу
 * (`member_paths`) и раскрывает КАЖДУЮ группу постраничным запросом
 * (`useContextGroupMembers`, §2.8 п. 3), а не читает обрезанный список
 * карточки, которого больше нет.
 */
export function ContextCard({ contextId }: ContextCardProps) {
  const cardQ = useContextCard(contextId);
  const activeFamilies = useWorkFamilies("active");

  const confirmKind = useConfirmKind();
  const setNameRole = useSetNameRole();
  const assignFamily = useAssignFamily();
  const splitContext = useSplitContext();
  const mergeContexts = useMergeContexts();
  const archiveContext = useArchiveContext();
  const moveMembers = useMoveMembers();
  const acceptStaleTransfer = useAcceptStaleTransfer();
  const acceptTargetDecision = useAcceptTargetDecision();
  const transferStaleGroup = useTransferStaleGroup();

  const [kindChoice, setKindChoice] = useState<SemanticKind>("WORK");
  const [roleChoice, setRoleChoice] = useState<NameRole>("WORK");
  // Ключ «текущей» карточки, для которой синхронизированы `kindChoice`/
  // `roleChoice` ниже — см. докстринг у их синхронизации (П1).
  const [syncedCardKey, setSyncedCardKey] = useState<string | null>(null);
  const [familyChoice, setFamilyChoice] = useState<number | null>(null);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  // Полный набор id по группе, снятый `groupMemberIds` при щелчке по
  // галочке группы (спека §2.5: «включая не загруженные на экран») — нужен
  // и чтобы отметить чекбокс группы «весь выбран», и чтобы СНЯТЬ ровно эти
  // id при снятии галочки, а не только загруженную страницу.
  const [groupIdCache, setGroupIdCache] = useState<Record<string, number[]>>({});

  const [splitRuleMode, setSplitRuleMode] = useState<string>(NO_RULE);
  const [ruleKind, setRuleKind] = useState<string>(ROUTING_RULE_KIND_VALUES[0]);
  const [ruleValue, setRuleValue] = useState("");
  const [ruleLevel, setRuleLevel] = useState("");

  const [bulkTarget, setBulkTarget] = useState<number | null>(null);

  const [conflictMove, setConflictMove] = useState<ContextMemberRow | null>(null);
  const [conflictMoveTarget, setConflictMoveTarget] = useState<number | null>(null);

  const [mergeTarget, setMergeTarget] = useState<number | null>(null);
  const [newDefaultInput, setNewDefaultInput] = useState("");
  const [archiveOpen, setArchiveOpen] = useState(false);

  // Результаты пакетного переноса устаревшей группы (спека §2.6, DoD §5
  // п. 6: «экран сообщает «перенесено N из M» и перечисляет отказы») — по
  // ключу группы, в ОТДЕЛЬНОМ блоке рядом со строками внимания, а не внутри
  // строки своей устаревшей группы: при полном успехе перечитанная карточка
  // (см. `onSuccess` ниже) больше не несёт эту группу в `stale_groups`, и
  // результат внутри строки пропал бы вместе с ней, не будучи увиденным
  // оператором (дефект живого прогона на стенде). Путь и раздел группы
  // сохраняются В МОМЕНТ переноса — своих полей у перечитанной карточки для
  // уже перенесённой группы больше нет.
  const [transferResults, setTransferResults] = useState<Record<string, {
    chapterItemId: number | null;
    pathLabel: string;
    moved: number;
    refused: number;
    refusals: { position_item_id: number; message: string | null }[];
  }>>({});
  const [acceptingAllConflicts, setAcceptingAllConflicts] = useState(false);

  if (contextId === null) {
    return (
      <EmptyState
        title="Контекст не выбран"
        description="Выберите строку в очереди контекстов слева, чтобы открыть карточку и операции."
      />
    );
  }

  if (cardQ.isPending) return <Skeleton className="h-64 w-full" />;

  if (cardQ.isError || !cardQ.data) {
    return <EmptyState title="Ошибка загрузки" description="Не удалось получить контекст." />;
  }

  const card = cardQ.data;

  // Селекты вида/роли открываются на ТЕКУЩЕМ значении карточки, а не на
  // захардкоженном "WORK": иначе «Подтвердить вид» без прикосновения к
  // селекту молча переписал бы SYSTEM-контекст на WORK (решение
  // оркестратора, ревью задачи 13, П1). Правка состояния ВО ВРЕМЯ рендера
  // (не в эффекте — React поддерживает этот приём для «подстроить состояние
  // под смену пропа/данных» без лишнего кадра моргания): срабатывает и при
  // смене контекста, и при изменении вида/роли на самой карточке.
  const cardKey = `${card.id}:${card.semantic_kind}:${card.name_role}`;
  if (cardKey !== syncedCardKey) {
    setSyncedCardKey(cardKey);
    setKindChoice(card.semantic_kind);
    setRoleChoice(card.name_role);
  }

  let familyCaption: string;
  let familySourceCaption: string | null = null;
  if (card.work_family_id !== null) {
    familyCaption = card.family_title ?? `Семья #${card.work_family_id}`;
    // Источник назначения семьи (спека §2.2) — семья и её источник разные
    // факты, подпись у обоих, словарём `FAMILY_SOURCE_LABEL`.
    familySourceCaption = card.family_source !== null ? FAMILY_SOURCE_LABEL[card.family_source] : null;
  } else if (card.comparability_reason === "insufficient_description") {
    // Отдельная от «нет семьи» подпись (спека §2.4/§2.5 — feature-1
    // сохраняет оба факта в карточке; в списке оба сведены к «—»).
    familyCaption = "семья не назначена, потому что состав не описан";
  } else {
    familyCaption = "нет семьи";
  }

  function toggleSelected(id: number, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function toggleGroupSelected(group: MemberPath, checked: boolean) {
    const key = groupKeyOf(group.chapter_item_id);
    if (checked) {
      // Не мутация react-query — прямой вызов из обработчика клика, без
      // собственного `onError`; неуспех обязан быть пойман явно, иначе
      // необработанный reject не даёт ни тоста, ни изменения выбора (спека
      // §2.5: выбор — только то, что оператор действительно отметил).
      try {
        const { position_item_ids } = await semanticApi.groupMemberIds(
          contextId as number,
          groupSelectorOf(group.chapter_item_id),
          "all"
        );
        setGroupIdCache((prev) => ({ ...prev, [key]: position_item_ids }));
        setSelectedIds((prev) => new Set([...prev, ...position_item_ids]));
      } catch (err) {
        toastApiError(err);
      }
    } else {
      const ids = groupIdCache[key] ?? [];
      setSelectedIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }
  }

  function isGroupChecked(group: MemberPath): boolean {
    const ids = groupIdCache[groupKeyOf(group.chapter_item_id)];
    return Boolean(ids && ids.length > 0 && ids.every((id) => selectedIds.has(id)));
  }

  const selectedIdList = Array.from(selectedIds);

  // Живые соседи по корзине, кроме текущего контекста — цель слияния/переноса
  // выбирается из НИХ (решение оркестратора П6, план задачи 13), а не
  // вводится id вручную: архивный сосед в выбор не попадает — переносить/
  // сливать в архивный контекст нечего.
  const liveBucketContexts = card.bucket_contexts.filter(
    (c) => c.archived_at === null && c.id !== contextId
  );

  const totalConflictCount = card.member_paths.reduce((sum, g) => sum + g.conflict_count, 0);

  async function acceptAllConflictingTargetDecisions() {
    setAcceptingAllConflicts(true);
    try {
      const { position_item_ids } = await semanticApi.groupMemberIds(
        contextId as number,
        { chapter_item_id: null, no_chapter: false },
        "conflict"
      );
      acceptTargetDecision.mutate({ position_item_ids });
    } catch (err) {
      toastApiError(err);
    } finally {
      setAcceptingAllConflicts(false);
    }
  }

  return (
    <div className="grid gap-4">
      <Surface className="grid gap-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-medium text-fg">
              {card.standard_job_title}
              {card.unit_code && <span className="ml-2 text-sm text-fg-tertiary">{card.unit_code}</span>}
            </h3>
            <div className="mt-1 flex flex-wrap items-center gap-1 text-sm text-fg-secondary">
              <SourceChip kind="classifier" />
              <span>
                {card.work_category_code && card.work_category_title
                  ? `${card.work_category_code} ${card.work_category_title}`
                  : "—"}
              </span>
            </div>
            {card.work_category_path.length > 0 && (
              <p className="mt-1 text-xs text-fg-tertiary">
                внутри: {card.work_category_path.map((p) => `${p.code} ${p.title}`).join(" › ")}
              </p>
            )}
            {card.work_category_source === "manual" && (
              <p className="text-xs text-fg-tertiary">{CATEGORY_SOURCE_LABEL.manual}</p>
            )}
          </div>
          <Badge variant={card.archived_at ? "outline" : "secondary"}>
            {card.archived_at ? "архивный" : SEMANTIC_STATE_LABEL[card.semantic_state]}
          </Badge>
        </div>
      </Surface>

      {/* Строки внимания (спека §2.6) — МЕЖДУ шапкой и вкладками, видны
          независимо от активной вкладки карточки. */}
      <div className="grid gap-2">
        {card.stale_groups.map((sg) => {
          const key = groupKeyOf(sg.chapter_item_id);
          const pathLabel = groupPathLabel(sg);
          return (
            <Surface key={key} className="grid gap-2 border-warning/40 bg-warning/5">
              <p className="text-sm text-fg">
                {sg.count} {pluralRu(sg.count, "позиция", "позиции", "позиций")} из раздела{" "}
                {sg.chapter_item_id === null ? (
                  <span>без раздела</span>
                ) : (
                  <>
                    <SourceChip kind="estimate" /> <span>{pathLabel}</span>
                  </>
                )}{" "}
                после ручного разноса {pluralRu(sg.count, "относится", "относятся", "относятся")} к статье{" "}
                <SourceChip kind="classifier" />{" "}
                <span>
                  {sg.target_category_code ?? "—"} «{sg.target_category_title ?? "—"}»
                </span>
                , а {pluralRu(sg.count, "лежит", "лежат", "лежат")} здесь, в статье{" "}
                <span>
                  {card.work_category_code ?? "—"} «{card.work_category_title ?? "—"}»
                </span>
                .
              </p>
              <div>
                <Button
                  size="xs"
                  variant="outline"
                  aria-label={`Перенести их в контекст «${card.standard_job_title} × ${
                    sg.target_category_code ?? "—"
                  }» — раздел ${pathLabel}`}
                  disabled={transferStaleGroup.isPending}
                  onClick={() =>
                    transferStaleGroup.mutate(
                      {
                        contextId,
                        input: {
                          chapter_item_id: sg.chapter_item_id,
                          expected_category_id: sg.target_category_id,
                        },
                      },
                      {
                        onSuccess: (data) => {
                          setTransferResults((prev) => ({
                            ...prev,
                            [key]: {
                              chapterItemId: sg.chapter_item_id,
                              pathLabel,
                              moved: data.moved,
                              refused: data.refused,
                              refusals: data.results
                                .filter((r) => r.outcome === "refused")
                                .map((r) => ({ position_item_id: r.position_item_id, message: r.message })),
                            },
                          }));
                        },
                      }
                    )
                  }
                >
                  Перенести их
                </Button>
              </div>
            </Surface>
          );
        })}

        {/* Результаты пакетного переноса (спека §2.6, DoD §5 п. 6) — ОТДЕЛЬНЫЙ
            блок рядом со строками внимания, не внутри строки своей группы (см.
            докстринг у `transferResults`): виден до перечитывания карточки
            оператором и после, пока тот не нажмёт «Скрыть» или не сменит
            контекст (карточка размонтируется по `key` в `ContextsTab.tsx`). */}
        {Object.entries(transferResults).map(([key, result]) => (
          <Surface key={key} className="grid gap-1">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="text-sm">
                <p className="flex flex-wrap items-center gap-1 text-fg-secondary">
                  {result.chapterItemId === null ? (
                    <span>без раздела</span>
                  ) : (
                    <>
                      <SourceChip kind="estimate" /> <span>{result.pathLabel}</span>
                    </>
                  )}
                </p>
                <p>
                  перенесено {result.moved} из {result.moved + result.refused}
                </p>
                {result.refusals.map((r) => (
                  <p key={r.position_item_id} className="text-destructive">
                    позиция {r.position_item_id}: {r.message}
                  </p>
                ))}
              </div>
              <Button
                size="xs"
                variant="ghost"
                onClick={() =>
                  setTransferResults((prev) => {
                    const next = { ...prev };
                    delete next[key];
                    return next;
                  })
                }
              >
                Скрыть
              </Button>
            </div>
          </Surface>
        ))}

        {totalConflictCount > 0 && (
          <Surface className="grid gap-2 border-warning/40 bg-warning/5">
            <p className="text-sm text-fg">
              {totalConflictCount} {pluralRu(totalConflictCount, "позиция", "позиции", "позиций")}{" "}
              {pluralRu(totalConflictCount, "пришла", "пришли", "пришли")} слиянием в Review с другим
              решением.
            </p>
            <div>
              <Button
                size="xs"
                variant="outline"
                aria-label="Принять решение цели (все конфликтные)"
                disabled={acceptingAllConflicts || acceptTargetDecision.isPending}
                onClick={() => acceptAllConflictingTargetDecisions()}
              >
                Принять решение цели
              </Button>
            </div>
          </Surface>
        )}

        {card.member_count === 0 && (
          <Surface className="border-warning/40 bg-warning/5">
            <p className="text-sm text-fg">Позиций нет: смета заменена. Архивирует оператор.</p>
          </Surface>
        )}
      </div>

      <Tabs defaultValue="decisions">
        <TabsList>
          <TabsTrigger value="decisions">Решения</TabsTrigger>
          <TabsTrigger value="memberships">Членства {card.member_count}</TabsTrigger>
          <TabsTrigger value="log">Журнал</TabsTrigger>
        </TabsList>

        <TabsContent value="decisions" className="mt-4">
          <Surface className="grid gap-4">
            <div className="grid gap-2 text-sm">
              <div>
                Вид: <span className="font-medium">{SEMANTIC_KIND_LABEL[card.semantic_kind]}</span>{" "}
                <span className="text-fg-tertiary">
                  {DECISION_SOURCE_LABEL[card.semantic_kind_source]}
                  {card.semantic_state === "CONFIRMED" ? ", подтверждён" : ""}
                </span>
              </div>
              <div>
                Наименование называет:{" "}
                <span className="font-medium">{NAME_ROLE_LABEL[card.name_role]}</span>{" "}
                <span className="text-fg-tertiary">{DECISION_SOURCE_LABEL[card.name_role_source]}</span>
                {card.name_role === "LOCATION_ONLY" && (
                  <div className="mt-1 text-fg-secondary">
                    работа по разделу представительной позиции:{" "}
                    {card.representative_work_title ? (
                      <>
                        <SourceChip kind="estimate" /> <span>{card.representative_work_title}</span>
                      </>
                    ) : (
                      // Подпись называет причину, а не молчит пустым «—»
                      // (спека §2.2): нет членств, нет раздела у
                      // представителя либо цепочка из одних мест — все три
                      // честно сведены к одной подписи.
                      <span className="text-fg-tertiary">рабочего раздела нет</span>
                    )}
                  </div>
                )}
              </div>
              <div>Состав описан: {comparabilityLabel(card.comparability_reason)}</div>
              <div>
                Семья: <span>{familyCaption}</span>
                {familySourceCaption && (
                  <span className="text-fg-tertiary"> · {familySourceCaption}</span>
                )}
              </div>
            </div>

            {/* Вид работы */}
            <div className="grid gap-2 border-t border-border-subtle pt-3">
              <Label htmlFor="context-kind-select">Вид работы</Label>
              <div className="flex flex-wrap gap-2">
                <Select value={kindChoice} onValueChange={(v) => v && setKindChoice(v as SemanticKind)}>
                  <SelectTrigger id="context-kind-select" className="w-48">
                    <SelectValue>{() => SEMANTIC_KIND_LABEL[kindChoice]}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {KIND_OPTIONS.map((kind) => (
                      <SelectItem key={kind} value={kind}>
                        {SEMANTIC_KIND_LABEL[kind]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  disabled={confirmKind.isPending}
                  onClick={() => confirmKind.mutate({ contextId, input: { kind: kindChoice } })}
                >
                  Подтвердить вид
                </Button>
                {/* Обратный переход CONFIRMED -> SUGGESTED (спека §2.5) — кнопка
                    видна ТОЛЬКО у подтверждённого вида, тем же маршрутом, что и
                    подтверждение. */}
                {card.semantic_state === "CONFIRMED" && (
                  <Button
                    variant="outline"
                    disabled={confirmKind.isPending}
                    onClick={() => confirmKind.mutate({ contextId, input: { unconfirm: true } })}
                  >
                    Снять подтверждение
                  </Button>
                )}
              </div>
            </div>

            {/* Роль имени */}
            <div className="grid gap-2">
              <Label htmlFor="context-role-select">Роль имени</Label>
              <div className="flex flex-wrap gap-2">
                <Select value={roleChoice} onValueChange={(v) => v && setRoleChoice(v as NameRole)}>
                  <SelectTrigger id="context-role-select" className="w-56">
                    <SelectValue>{() => NAME_ROLE_LABEL[roleChoice]}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {ROLE_OPTIONS.map((role) => (
                      <SelectItem key={role} value={role}>
                        {NAME_ROLE_LABEL[role]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  disabled={setNameRole.isPending}
                  onClick={() => setNameRole.mutate({ contextId, input: { role: roleChoice } })}
                >
                  Переопределить роль
                </Button>
              </div>
            </div>

            {/* Семья */}
            <div className="grid gap-2">
              <Label htmlFor="context-family-select">Семья</Label>
              <div className="flex flex-wrap items-center gap-2">
                <EntitySelect
                  id="context-family-select"
                  className="w-64"
                  items={activeFamilies.data}
                  value={familyChoice}
                  onChange={(v) => setFamilyChoice(v as number | null)}
                  getLabel={(f) => f.title}
                  placeholder="Выбрать семью"
                />
                <Button
                  disabled={assignFamily.isPending || familyChoice === null}
                  onClick={() => assignFamily.mutate({ contextId, input: { family_id: familyChoice } })}
                >
                  Назначить семью
                </Button>
                <Button
                  variant="outline"
                  disabled={assignFamily.isPending || card.work_family_id === null}
                  onClick={() => assignFamily.mutate({ contextId, input: { family_id: null } })}
                >
                  Снять семью
                </Button>
              </div>
            </div>
          </Surface>
        </TabsContent>

        <TabsContent value="memberships" className="mt-4">
          <Surface className="grid gap-4">
            {card.member_paths.length === 0 ? (
              <p className="text-sm text-fg-tertiary">Членств нет.</p>
            ) : (
              <div className="grid gap-1">
                {card.member_paths.map((group) => (
                  <MembershipGroupSection
                    key={groupKeyOf(group.chapter_item_id)}
                    contextId={contextId as number}
                    group={group}
                    selectedIds={selectedIds}
                    isChecked={isGroupChecked(group)}
                    onToggleGroup={(checked) => toggleGroupSelected(group, checked)}
                    onTogglePosition={toggleSelected}
                    onAcceptStaleTransfer={(id) => acceptStaleTransfer.mutate(id)}
                    acceptStaleTransferPending={acceptStaleTransfer.isPending}
                    onAcceptTargetDecision={(id) =>
                      acceptTargetDecision.mutate({ position_item_ids: [id] })
                    }
                    acceptTargetDecisionPending={acceptTargetDecision.isPending}
                    onOpenConflictMove={(row) => {
                      setConflictMove(row);
                      setConflictMoveTarget(null);
                    }}
                  />
                ))}
              </div>
            )}

            {/* Массовые действия над ВЫБРАННЫМИ (чекбоксы группы/строки) — разделить/перенести. */}
            <div className="grid gap-2 border-t border-border-subtle pt-3">
              <Label>Выбрано членств: {selectedIdList.length}</Label>

              {/* Разделить — правило либо явный отказ от правила (спека §2.4:
                  «с правилом или без — выбор явный»), не подразумеваемое значение. */}
              <div className="flex flex-wrap items-center gap-2">
                <Select value={splitRuleMode} onValueChange={(v) => v && setSplitRuleMode(v)}>
                  <SelectTrigger id="split-rule-mode" aria-label="Разделить с правилом или без" className="w-48">
                    <SelectValue>
                      {() => (splitRuleMode === WITH_RULE ? "С правилом" : "Без правила")}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_RULE}>Без правила</SelectItem>
                    <SelectItem value={WITH_RULE}>С правилом</SelectItem>
                  </SelectContent>
                </Select>
                {splitRuleMode === WITH_RULE && (
                  <>
                    <Select value={ruleKind} onValueChange={(v) => v && setRuleKind(v)}>
                      <SelectTrigger id="split-rule-kind" aria-label="Вид правила" className="w-56">
                        <SelectValue>
                          {() => ROUTING_RULE_KIND_LABEL[ruleKind as (typeof ROUTING_RULE_KIND_VALUES)[number]]}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {ROUTING_RULE_KIND_VALUES.map((k) => (
                          <SelectItem key={k} value={k}>{ROUTING_RULE_KIND_LABEL[k]}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Input
                      className="w-56"
                      placeholder="значение раздела"
                      aria-label="Значение правила"
                      value={ruleValue}
                      onChange={(e) => setRuleValue(e.target.value)}
                    />
                    {ruleKind === "chapter_level_equals" && (
                      <Input
                        className="w-24"
                        placeholder="уровень"
                        aria-label="Уровень правила"
                        inputMode="numeric"
                        value={ruleLevel}
                        onChange={(e) => setRuleLevel(e.target.value)}
                      />
                    )}
                  </>
                )}
                <Button
                  variant="outline"
                  disabled={
                    splitContext.isPending ||
                    selectedIdList.length === 0 ||
                    (splitRuleMode === WITH_RULE && !ruleValue.trim())
                  }
                  onClick={() =>
                    splitContext.mutate(
                      {
                        contextId,
                        input: {
                          position_item_ids: selectedIdList,
                          rule:
                            splitRuleMode === WITH_RULE
                              ? {
                                  kind: ruleKind,
                                  value: ruleValue.trim(),
                                  ...(ruleKind === "chapter_level_equals" && ruleLevel
                                    ? { level: Number(ruleLevel) }
                                    : {}),
                                }
                              : null,
                        },
                      },
                      { onSuccess: () => setSelectedIds(new Set()) }
                    )
                  }
                >
                  Разделить выбранные
                </Button>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Label htmlFor="bulk-move-target" className="sr-only">Целевой контекст для переноса выбранных</Label>
                <EntitySelect
                  id="bulk-move-target"
                  className="w-56"
                  items={liveBucketContexts}
                  value={bulkTarget}
                  onChange={(v) => setBulkTarget(v as number | null)}
                  getLabel={bucketTargetLabel}
                  placeholder="Целевой контекст"
                />
                <Button
                  variant="outline"
                  disabled={
                    moveMembers.isPending ||
                    selectedIdList.length === 0 ||
                    bulkTarget === null
                  }
                  onClick={() =>
                    moveMembers.mutate(
                      {
                        position_item_ids: selectedIdList,
                        target_context_id: bulkTarget as number,
                        // Причина переноса — не свободный текст с экрана: сервис
                        // принимает в этом маршруте только "manual" (спека §2.8,
                        // §2.14 — журнал переноса вручную).
                        reason: "manual",
                      },
                      { onSuccess: () => setSelectedIds(new Set()) }
                    )
                  }
                >
                  Перенести выбранные
                </Button>
              </div>
            </div>

            {/* Слить/архивировать контекст — не зависят от выбора (спека §2.5: «внизу вкладки»). */}
            <div className="grid gap-2 border-t border-border-subtle pt-3">
              <Label htmlFor="merge-target-context">Слить контекст в целевой</Label>
              <div className="flex flex-wrap items-center gap-2">
                <EntitySelect
                  id="merge-target-context"
                  className="w-56"
                  items={liveBucketContexts}
                  value={mergeTarget}
                  onChange={(v) => setMergeTarget(v as number | null)}
                  getLabel={bucketTargetLabel}
                  placeholder="Целевой контекст"
                />
                <Button
                  disabled={mergeContexts.isPending || mergeTarget === null}
                  onClick={() =>
                    mergeContexts.mutate({ contextId, targetContextId: mergeTarget as number })
                  }
                >
                  Слить контексты
                </Button>
              </div>
            </div>

            <div className="grid gap-2 border-t border-border-subtle pt-3">
              <Label htmlFor="archive-new-default">Архивировать контекст — новый контекст по умолчанию (если требуется)</Label>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  id="archive-new-default"
                  className="w-48"
                  value={newDefaultInput}
                  onChange={(e) => setNewDefaultInput(e.target.value)}
                  inputMode="numeric"
                  disabled={Boolean(card.archived_at)}
                />
                <Button
                  variant="destructive"
                  disabled={Boolean(card.archived_at)}
                  onClick={() => setArchiveOpen(true)}
                >
                  Архивировать
                </Button>
              </div>
            </div>
          </Surface>
        </TabsContent>

        <TabsContent value="log" className="mt-4">
          <Surface>
            {card.events.length === 0 ? (
              <p className="text-sm text-fg-tertiary">Событий нет.</p>
            ) : (
              <ul className="grid gap-1 text-sm text-fg-secondary">
                {card.events.map((event) => (
                  <li key={event.id}>
                    <Tooltip>
                      <TooltipTrigger
                        title={event.event_type}
                        render={<span className="cursor-help text-fg">{eventLabel(event.event_type)}</span>}
                      />
                      <TooltipContent>{event.event_type}</TooltipContent>
                    </Tooltip>
                    {" · "}
                    {formatDate(event.created_at)}
                    {" · "}
                    {event.actor_id === null ? "система" : `оператор ${event.actor_id}`}
                  </li>
                ))}
              </ul>
            )}
          </Surface>
        </TabsContent>
      </Tabs>

      <AlertDialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Архивировать контекст?</AlertDialogTitle>
            <AlertDialogDescription>
              Архивирование требует пустого контекста и снятых либо переведённых входящих
              правил. Восстановить архивный контекст с экрана нельзя.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    archiveContext.mutate({
                      contextId,
                      input: { new_default_context_id: newDefaultInput ? Number(newDefaultInput) : null },
                    });
                    setArchiveOpen(false);
                  }}
                >
                  Архивировать
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Перенос ОДНОГО конфликтного членства в другой контекст — второй путь разрешения конфликта, кроме «принять решение цели». */}
      <Dialog open={conflictMove !== null} onOpenChange={(open) => !open && setConflictMove(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              Перенести позицию {conflictMove?.position_item_id} в другой контекст
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-2 py-4">
            <Label htmlFor="conflict-move-target">Целевой контекст</Label>
            <EntitySelect
              id="conflict-move-target"
              items={liveBucketContexts}
              value={conflictMoveTarget}
              onChange={(v) => setConflictMoveTarget(v as number | null)}
              getLabel={bucketTargetLabel}
              placeholder="Целевой контекст"
            />
          </div>
          <DialogFooter>
            <Button
              disabled={moveMembers.isPending || conflictMoveTarget === null}
              onClick={() => {
                if (!conflictMove || conflictMoveTarget === null) return;
                moveMembers.mutate({
                  position_item_ids: [conflictMove.position_item_id],
                  target_context_id: conflictMoveTarget,
                  // См. комментарий у «Перенести выбранные» — причина не
                  // вводится оператором, сервис принимает только "manual".
                  reason: "manual",
                });
                setConflictMove(null);
              }}
            >
              Перенести
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface MembershipGroupSectionProps {
  contextId: number;
  group: MemberPath;
  selectedIds: Set<number>;
  isChecked: boolean;
  onToggleGroup: (checked: boolean) => void;
  onTogglePosition: (id: number, checked: boolean) => void;
  onAcceptStaleTransfer: (positionItemId: number) => void;
  acceptStaleTransferPending: boolean;
  onAcceptTargetDecision: (positionItemId: number) => void;
  acceptTargetDecisionPending: boolean;
  onOpenConflictMove: (row: ContextMemberRow) => void;
}

/**
 * Одна группа вкладки «Членства» (спека §2.5) — строка группы всегда видна
 * (путь, число, счётчики устаревших/конфликтных), позиции грузятся
 * ПОСТРАНИЧНО запросом группы (`useContextGroupMembers`) только при
 * раскрытии (`Collapsible`, `keepMounted` по умолчанию ложно — свёрнутая
 * группа не держит своего запроса).
 */
function MembershipGroupSection({
  contextId,
  group,
  selectedIds,
  isChecked,
  onToggleGroup,
  onTogglePosition,
  onAcceptStaleTransfer,
  acceptStaleTransferPending,
  onAcceptTargetDecision,
  acceptTargetDecisionPending,
  onOpenConflictMove,
}: MembershipGroupSectionProps) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const selector = groupSelectorOf(group.chapter_item_id);
  // Запрос страницы группы — только пока группа РАСКРЫТА (спека §2.5:
  // «раскрывается постраничным запросом группы»): `open ? contextId : null`
  // отключает `useQuery` (`enabled`) у свёрнутой группы, а не только прячет
  // уже загруженный результат.
  const pageQ = useContextGroupMembers(open ? contextId : null, selector, "all", page, MEMBER_PAGE_SIZE);
  const pathLabel = groupPathLabel(group);
  // Винительный падеж — оба глагола («выбрать», «раскрыть») требуют его от
  // прямого дополнения («выбрать ЧТО» — группу, а не «группа»).
  const groupLabel = `группу «${pathLabel}»`;

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border-b border-border-subtle py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Checkbox
          aria-label={`Выбрать ${groupLabel}`}
          checked={isChecked}
          onCheckedChange={(checked) => onToggleGroup(checked === true)}
        />
        <CollapsibleTrigger
          aria-label={`Раскрыть ${groupLabel}`}
          className="flex flex-1 flex-wrap items-center gap-2 text-left text-sm"
        >
          {group.chapter_item_id === null ? (
            <span>без раздела</span>
          ) : (
            <>
              <SourceChip kind="estimate" />
              <span>{pathLabel}</span>
            </>
          )}
          <span className="tabular-nums text-fg-tertiary">{group.member_count}</span>
          {group.stale_count > 0 && <Badge variant="outline">устаревших: {group.stale_count}</Badge>}
          {group.conflict_count > 0 && (
            <Badge variant="destructive">конфликтных: {group.conflict_count}</Badge>
          )}
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="mt-2">
        {pageQ.isPending && <Skeleton className="h-16 w-full" />}
        {pageQ.data && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
                    <TableHead>Работа по смете</TableHead>
                    <TableHead>Смета</TableHead>
                    <TableHead>Состояние</TableHead>
                    <TableHead>Конфликт</TableHead>
                    <TableHead>Действие</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageQ.data.items.map((member) => {
                    const isStale = member.membership_state === "STALE";
                    const isConflicted = member.conflict_at !== null;
                    return (
                      <TableRow key={member.position_item_id}>
                        <TableCell>
                          <Checkbox
                            aria-label={`Выбрать позицию ${member.position_item_id}`}
                            checked={selectedIds.has(member.position_item_id)}
                            onCheckedChange={(checked) =>
                              onTogglePosition(member.position_item_id, checked === true)
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <div className="font-medium text-fg">{member.job_title}</div>
                          <span className="text-xs text-fg-tertiary">
                            позиция #{member.position_item_id}
                          </span>
                        </TableCell>
                        <TableCell>смета #{member.estimate_id}</TableCell>
                        <TableCell>
                          <Badge variant={isStale ? "destructive" : "outline"}>
                            {MEMBERSHIP_STATE_LABEL[member.membership_state]}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {isConflicted ? (
                            <span className="text-sm text-fg-secondary">
                              с контекстом {member.conflict_from_context_id}
                            </span>
                          ) : (
                            <span className="text-fg-tertiary">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {/*
                              Действие по членству читается из ЕГО полей —
                              устаревшему предлагается перенос, конфликтному —
                              решение цели или перенос в другой контекст;
                              одно НИКОГДА не подменяет другое. Членство разом
                              устаревшее И конфликтное несёт ТОЛЬКО действия
                              конфликта (спека §2.5).
                            */}
                            {isStale && !isConflicted && (
                              <Button
                                size="xs"
                                variant="outline"
                                aria-label={`Принять предложение переноса для позиции ${member.position_item_id}`}
                                disabled={acceptStaleTransferPending}
                                onClick={() => onAcceptStaleTransfer(member.position_item_id)}
                              >
                                Принять предложение переноса
                              </Button>
                            )}
                            {isConflicted && (
                              <>
                                <Button
                                  size="xs"
                                  variant="outline"
                                  aria-label={`Принять решение цели для позиции ${member.position_item_id}`}
                                  disabled={acceptTargetDecisionPending}
                                  onClick={() => onAcceptTargetDecision(member.position_item_id)}
                                >
                                  Принять решение цели
                                </Button>
                                <Button
                                  size="xs"
                                  variant="outline"
                                  aria-label={`Перенести в другой контекст позицию ${member.position_item_id}`}
                                  onClick={() => onOpenConflictMove(member)}
                                >
                                  Перенести в другой контекст
                                </Button>
                              </>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </Surface>
            {pageQ.data.total > MEMBER_PAGE_SIZE && (
              <Pager
                page={page}
                total={pageQ.data.total}
                pageSize={MEMBER_PAGE_SIZE}
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
