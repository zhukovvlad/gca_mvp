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
  useMarkNotWork,
  useMergeContexts,
  useMoveMembers,
  useSetNameRole,
  useSplitContext,
  useTransferStaleGroup,
  useWorkFamilies,
  apiErrorCode,
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
  contextRefusalLabel,
  DECISION_SOURCE_LABEL,
  FAMILY_CHANGE_OUTCOME_LABEL,
  FAMILY_REMOVED_LABEL,
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
import { ContextVariant } from "./ContextVariant";
import { MarkPositionDialog } from "./MarkPositionDialog";
import { PendingFamilyBlock } from "./PendingFamilyBlock";
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

/** Ключ группы членств — разделы группы через запятую, `"no-chapter"` для группы «без раздела». */
function groupKeyOf(chapterItemIds: number[]): string {
  return chapterItemIds.length === 0 ? "no-chapter" : chapterItemIds.join(",");
}

/** Селектор группы (спека §2.8 п. 3, редакция 3) из её разделов карточки — обратное `groupKeyOf`. */
function groupSelectorOf(chapterItemIds: number[]): GroupSelector {
  return chapterItemIds.length === 0
    ? { chapter_item_ids: [], no_chapter: true }
    : { chapter_item_ids: chapterItemIds, no_chapter: false };
}

/** Путь группы текстом — «без раздела» у группы без разделов. */
function groupPathLabel(group: Pick<MemberPath, "chapter_item_ids" | "path">): string {
  return group.chapter_item_ids.length === 0 ? "без раздела" : group.path.join(" › ");
}

/**
 * Подпись пути группы, РАЗБИТАЯ на строки (замер на стенде 27.09.2026,
 * четвёртый круг): в колонке 420px одна строка не держит и уникальный
 * суффикс целиком — обрезка (RTL-приём ниже) резала уже РАЗЛИЧАЮЩЕЕ звено
 * при более чем двух звеньях суффикса. `line1` — последние ДВА звена
 * суффикса (либо весь суффикс, если он короче — тогда `line2` нет вовсе):
 * общий, повторяющийся у нескольких групп «хвост» пути. `line2` —
 * ОСТАВШИЕСЯ (более старшие) звенья суффикса поверх этих двух — именно они
 * РАЗЛИЧАЮТ группы, и им нужна СВОЯ строка, а не место в первой.
 */
interface GroupPathLabel {
  line1: string;
  line2: string | null;
}

function groupPathLabels(groups: MemberPath[]): GroupPathLabel[] {
  return groups.map((group, index) => {
    if (group.chapter_item_ids.length === 0) return { line1: "без раздела", line2: null };
    const maxLen = group.path.length;
    const minLen = Math.min(2, maxLen);
    let suffix = group.path.slice(-minLen);
    for (let len = minLen; len <= maxLen; len++) {
      const candidate = group.path.slice(-len);
      const suffixKey = candidate.join("\u0000");
      const collides = groups.some((other, otherIndex) => {
        if (otherIndex === index) return false;
        return other.path.slice(-len).join("\u0000") === suffixKey;
      });
      suffix = candidate;
      // Полные пути групп различны по построению (слияние §2.8 п. 2) —
      // цикл обязан остановиться не позже `len === maxLen`.
      if (!collides) break;
    }
    const tailCount = Math.min(2, suffix.length);
    const leading = suffix.slice(0, suffix.length - tailCount);
    return {
      line1: suffix.slice(-tailCount).join(" / "),
      line2: leading.length > 0 ? leading.join(" / ") : null,
    };
  });
}


interface ContextCardProps {
  contextId: number | null;
}

