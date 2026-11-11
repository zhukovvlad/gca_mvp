import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

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
import { Button, buttonVariants } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatDateTime, pluralRu } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  apiErrorCode,
  apiErrorContext,
  useFamilySchema,
  useJournalItem,
  useJournalRestore,
  useJournalResplit,
  useJournalUndo,
} from "@/services/queries";
import type {
  JournalContextDetail,
  JournalItemDetail,
  JournalLockedBy,
  JournalSplitPart,
} from "@/types/domain";

import { MergeValuesDialog } from "./MergeValuesDialog";
import {
  JOURNAL_KIND_LABEL,
  JOURNAL_KIND_TINT,
  JOURNAL_RESPLIT_LEVELS,
  journalLockedLabel,
  journalRefusalLabel,
} from "./labels";
import { ListSkeleton } from "./pending";

/** Текст «5 контекстов» с верным окончанием. */
function contextsCount(n: number): string {
  return `${n} контекст${pluralRu(n)}`;
}

/** Глагол по числу: «остаётся» при 1, 21, 31…, иначе «остаются». */
function agree(n: number, one: string, many: string): string {
  return n % 10 === 1 && n % 100 !== 11 ? one : many;
}

function splitParts(item: JournalItemDetail): { level: number | null; parts: JournalSplitPart[] } | null {
  return item.details !== undefined && "parts" in item.details ? item.details : null;
}

function contextDetails(item: JournalItemDetail): { contexts: JournalContextDetail[]; undo_text: string } | null {
  return item.details !== undefined && "contexts" in item.details ? item.details : null;
}

/**
 * Знак между частями составного. Вид составного записи не несёт, но текст значения собран по его
 * виду («A + B» или «A или B»), а его показывают контексты записи; нет ни того, ни другого —
 * нейтральная точка.
 */
function partsSeparator(contexts: JournalContextDetail[]): string {
  const values = contexts.map((context) => context.parameter_value ?? "");
  if (values.length > 0 && values.every((value) => value.includes(" или "))) return "или";
  if (values.length > 0 && values.every((value) => value.includes(" + "))) return "+";
  return "·";
}

function normText(text: string): string {
  return text.trim().toLowerCase();
}

function levelLabel(level: number, current: number | null): string {
  return `${level + 1}-й уровень от корня${level === current ? " (сейчас)" : ""}`;
}

/** Карточка записи «Журнала ИИ» — панель справа от ленты (спека упрощения каталога §2.7). */
export function JournalCard({ id }: { id: number | null }) {
  const itemQ = useJournalItem(id);

  return (
    <section
      aria-label="Запись журнала"
      aria-live="polite"
      className="grid gap-3.5 rounded-xl border border-border bg-surface p-4"
    >
      {id === null && <p className="text-sm text-fg-tertiary">Выберите запись слева.</p>}
      {id !== null && itemQ.isPending && <ListSkeleton rows={3} />}
      {id !== null && itemQ.isError && (
        <p className="text-sm text-fg-tertiary">
          {apiErrorCode(itemQ.error) === "journal_not_found"
            ? journalRefusalLabel("journal_not_found")
            : "Не удалось получить запись."}
        </p>
      )}
      {itemQ.data && <CardBody key={itemQ.data.id} item={itemQ.data} />}
    </section>
  );
}

