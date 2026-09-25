import { useState } from "react";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
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
import {
  useAcceptStaleTransfer,
  useAcceptTargetDecision,
  useArchiveContext,
  useAssignFamily,
  useConfirmKind,
  useContextCard,
  useMergeContexts,
  useMoveMembers,
  useSetNameRole,
  useSplitContext,
  useWorkFamilies,
} from "@/services/queries";
import type { BucketContextOption, ContextMemberRow, NameRole, SemanticKind } from "@/types/domain";

const KIND_OPTIONS: SemanticKind[] = ["WORK", "SYSTEM", "UNKNOWN"];
const ROLE_OPTIONS: NameRole[] = ["WORK", "LOCATION_ONLY", "GENERIC_WORK"];
const RULE_KIND_OPTIONS = [
  "nearest_chapter_equals",
  "chapter_chain_contains",
  "chapter_level_equals",
] as const;
const NO_RULE = "no-rule";
const WITH_RULE = "with-rule";

function bucketTargetLabel(option: BucketContextOption): string {
  return option.is_default ? `контекст #${option.id} (по умолчанию)` : `контекст #${option.id}`;
}

interface ContextCardProps {
  contextId: number | null;
}

/**
 * Карточка контекста и его операции (спека §2.10). Карточка несёт членства
 * ПОШТУЧНО (`ContextCardData.members`, `backend/crud/semantic.py::
 * context_card`) — без списка утверждение «действие переноса не
 * предлагается конфликтному членству» было недоказуемо, а разделение/перенос
 * неисполнимы вслепую. Действие по конкретному членству
 * читается из ЕГО СОБСТВЕННЫХ полей (`membership_state`, `conflict_at`), а
 * не вводится оператором на глаз.
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

  const [kindChoice, setKindChoice] = useState<SemanticKind>("WORK");
  const [roleChoice, setRoleChoice] = useState<NameRole>("WORK");
  // Ключ «текущей» карточки, для которой синхронизированы `kindChoice`/
  // `roleChoice` ниже — см. докстринг у их синхронизации (П1).
  const [syncedCardKey, setSyncedCardKey] = useState<string | null>(null);
  const [familyChoice, setFamilyChoice] = useState<number | null>(null);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const [splitRuleMode, setSplitRuleMode] = useState<string>(NO_RULE);
  const [ruleKind, setRuleKind] = useState<string>(RULE_KIND_OPTIONS[0]);
  const [ruleValue, setRuleValue] = useState("");
  const [ruleLevel, setRuleLevel] = useState("");

  const [bulkTarget, setBulkTarget] = useState<number | null>(null);

  const [conflictMove, setConflictMove] = useState<ContextMemberRow | null>(null);
  const [conflictMoveTarget, setConflictMoveTarget] = useState<number | null>(null);

  const [mergeTarget, setMergeTarget] = useState<number | null>(null);
  const [newDefaultInput, setNewDefaultInput] = useState("");
  const [archiveOpen, setArchiveOpen] = useState(false);

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

  const kindSourceLabel = card.semantic_kind_source === "manual" ? "подтверждён вручную" : "по правилу";
  const roleSourceLabel = card.name_role_source === "manual" ? "переопределена вручную" : "по словарю";
  const categorySourceLabel =
    card.work_category_source === "manual" ? "разнесена вручную" : card.work_category_source === "file" ? "из файла" : "—";

  let familyCaption: string;
  let familySourceCaption: string | null = null;
  if (card.work_family_id !== null) {
    familyCaption = card.family_title ?? `Семья #${card.work_family_id}`;
    // Источник назначения семьи (спека §2.10: «семью с источником
    // назначения») — семья и её источник разные факты, подпись у обоих.
    familySourceCaption = card.family_source === "manual" ? "назначена вручную" : "по предложению";
  } else if (card.comparability_reason === "insufficient_description") {
    familyCaption = "семья не назначена, потому что состав не описан";
  } else {
    familyCaption = "нет семьи";
  }

  const comparabilityCaption =
    card.comparability_reason === "insufficient_description"
      ? "состав не описан — сравнение ставок не производится"
      : "";

  function toggleSelected(id: number, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const selectedIdList = Array.from(selectedIds);

  // Живые соседи по корзине, кроме текущего контекста — цель слияния/переноса
  // выбирается из НИХ (решение оркестратора П6, план задачи 13), а не
  // вводится id вручную: архивный сосед в выбор не попадает — переносить/
  // сливать в архивный контекст нечего.
  const liveBucketContexts = card.bucket_contexts.filter(
    (c) => c.archived_at === null && c.id !== contextId
  );

  return (
    <div className="grid gap-4">
      <Surface className="grid gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-medium text-fg">
              {card.standard_job_title}
              {card.unit_code && <span className="ml-2 text-sm text-fg-tertiary">{card.unit_code}</span>}
            </h3>
            <p className="mt-1 text-sm text-fg-secondary">
              Статья: {card.work_category_title ?? "—"} {card.work_category_code && `(${card.work_category_code})`}{" "}
              <span className="text-fg-tertiary">· {categorySourceLabel}</span>
            </p>
          </div>
          <Badge variant={card.archived_at ? "outline" : "secondary"}>
            {card.archived_at ? "архивный" : card.semantic_state}
          </Badge>
        </div>

        <div className="grid gap-1 text-sm">
          <div>
            Вид: <Badge variant="outline">{card.semantic_kind}</Badge>{" "}
            <span className="text-fg-tertiary">{kindSourceLabel}</span>
          </div>
          <div>
            Роль имени: <Badge variant="outline">{card.name_role}</Badge>{" "}
            <span className="text-fg-tertiary">
              {roleSourceLabel}, словарь v{card.place_dictionary_version}
            </span>
          </div>
          <div>
            Сравнимость:{" "}
            {comparabilityCaption ? (
              <span>{comparabilityCaption}</span>
            ) : (
              <span className="text-fg-tertiary">—</span>
            )}
          </div>
          <div>
            Семья: <span>{familyCaption}</span>
            {familySourceCaption && (
              <span className="text-fg-tertiary"> · {familySourceCaption}</span>
            )}
          </div>
          <div>Членств: {card.member_count}</div>
        </div>

        <div>
          <h4 className="text-sm font-medium text-fg">Журнал</h4>
          {card.events.length === 0 ? (
            <p className="text-sm text-fg-tertiary">Событий нет.</p>
          ) : (
            <ul className="mt-1 grid gap-1 text-sm text-fg-secondary">
              {card.events.map((event) => (
                <li key={event.id}>
                  {event.event_type} · {event.created_at}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Surface>

      <Surface className="grid gap-4">
        <h4 className="text-sm font-medium text-fg">Членства</h4>

        {card.members.length === 0 ? (
          <p className="text-sm text-fg-tertiary">Членств нет.</p>
        ) : (
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
                  {card.members.map((member) => {
                    const isStale = member.membership_state === "STALE";
                    const isConflicted = member.conflict_at !== null;
                    return (
                      <TableRow key={member.position_item_id}>
                        <TableCell>
                          <Checkbox
                            aria-label={`Выбрать позицию ${member.position_item_id}`}
                            checked={selectedIds.has(member.position_item_id)}
                            onCheckedChange={(checked) =>
                              toggleSelected(member.position_item_id, checked === true)
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
                            {member.membership_state}
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
                              конфликта: принятие переноса не трогает поля
                              конфликта, и уже промаршрутизированная строка
                              осталась бы висеть со старым конфликтом.
                            */}
                            {isStale && !isConflicted && (
                              <Button
                                size="xs"
                                variant="outline"
                                aria-label={`Принять предложение переноса для позиции ${member.position_item_id}`}
                                disabled={acceptStaleTransfer.isPending}
                                onClick={() =>
                                  acceptStaleTransfer.mutate(member.position_item_id)
                                }
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
                                  disabled={acceptTargetDecision.isPending}
                                  onClick={() =>
                                    acceptTargetDecision.mutate({
                                      position_item_ids: [member.position_item_id],
                                    })
                                  }
                                >
                                  Принять решение цели
                                </Button>
                                <Button
                                  size="xs"
                                  variant="outline"
                                  aria-label={`Перенести в другой контекст позицию ${member.position_item_id}`}
                                  onClick={() => {
                                    setConflictMove(member);
                                    setConflictMoveTarget(null);
                                  }}
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
            {card.members_truncated && (
              <p className="text-xs text-fg-tertiary">
                Показаны первые {card.members.length} из {card.member_count} членств — список обрезан.
              </p>
            )}
          </>
        )}

        {/* Массовые действия над ВЫБРАННЫМИ (чекбоксы) членствами — разделить/перенести. */}
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
                    <SelectValue>{() => ruleKind}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {RULE_KIND_OPTIONS.map((k) => (
                      <SelectItem key={k} value={k}>{k}</SelectItem>
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
      </Surface>

      <Surface className="grid gap-4">
        <h4 className="text-sm font-medium text-fg">Операции</h4>

        {/* Вид работы */}
        <div className="grid gap-2">
          <Label htmlFor="context-kind-select">Вид работы</Label>
          <div className="flex flex-wrap gap-2">
            <Select value={kindChoice} onValueChange={(v) => v && setKindChoice(v as SemanticKind)}>
              <SelectTrigger id="context-kind-select" className="w-48">
                <SelectValue>{() => kindChoice}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {KIND_OPTIONS.map((kind) => (
                  <SelectItem key={kind} value={kind}>
                    {kind}
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
                <SelectValue>{() => roleChoice}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((role) => (
                  <SelectItem key={role} value={role}>
                    {role}
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

        {/* Слить контекст — цель из живых соседей по корзине (П6), не голый id. */}
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

        {/* Архивировать контекст — восстановления архивного контекста на экране нет (спека §2.8, §2.10) */}
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