/**
 * Карточка контекста и его операции (спека
 * `2026-09-25-families-screen-design.md` §2.5, §2.6, §2.8). Панель СПРАВА ОТ
 * СПИСКА (§2.1), не отдельная вкладка «Операции» — та упразднена. Членства
 * карточка не несёт поштучно (`members`/`members_truncated` удалены §2.8
 * п. 5): вкладка «Членства» группирует их по тексту пути ближайшего раздела
 * (`member_paths`, редакция 3) и раскрывает КАЖДУЮ группу постраничным запросом
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
  const markNotWork = useMarkNotWork();

  const [tab, setTab] = useState("decisions");
  const [notWorkOpen, setNotWorkOpen] = useState(false);
  const [markPositionOpen, setMarkPositionOpen] = useState(false);
  // Исход и отказ смены семьи показывает само окно: тоста у этой мутации нет.
  const [familyOutcome, setFamilyOutcome] = useState<string | null>(null);
  const [familyRefusal, setFamilyRefusal] = useState<string | null>(null);

  const [kindChoice, setKindChoice] = useState<SemanticKind>("WORK");
  const [roleChoice, setRoleChoice] = useState<NameRole>("WORK");
  // Диалоги «Решений» (сверка с макетом 27.09.2026, `mock-card-decisions.png`):
  // формы вида/роли/семьи переехали из инлайна в диалог, открытый своей
  // кнопкой — тело запроса и поведение те же.
  const [kindDialogOpen, setKindDialogOpen] = useState(false);
  const [roleDialogOpen, setRoleDialogOpen] = useState(false);
  const [familyDialogOpen, setFamilyDialogOpen] = useState(false);
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
    chapterItemIds: number[];
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

  function resetFamilyDialog() {
    setFamilyOutcome(null);
    setFamilyRefusal(null);
  }

  function changeFamily(familyId: number | null) {
    resetFamilyDialog();
    assignFamily.mutate(
      { contextId: contextId as number, input: { family_id: familyId } },
      {
        onSuccess: (result) =>
          setFamilyOutcome(
            result.outcome === "assigned" && result.family_id === null
              ? FAMILY_REMOVED_LABEL
              : FAMILY_CHANGE_OUTCOME_LABEL[result.outcome]
          ),
        onError: (error) => setFamilyRefusal(contextRefusalLabel(apiErrorCode(error))),
      }
    );
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
    const key = groupKeyOf(group.chapter_item_ids);
    if (checked) {
      // Не мутация react-query — прямой вызов из обработчика клика, без
      // собственного `onError`; неуспех обязан быть пойман явно, иначе
      // необработанный reject не даёт ни тоста, ни изменения выбора (спека
      // §2.5: выбор — только то, что оператор действительно отметил).
      try {
        const { position_item_ids } = await semanticApi.groupMemberIds(
          contextId as number,
          groupSelectorOf(group.chapter_item_ids),
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
    const ids = groupIdCache[groupKeyOf(group.chapter_item_ids)];
    return Boolean(ids && ids.length > 0 && ids.every((id) => selectedIds.has(id)));
  }

  /**
   * Снимает выбор с id, которых коснулась только что успешная мутация
   * членств, и полностью сбрасывает `groupIdCache` (MINOR-3, ревью Fable
   * 27.09.2026): без этого «Выбрано членств: N» продолжает считать позиции,
   * которые уже покинули контекст (принятое предложение переноса, пакетный
   * перенос устаревшей группы, принятое решение цели, перенос конфликтного
   * через диалог), `isGroupChecked` держит галочку по устаревшему кэшу, а
   * следующая массовая операция отправила бы id, которых в контексте больше
   * нет. Кэш групп — ЦЕЛИКОМ, а не по одному ключу: состав ЛЮБОЙ группы мог
   * измениться этим действием (не только той, что его вызвала — «Принять
   * решение цели» берёт конфликтные member'ы СРАЗУ по всему контексту), а
   * следующая галочка группы перечитывает id заново запросом.
   */
  function pruneSelection(ids: number[]) {
    if (ids.length > 0) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }
    setGroupIdCache({});
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
        { chapter_item_ids: [], no_chapter: false },
        "conflict"
      );
      acceptTargetDecision.mutate(
        { position_item_ids },
        { onSuccess: () => pruneSelection(position_item_ids) }
      );
    } catch (err) {
      toastApiError(err);
    } finally {
      setAcceptingAllConflicts(false);
    }
  }

  return (
    // `min-w-0` НА КАЖДОМ уровне грид/флекс-вложенности (замер на стенде
    // 27.09.2026) — без него автоматический минимум элемента считается по
    // контенту (grid/flex «blowout»), и длинная подпись пути группы
    // раздвигала карточку шире выделенной колонки, выталкивая её за край
    // окна: локального `truncate`/`min-w-0` на строке группы недостаточно,
    // если хоть один предок в цепочке (этот корень, `Surface`, `Tabs`,
    // `TabsContent`) не передал сужение дальше.
    <div className="grid min-w-0 gap-4">
      {/* Карточка — ОДНА поверхность (сверка с макетом 27.09.2026): шапка,
          строки внимания и вкладки с содержимым живут в одном бордюре, не в
          трёх отдельных карточках. */}
      <Surface className="grid min-w-0 gap-4">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          {/* `min-w-0` — заголовок обязан ПЕРЕНОСИТЬСЯ на длинном названии
              статьи, а не растягивать карточку (замер на стенде 27.09.2026);
              `flex-wrap` строки статьи сам по себе текст внутри `<span>` не
              рвёт, пока флекс-родитель растянут «blowout»-ом выше по дереву. */}
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-medium text-fg">
              {card.standard_job_title}
              {card.unit_symbol && <span className="ml-2 text-sm text-fg-tertiary">{card.unit_symbol}</span>}
            </h3>
            <div className="mt-1 flex flex-wrap items-center gap-1 text-sm text-fg-secondary">
              <SourceChip kind="classifier" />
              <span className="break-words">
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
            <Button
              size="xs"
              variant="link"
              className="mt-1 h-auto p-0"
              onClick={() => setMarkPositionOpen(true)}
            >
              Пометить написание целиком…
            </Button>
          </div>
          <Badge variant={card.archived_at ? "outline" : "secondary"}>
            {card.archived_at ? "архивный" : SEMANTIC_STATE_LABEL[card.semantic_state]}
          </Badge>
        </div>

      {/* Строки внимания (спека §2.6) — МЕЖДУ шапкой и вкладками, видны
          независимо от активной вкладки карточки. */}
      <div className="grid min-w-0 gap-2">
        {card.stale_groups.map((sg) => {
          const key = groupKeyOf(sg.chapter_item_ids);
          const pathLabel = groupPathLabel(sg);
          return (
            <Surface key={key} className="grid min-w-0 gap-2 border-warning/40 bg-warning/5">
              <p className="text-sm text-fg">
                {sg.count} {pluralRu(sg.count, "позиция", "позиции", "позиций")} из раздела{" "}
                {sg.chapter_item_ids.length === 0 ? (
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
                <SourceChip kind="classifier" />{" "}
                <span>
                  {card.work_category_code ?? "—"} «{card.work_category_title ?? "—"}»
                </span>
                .
              </p>
              <div>
                {/* Ссылка-действие (сверка с макетом 27.09.2026, `.linkb`),
                    не кнопка с рамкой — семантика и aria те же. */}
                <Button
                  size="xs"
                  variant="link"
                  className="h-auto p-0"
                  aria-label={`Перенести их в контекст «${card.standard_job_title} × ${
                    sg.target_category_code ?? "—"
                  }» — раздел ${pathLabel}`}
                  disabled={transferStaleGroup.isPending}
                  onClick={() =>
                    transferStaleGroup.mutate(
                      {
                        contextId,
                        input: {
                          chapter_item_ids: sg.chapter_item_ids.length ? sg.chapter_item_ids : null,
                          expected_category_id: sg.target_category_id,
                        },
                      },
                      {
                        onSuccess: (data) => {
                          setTransferResults((prev) => ({
                            ...prev,
                            [key]: {
                              chapterItemIds: sg.chapter_item_ids,
                              pathLabel,
                              moved: data.moved,
                              refused: data.refused,
                              refusals: data.results
                                .filter((r) => r.outcome === "refused")
                                .map((r) => ({ position_item_id: r.position_item_id, message: r.message })),
                            },
                          }));
                          // MINOR-3: только ПЕРЕНЕСЁННЫЕ id покинули контекст —
                          // отказавшие (`refused`) остались на месте, снимать с
                          // них выбор не нужно.
                          pruneSelection(
                            data.results
                              .filter((r) => r.outcome === "moved")
                              .map((r) => r.position_item_id)
                          );
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
          <Surface key={key} className="grid min-w-0 gap-1">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="text-sm">
                <p className="flex flex-wrap items-center gap-1 text-fg-secondary">
                  {result.chapterItemIds.length === 0 ? (
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
          <Surface className="grid min-w-0 gap-2 border-warning/40 bg-warning/5">
            <p className="text-sm text-fg">
              {totalConflictCount} {pluralRu(totalConflictCount, "позиция", "позиции", "позиций")}{" "}
              {pluralRu(totalConflictCount, "пришла", "пришли", "пришли")} слиянием в Review с другим
              решением.
            </p>
            <div>
              <Button
                size="xs"
                variant="link"
                className="h-auto p-0"
                aria-label="Принять решение цели (все конфликтные)"
                disabled={acceptingAllConflicts || acceptTargetDecision.isPending}
                onClick={() => acceptAllConflictingTargetDecisions()}
              >
                Принять решение цели
              </Button>
            </div>
          </Surface>
        )}

        {card.variant.pending !== null && (
          <PendingFamilyBlock contextId={card.id} pending={card.variant.pending} />
        )}

        {card.member_count === 0 && (
          <Surface className="min-w-0 border-warning/40 bg-warning/5">
            <p className="text-sm text-fg">Позиций нет: смета заменена. Архивирует оператор.</p>
          </Surface>
        )}
      </div>

      <Tabs value={tab} onValueChange={(value) => setTab(String(value))} className="min-w-0">
        {/* Вкладки карточки — подчёркиванием (сверка с макетом 27.09.2026,
            `.ptabs`), не сегментным переключателем: тот занят вкладками
            ВЕРХНЕГО уровня «Семьи»/«Контексты» (`FamiliesPage.tsx`). */}
        <TabsList variant="line">
          <TabsTrigger value="decisions">Решения</TabsTrigger>
          <TabsTrigger value="memberships">Членства {card.member_count}</TabsTrigger>
          <TabsTrigger value="log">Журнал</TabsTrigger>
        </TabsList>

        <TabsContent value="decisions" className="mt-4 min-w-0">
          <div className="grid gap-4">
            {/* Двухколоночный список «ключ — значение» (сверка с макетом
                27.09.2026, `mock-card-decisions.png`): подпись поля слева
                приглушённым цветом, значение справа, источник решения —
                мелким приглушённым ПОСЛЕ значения (та же строка). */}
            <div className="grid grid-cols-[170px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
              <div className="text-fg-tertiary">Вид</div>
              <div>
                <span className="font-medium">{SEMANTIC_KIND_LABEL[card.semantic_kind]}</span>{" "}
                <span className="text-fg-tertiary">
                  {DECISION_SOURCE_LABEL[card.semantic_kind_source]}
                  {card.semantic_state === "CONFIRMED" ? ", подтверждён" : ""}
                </span>
              </div>

              <div className="text-fg-tertiary">Наименование называет</div>
              <div>
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

              <div className="text-fg-tertiary">Состав описан</div>
              <div>{comparabilityLabel(card.comparability_reason)}</div>

              <div className="text-fg-tertiary">Семья</div>
              <div>
                <span>{familyCaption}</span>
                {familySourceCaption && (
                  <span className="text-fg-tertiary"> · {familySourceCaption}</span>
                )}
              </div>
            </div>

            <ContextVariant
              variant={card.variant}
              onOpenMemberships={() => setTab("memberships")}
            />

            {/* Три кнопки, каждая открывает диалог с прежней формой (сверка с
                макетом 27.09.2026, `mock-card-decisions.png`) — тела запросов
                и поведение не меняются, меняется только путь до формы. */}
            <div className="flex flex-wrap gap-2 border-t border-border-subtle pt-3">
              <Button variant="outline" onClick={() => setKindDialogOpen(true)}>
                Подтвердить вид
              </Button>
              <Button variant="outline" onClick={() => setFamilyDialogOpen(true)}>
                {card.work_family_id === null ? "Назначить семью…" : "Другая семья…"}
              </Button>
              <Button variant="outline" onClick={() => setRoleDialogOpen(true)}>
                Изменить «что называет»…
              </Button>
              <Button
                variant="outline"
                disabled={card.archived_at !== null || card.semantic_state === "NOT_APPLICABLE"}
                onClick={() => setNotWorkOpen(true)}
              >
                Не работа
              </Button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="memberships" className="mt-4 min-w-0">
          <div className="grid min-w-0 gap-4">
            {card.member_paths.length === 0 ? (
              <p className="text-sm text-fg-tertiary">Членств нет.</p>
            ) : (
              <div className="grid min-w-0 gap-1">
                {/* Заголовок вкладки (сверка с макетом 27.09.2026,
                    `mock-card-members.png`): «Позиции лежат в N разных
                    разделах смет» — единственное число раздела ТОЛЬКО при
                    ОДНОЙ группе, число разных путей = `member_paths.length`
                    (докстрока `context_card`, спека §2.8 п. 2). */}
                <p className="text-sm text-fg-tertiary">
                  Позиции лежат в {card.member_paths.length}{" "}
                  {card.member_paths.length === 1 ? "разделе" : "разных разделах"} смет
                </p>
                {(() => {
                  const labels = groupPathLabels(card.member_paths);
                  return card.member_paths.map((group, index) => (
                    <MembershipGroupSection
                      key={groupKeyOf(group.chapter_item_ids)}
                      contextId={contextId as number}
                      group={group}
                      label={labels[index]}
                      selectedIds={selectedIds}
                      isChecked={isGroupChecked(group)}
                      onToggleGroup={(checked) => toggleGroupSelected(group, checked)}
                      onTogglePosition={toggleSelected}
                      onAcceptStaleTransfer={(id) =>
                        acceptStaleTransfer.mutate(id, { onSuccess: () => pruneSelection([id]) })
                      }
                      acceptStaleTransferPending={acceptStaleTransfer.isPending}
                      onAcceptTargetDecision={(id) =>
                        acceptTargetDecision.mutate(
                          { position_item_ids: [id] },
                          { onSuccess: () => pruneSelection([id]) }
                        )
                      }
                      acceptTargetDecisionPending={acceptTargetDecision.isPending}
                      onOpenConflictMove={(row) => {
                        setConflictMove(row);
                        setConflictMoveTarget(null);
                      }}
                    />
                  ));
                })()}
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
                      { onSuccess: () => pruneSelection(selectedIdList) }
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
                      { onSuccess: () => pruneSelection(selectedIdList) }
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
          </div>
        </TabsContent>

        <TabsContent value="log" className="mt-4 min-w-0">
          <div className="min-w-0">
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
          </div>
        </TabsContent>
      </Tabs>
      </Surface>

      {/* Диалог «Подтвердить вид» (сверка с макетом 27.09.2026) — прежняя
          инлайн-форма (спека §2.5): вид работы и подтверждение/снятие
          подтверждения. */}
      <Dialog open={kindDialogOpen} onOpenChange={setKindDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Подтвердить вид</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="context-kind-select">Вид работы</Label>
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
          </div>
          <DialogFooter>
            {/* Обратный переход CONFIRMED -> SUGGESTED (спека §2.5) — кнопка
                видна ТОЛЬКО у подтверждённого вида, тем же маршрутом, что и
                подтверждение. */}
            {card.semantic_state === "CONFIRMED" && (
              <Button
                variant="outline"
                disabled={confirmKind.isPending}
                onClick={() =>
                  confirmKind.mutate(
                    { contextId, input: { unconfirm: true } },
                    { onSuccess: () => setKindDialogOpen(false) }
                  )
                }
              >
                Снять подтверждение
              </Button>
            )}
            <Button
              disabled={confirmKind.isPending}
              onClick={() =>
                confirmKind.mutate(
                  { contextId, input: { kind: kindChoice } },
                  { onSuccess: () => setKindDialogOpen(false) }
                )
              }
            >
              Подтвердить вид
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Диалог «Изменить «что называет»…» (сверка с макетом 27.09.2026) —
          прежняя инлайн-форма роли имени (спека §2.5). */}
      <Dialog open={roleDialogOpen} onOpenChange={setRoleDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Изменить «что называет»</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="context-role-select">Наименование называет</Label>
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
          </div>
          <DialogFooter>
            <Button
              disabled={setNameRole.isPending}
              onClick={() =>
                setNameRole.mutate(
                  { contextId, input: { role: roleChoice } },
                  { onSuccess: () => setRoleDialogOpen(false) }
                )
              }
            >
              Переопределить роль
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Диалог «Назначить семью…» (сверка с макетом 27.09.2026) — прежняя
          инлайн-форма семьи (спека §2.5): назначение и снятие рядом. */}
      <Dialog
        open={familyDialogOpen}
        onOpenChange={(open) => {
          setFamilyDialogOpen(open);
          if (!open) resetFamilyDialog();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{card.work_family_id === null ? "Назначить семью" : "Другая семья"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="context-family-select">Семья</Label>
            <EntitySelect
              id="context-family-select"
              items={activeFamilies.data}
              value={familyChoice}
              onChange={(v) => setFamilyChoice(v as number | null)}
              getLabel={(f) => f.title}
              placeholder="Выбрать семью"
            />
            {familyOutcome && (
              <p role="status" className="text-sm text-fg">
                {familyOutcome}
              </p>
            )}
            {familyRefusal && (
              <p role="alert" className="text-sm text-danger-text">
                {familyRefusal}
              </p>
            )}
          </div>
          <DialogFooter>
            {familyOutcome ? (
              <Button
                onClick={() => {
                  setFamilyDialogOpen(false);
                  resetFamilyDialog();
                }}
              >
                Закрыть
              </Button>
            ) : (
              <>
                <Button
                  variant="outline"
                  disabled={assignFamily.isPending || card.work_family_id === null}
                  onClick={() => changeFamily(null)}
                >
                  Снять семью
                </Button>
                <Button
                  disabled={assignFamily.isPending || familyChoice === null}
                  onClick={() => changeFamily(familyChoice)}
                >
                  Назначить семью
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={notWorkOpen} onOpenChange={setNotWorkOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Отметить контекст как не работу?</AlertDialogTitle>
            <AlertDialogDescription>
              Контекст перестанет считаться работой: семья, вариант и значения контекста будут сняты.
              Остальные контексты этого написания не меняются.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  disabled={markNotWork.isPending}
                  onClick={() => {
                    markNotWork.mutate(contextId);
                    setNotWorkOpen(false);
                  }}
                >
                  Отметить как не работу
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <MarkPositionDialog
        positionId={card.catalog_position_id}
        title={card.standard_job_title}
        open={markPositionOpen}
        onOpenChange={setMarkPositionOpen}
      />

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
                moveMembers.mutate(
                  {
                    position_item_ids: [conflictMove.position_item_id],
                    target_context_id: conflictMoveTarget,
                    // См. комментарий у «Перенести выбранные» — причина не
                    // вводится оператором, сервис принимает только "manual".
                    reason: "manual",
                  },
                  { onSuccess: () => pruneSelection([conflictMove.position_item_id]) }
                );
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
  /** Подпись пути, РАЗБИТАЯ на строки среди групп КАРТОЧКИ ({@link groupPathLabels}), не одной группы поодиночке. */
  label: GroupPathLabel;
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
  label,
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
  const selector = groupSelectorOf(group.chapter_item_ids);
  // Запрос страницы группы — только пока группа РАСКРЫТА (спека §2.5:
  // «раскрывается постраничным запросом группы»): `open ? contextId : null`
  // отключает `useQuery` (`enabled`) у свёрнутой группы, а не только прячет
  // уже загруженный результат.
  const pageQ = useContextGroupMembers(open ? contextId : null, selector, "all", page, MEMBER_PAGE_SIZE);
  // Зажим страницы (тот же приём, что `ContextsTab`/`FamiliesTab`, спека
  // §2.7): группа сузилась (разделение/перенос части её членств, принятие
  // предложения переноса по одной) при том же `page`, и текущий `offset`
  // больше не попадает в перечитанную выдачу — правка состояния ВО ВРЕМЯ
  // РЕНДЕРА, не в эффекте, следующий рендер запрашивает уже исправленный
  // `offset` (MAJOR-2, ревью Fable 27.09.2026).
  if (pageQ.data) {
    const lastValidPage = Math.max(1, Math.ceil(pageQ.data.total / MEMBER_PAGE_SIZE));
    if (page > lastValidPage) {
      setPage(lastValidPage);
    }
  }
  const pathLabel = groupPathLabel(group);
  // Винительный падеж — оба глагола («выбрать», «раскрыть») требуют его от
  // прямого дополнения («выбрать ЧТО» — группу, а не «группа»).
  const groupLabel = `группу «${pathLabel}»`;

  // Строка группы — КОМПАКТНАЯ (сверка со стендом 27.09.2026, четвёртый
  // круг): плашка «в смете» + путь В ДВЕ СТРОКИ, когда различающее звено
  // суффикса не помещается рядом с общим хвостом — `line1` (последние два
  // звена, общие у нескольких групп) и, только если суффикс длиннее двух
  // звеньев, `line2` (более старшие звенья суффикса — РАЗЛИЧАЮЩИЕ группы).
  // Обе строки — однострочная обрезка СЛЕВА браузером, не подбором лимита
  // символов: фиксированный лимит не сходится ни с какой пиксельной
  // шириной колонки. `dir="rtl"` разворачивает направление усечения
  // `truncate` (эллипсис ставится в НАЧАЛЕ, хвост всегда виден),
  // `<bdi dir="ltr">` восстанавливает порядок символов строки (кириллица и
  // `/` остаются слева направо) внутри развёрнутого контекста; полный путь
  // — в `title` каждой строки. Счётчик членств — ПРАВЫМ КРАЕМ строки 1 в
  // своей ячейке (`flex-none`, фиксированная ширина), устаревшие/
  // конфликтные — приглушённым мелким текстом РЯДОМ со счётчиком, тоже
  // справа. Ряд (чекбокс + вся подпись) выровнен ВЕРХОМ (`items-start`) —
  // чекбокс держится у ПЕРВОЙ строки, а не съезжает в середину, когда есть
  // вторая.
  const rtlLabel = (text: string, className: string) => (
    <span dir="rtl" className={className} title={pathLabel}>
      <bdi dir="ltr">{text}</bdi>
    </span>
  );

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="min-w-0 border-b border-border-subtle py-1.5">
      <div className="flex items-start gap-2">
        <Checkbox
          aria-label={`Выбрать ${groupLabel}`}
          checked={isChecked}
          onCheckedChange={(checked) => onToggleGroup(checked === true)}
        />
        <CollapsibleTrigger
          aria-label={`Раскрыть ${groupLabel}`}
          className="flex min-w-0 flex-1 flex-col items-stretch text-left text-sm"
        >
          <span className="flex min-w-0 items-center gap-3">
            <span className="flex min-w-0 flex-1 items-center gap-2">
              {group.chapter_item_ids.length === 0 ? (
                <span className="truncate">{label.line1}</span>
              ) : (
                <>
                  <SourceChip kind="estimate" />
                  {rtlLabel(label.line1, "block min-w-0 flex-1 truncate text-left")}
                </>
              )}
            </span>
            <span className="flex-none whitespace-nowrap text-xs text-fg-tertiary">
              {group.stale_count > 0 && <>устаревшее: {group.stale_count}</>}
              {group.stale_count > 0 && group.conflict_count > 0 && " · "}
              {group.conflict_count > 0 && <>конфликт: {group.conflict_count}</>}
            </span>
            <span className="w-8 flex-none text-right tabular-nums text-fg-tertiary">
              {group.member_count}
            </span>
          </span>
          {label.line2 !== null &&
            rtlLabel(label.line2, "mt-0.5 block min-w-0 truncate pl-5 text-left text-xs text-fg-tertiary")}
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="mt-2">
        {pageQ.isPending && <Skeleton className="h-16 w-full" />}
        {/*
          Внешнее ревью PR #54: неуспех `GET .../members` оставлял раскрытую
          группу пустой — `pageQ.data` не приходит никогда, а скелет гаснет
          сразу после ответа отказом. Текст отказа и «Повторить»
          (`pageQ.refetch()`) — тот же приём, что несёт `PositionDrilldown`/
          `UnallocatedSheet` для своих запросов.
        */}
        {pageQ.isError && (
          <div className="flex items-center gap-2 text-sm text-fg-secondary">
            <span>Не удалось загрузить позиции группы</span>
            <Button variant="outline" size="sm" onClick={() => pageQ.refetch()}>
              Повторить
            </Button>
          </div>
        )}
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