function CardBody({ item }: { item: JournalItemDetail }) {
  const undo = useJournalUndo();
  const resplit = useJournalResplit();
  const restore = useJournalRestore();
  const split = splitParts(item);
  const withContexts = contextDetails(item);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [resplitting, setResplitting] = useState(false);
  const [merging, setMerging] = useState(false);
  const [level, setLevel] = useState<number>(split?.level ?? 0);

  const undone = item.state === "undone";
  const locked = item.state === "locked";
  const target = item.summary_values[1] ?? "";
  const contexts = withContexts?.contexts ?? [];

  function showRefusal(error: unknown) {
    const code = apiErrorCode(error);
    if (code === "journal_undo_locked") {
      const lockedBy = apiErrorContext<{ locked_by: JournalLockedBy | null }>(error)?.locked_by ?? null;
      setRefusal(journalLockedLabel(lockedBy, lockedBy?.at ? formatDateTime(lockedBy.at) : null));
      return;
    }
    setRefusal(journalRefusalLabel(code));
  }

  function runUndo() {
    setConfirming(false);
    setRefusal(null);
    undo.mutate(item.id, { onError: showRefusal });
  }

  function runResplit() {
    setRefusal(null);
    resplit.mutate(
      { id: item.id, level },
      { onSuccess: () => setResplitting(false), onError: showRefusal }
    );
  }

  function runRestore() {
    setRefusal(null);
    restore.mutate(item.id, { onError: showRefusal });
  }

  const heading =
    item.kind === "split"
      ? `Контекст ${item.context ? `«${item.context.title}» ` : ""}разделён`
      : item.kind === "composite"
        ? `«${item.text ?? ""}» стало составным значением из ${item.summary_values.length} частей`
        : item.kind === "value_added"
          ? `Добавлено значение «${item.text ?? ""}»`
          : `«${item.text ?? ""}» приравнено к «${target}»`;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-tertiary">
        <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold", JOURNAL_KIND_TINT[item.kind])}>
          {JOURNAL_KIND_LABEL[item.kind]}
        </span>
        <span>запись {item.id}</span>
        <span>·</span>
        <span className="tabular-nums">{formatDateTime(item.created_at)}</span>
        <span>·</span>
        <span>система</span>
      </div>

      <h3 className="font-serif text-xl leading-tight font-semibold text-fg">{heading}</h3>

      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-fg-tertiary">{item.parameter ? "Семья · параметр" : "Семья"}</dt>
        <dd>
          {item.family.title}
          {item.parameter ? ` · «${item.parameter.name}»` : ""}
        </dd>
        {item.kind === "value_added" && item.context && (
          <>
            <dt className="text-fg-tertiary">Откуда</dt>
            <dd>контекст «{item.context.title}»</dd>
          </>
        )}
      </dl>

      {item.kind === "split" && split && <SplitParts parts={split.parts} undone={undone} />}
      {item.kind === "composite" && <CompositeBlock item={item} contexts={contexts} undoText={withContexts?.undo_text ?? item.text ?? ""} />}
      {item.kind === "equated" && (
        <ContextsBlock item={item} contexts={contexts} undoText={withContexts?.undo_text ?? item.text ?? ""} />
      )}

      {locked && (
        <p className="rounded-lg border-l-[3px] border-danger bg-surface-sunken px-3 py-2.5 text-[12.5px] text-fg-secondary">
          {journalLockedLabel(
            item.undo.locked_by,
            item.undo.locked_by?.at ? formatDateTime(item.undo.locked_by.at) : null
          )}
        </p>
      )}

      {undone && (
        <p className="rounded-lg border-l-[3px] border-danger bg-surface-sunken px-3 py-2.5 text-[12.5px] text-fg-secondary">
          {`Отменено · ${item.undone?.by_email ?? "администратор"} · ${formatDateTime(item.undone?.at)}. ${undoneNote(item, target)}`}
        </p>
      )}

      {item.restored_at !== null && (
        <p className="rounded-lg border-l-[3px] border-accent-border bg-surface-sunken px-3 py-2.5 text-[12.5px] text-fg-secondary">
          {`Автоматика возвращена · ${formatDateTime(item.restored_at)}.`}
        </p>
      )}

      {resplitting && split && (
        <div className="grid gap-2.5 rounded-lg border border-dashed border-border-strong p-3">
          <p className="text-sm text-fg-secondary">
            Деление будет отменено, а новое построено по разделам выбранного уровня. Новое придёт отдельной записью журнала.
          </p>
          <div className="grid gap-1">
            <Label htmlFor="journal-resplit-level" className="text-xs text-fg-tertiary">
              Уровень разделов
            </Label>
            <Select value={String(level)} onValueChange={(next) => setLevel(Number(next))}>
              <SelectTrigger id="journal-resplit-level" className="w-64">
                <SelectValue>{(raw) => levelLabel(Number(raw), split.level)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {JOURNAL_RESPLIT_LEVELS.map((option) => (
                  <SelectItem key={option} value={String(option)}>
                    {levelLabel(option, split.level)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button loading={resplit.isPending} onClick={runResplit}>
              Отменить и разделить заново
            </Button>
            <Button variant="outline" onClick={() => setResplitting(false)}>
              Не менять
            </Button>
          </div>
        </div>
      )}

      {refusal !== null && (
        <p role="alert" className="text-sm text-danger-text">
          {refusal}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
        {!undone && item.kind === "split" && (
          <>
            <Button
              variant="outline"
              className="border-danger text-danger-text"
              disabled={!item.undo.available}
              loading={undo.isPending}
              onClick={() => setConfirming(true)}
            >
              Отменить — вернуть как было
            </Button>
            <Button variant="outline" disabled={!item.undo.available} onClick={() => setResplitting(true)}>
              Поправить
            </Button>
          </>
        )}
        {!undone && item.kind === "composite" && (
          <Button
            variant="outline"
            className="border-danger text-danger-text"
            disabled={!item.undo.available}
            loading={undo.isPending}
            onClick={() => setConfirming(true)}
          >
            Отменить — снова простое значение
          </Button>
        )}
        {!undone && item.kind === "equated" && (
          <Button
            variant="outline"
            className="border-danger text-danger-text"
            disabled={!item.undo.available}
            loading={undo.isPending}
            onClick={() => setConfirming(true)}
          >
            Отвязать — завести отдельным значением
          </Button>
        )}
        {item.kind === "value_added" && <MergeAction item={item} open={merging} onOpenChange={setMerging} />}
        {undone && item.restored_at === null && item.kind !== "value_added" && (
          <Button variant="outline" loading={restore.isPending} onClick={runRestore}>
            Вернуть автоматику
          </Button>
        )}
        {item.kind === "split" && item.context && (
          <Link to={`/families?context=${item.context.id}`} className={buttonVariants({ variant: "link" })}>
            Открыть контекст →
          </Link>
        )}
      </div>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmTitle(item)}</AlertDialogTitle>
            <AlertDialogDescription render={<div />}>
              <ConfirmBody item={item} contexts={contexts} target={target} parts={split?.parts ?? []} />
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Не отменять</Button>} />
            <AlertDialogAction
              render={
                <Button variant="destructive" onClick={runUndo}>
                  {confirmAction(item)}
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function undoneNote(item: JournalItemDetail, target: string): string {
  if (item.kind === "split") {
    return "Контекст снова один, помечен «к делению», аналогов у строк нет. Автоматически система его больше не делит — разделить можно вручную на вкладке «Контексты».";
  }
  if (item.kind === "composite") {
    return "Значение снова простое, без частей; контексты его не меняли. Решение человека: фильтр склеек это значение больше не раскладывает.";
  }
  return `«${item.text ?? ""}» заведено отдельным значением, контексты переведены на него. Пара помечена «не дубль»: фильтр больше не приравняет этот текст к «${target}».`;
}

function confirmTitle(item: JournalItemDetail): string {
  if (item.kind === "split") return "Вернуть как было?";
  if (item.kind === "composite") return "Снова простое значение?";
  return "Отвязать значение?";
}

function confirmAction(item: JournalItemDetail): string {
  if (item.kind === "split") return "Вернуть как было";
  if (item.kind === "composite") return "Снова простое значение";
  return "Отвязать";
}

/** Подтверждение отмены с нынешними числами записи. */
function ConfirmBody({
  item,
  contexts,
  target,
  parts,
}: {
  item: JournalItemDetail;
  contexts: JournalContextDetail[];
  target: string;
  parts: JournalSplitPart[];
}) {
  if (item.kind === "split") {
    const total = parts.reduce((sum, part) => sum + part.members_after, 0);
    return (
      <ul className="grid list-disc gap-1 pl-5 text-left">
        <li>
          {`Сейчас в частях: ${parts.map((part) => `«${part.title}» — ${part.members_now} строк`).join(", ")}. Все ${total} строк вернутся в один контекст «${item.context?.title ?? ""}».`}
        </li>
        <li>Снимутся правила маршрутизации, заведённые этим делением.</li>
        <li>Контекст станет «к делению», у его строк не будет аналога.</li>
        <li>Автоматически система его больше делить не будет — только вручную на вкладке «Контексты».</li>
      </ul>
    );
  }
  const n = contexts.length;
  if (item.kind === "composite") {
    return (
      <ul className="grid list-disc gap-1 pl-5 text-left">
        <li>
          {`«${item.text ?? ""}» потеряет ссылки на части и станет обычным значением списка; ${contextsCount(n)} ${agree(n, "остаётся", "остаются")} на нём.`}
        </li>
        <li>Блок «по частям» у этих строк пропадёт.</li>
        <li>Заведённые из склейки значения останутся в списке — слить их можно из их записей.</li>
      </ul>
    );
  }
  return (
    <ul className="grid list-disc gap-1 pl-5 text-left">
      <li>
        {`«${item.text ?? ""}» будет заведено отдельным значением параметра «${item.parameter?.name ?? ""}».`}
      </li>
      <li>{`${contextsCount(n)} ${agree(n, "перейдёт", "перейдут")} с «${target}» на него.`}</li>
    </ul>
  );
}

/** Части деления с нынешним числом строк; у отменённого деления числа уже не текущие и не показываются. */
function SplitParts({ parts, undone }: { parts: JournalSplitPart[]; undone: boolean }) {
  return (
    <ul className="grid gap-2">
      {parts.map((part) => (
        <li
          key={part.context_id}
          className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-surface-sunken px-3 py-2 text-sm"
        >
          <span className="font-medium text-fg">{part.title}</span>
          {!undone && <span className="text-fg-tertiary tabular-nums">{part.members_now} строк</span>}
        </li>
      ))}
    </ul>
  );
}

/** «Было → стало» склейки и контексты записи с параметром на них. */
function CompositeBlock({
  item,
  contexts,
  undoText,
}: {
  item: JournalItemDetail;
  contexts: JournalContextDetail[];
  undoText: string;
}) {
  const separator = partsSeparator(contexts);
  return (
    <>
      <div className="grid gap-2 rounded-lg bg-surface-sunken p-3">
        <div className="grid gap-1.5">
          <span className="text-[10.5px] tracking-wide text-fg-tertiary uppercase">Было — простое значение, склейка в тексте</span>
          <span data-testid="journal-was" className="w-fit text-sm text-fg-secondary line-through">
            {undoText}
          </span>
        </div>
        <span aria-hidden="true" className="pl-3 text-xl leading-none text-fg-secondary">
          ↓
        </span>
        <div className="grid gap-1.5">
          <span className="text-[10.5px] tracking-wide text-fg-tertiary uppercase">
            Стало — то же значение, составное: части — ссылки на значения списка
          </span>
          <div data-testid="journal-now" className="flex flex-wrap items-center gap-1.5">
            {item.summary_values.map((part, index) => (
              <span key={`${part}-${index}`} className="contents">
                {index > 0 && <span className="text-xs text-fg-tertiary">{separator}</span>}
                <span className="rounded-md border border-border bg-surface px-2.5 py-1 text-[13.5px] font-medium">{part}</span>
              </span>
            ))}
          </div>
        </div>
      </div>
      <ContextsBlock item={item} contexts={contexts} undoText={undoText} />
    </>
  );
}

/** Контексты записи: значение параметра на каждом сейчас; у склейки — и каким оно было текстом. */
function ContextsBlock({
  item,
  contexts,
  undoText,
}: {
  item: JournalItemDetail;
  contexts: JournalContextDetail[];
  undoText: string;
}) {
  if (contexts.length === 0) return null;
  return (
    <div className="grid gap-1 border-t border-border-subtle pt-3">
      <span className="text-[10.5px] tracking-wide text-fg-tertiary uppercase">
        {`Контексты · ${contexts.length}`}
      </span>
      {contexts.map((context) => (
        <div key={context.id} data-testid="journal-context" className="grid gap-0.5 border-b border-dashed border-border-subtle py-2">
          <span className="text-[13px] font-medium">{context.title}</span>
          <span className="text-[12.5px] text-fg-secondary">
            {`${item.parameter?.name ?? "Значение"}: `}
            <b>{context.parameter_value ?? "—"}</b>
            {item.kind === "composite" && item.state !== "undone" && (
              <span className="text-fg-tertiary">{` — одно составное значение; было текстом «${undoText}»`}</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

/** «Слить с…» у нового значения: окно слияния с этим значением источником. */
function MergeAction({
  item,
  open,
  onOpenChange,
}: {
  item: JournalItemDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const schemaQ = useFamilySchema(item.family.id);
  const schema = schemaQ.data;
  const parameter = schema?.parameters.find((candidate) => candidate.id === item.parameter?.id);
  const value = parameter?.values.find((candidate) => normText(candidate.value) === normText(item.text ?? ""));
  const mergedInto = value && value.merged_into_id !== null ? parameter?.values.find((v) => v.id === value.merged_into_id) : undefined;

  let reason: ReactNode = null;
  if (schema !== undefined && (parameter === undefined || value === undefined)) {
    reason = "Слить нельзя: в списке параметра его больше нет.";
  } else if (value !== undefined && value.merged_into_id !== null) {
    reason = `Значение уже слито${mergedInto ? ` со «${mergedInto.value}»` : ""}.`;
  }
  const canMerge = schema !== undefined && parameter !== undefined && value !== undefined && value.merged_into_id === null;

  return (
    <>
      <Button variant="outline" className="border-primary font-semibold" disabled={!canMerge} onClick={() => onOpenChange(true)}>
        Слить с другим значением…
      </Button>
      {reason !== null && <span className="text-xs text-fg-tertiary">{reason}</span>}
      {canMerge && schema && parameter && value && (
        <MergeValuesDialog
          schema={schema}
          open={open}
          onOpenChange={onOpenChange}
          initial={{ parameterId: parameter.id, sourceId: value.id }}
        />
      )}
    </>
  );
}
