import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { formatDate } from "@/lib/format";
import { useClearCategoryOverride, useSetCategoryOverride } from "@/services/queries";
import type {
  ProjectPassport,
  ProjectPassportCategoryOption,
  ProjectPassportManualAssignment,
  ProjectPassportUnallocatedSection,
} from "@/types/domain";

/**
 * Панель-верстак разноса — разворачивается под строкой «Нераспределённое»
 * таблицы по статьям (спека разноса §2.6; план, задача 8).
 *
 * Монтирует и разворачивает панель `CategoryTable` (её тесты — в
 * `ProjectPassportPage.test.tsx`, там же и разводка `contractId`/`estimateId`);
 * этот компонент — только содержимое панели, всегда «развёрнутое», как только
 * смонтировано.
 *
 * Два блока с ОДНОЙ и ТОЙ же по имени, но РАЗНОЙ по смыслу колонкой денег
 * (замечание ревью задачи 4, актуальное и здесь):
 *
 * - «Разделы без статьи» — `unallocated.sections[]`. `subtree_amount` там
 *   сворачивает только НЕРАСПРЕДЕЛЁННУЮ часть поддерева: резолвер приоритета
 *   LOCAL, и потомок со своей файловой статьёй ничего не наследует от
 *   вручную решённого предка.
 * - «Разнесено вручную» — `manual_assignments[]`. `subtree_amount` там —
 *   ПОЛНАЯ файловая свёртка раздела, без обрезки по внутренним решениям.
 *
 * Числа из разных блоков НЕ складываются и не сравнимы «по вершине» — оба
 * блока подписывают свою колонку явно, чтобы это не подразумевалось само собой.
 */
export function UnallocatedPanel({
  passport,
  contractId,
  estimateId,
}: {
  passport: ProjectPassport;
  contractId: number;
  estimateId: number;
}) {
  const roots = buildUnallocatedTree(passport.unallocated.sections);
  const setOverride = useSetCategoryOverride();
  const clearOverride = useClearCategoryOverride();
  const disabled = setOverride.isPending || clearOverride.isPending;

  function pickCategory(positionItemId: number, option: ProjectPassportCategoryOption) {
    setOverride.mutate({
      contractId,
      estimateId,
      positionItemId,
      workCategoryId: option.id,
    });
  }

  function clearAssignment(positionItemId: number) {
    clearOverride.mutate({ contractId, estimateId, positionItemId });
  }

  return (
    // Служебная зона разноса, не часть документа — на бумаге разносить нечем
    // (те же причины, что у ExpandToggle/переключателя нулевых в CategoryTable).
    <section data-testid="unallocated-panel" data-print="hide" className="bg-surface">
      <div className="border-b border-border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-fg">Разделы без статьи</h3>
        <p className="text-2xs text-fg-tertiary">
          Сумма — только нераспределённая часть поддерева; строки со своей файловой
          статьёй в неё не входят.
        </p>
      </div>
      <div className="px-2 py-1">
        {roots.length === 0 ? (
          <p className="px-2 py-3 text-xs text-fg-tertiary">Разделов без статьи не осталось.</p>
        ) : (
          roots.map((node) => (
            <SectionRow
              key={node.position_item_id}
              node={node}
              depth={0}
              categoryOptions={passport.category_options}
              onPick={pickCategory}
              disabled={disabled}
            />
          ))
        )}
      </div>

      <div className="border-t border-b border-border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-fg">Разнесено вручную</h3>
        <p className="text-2xs text-fg-tertiary">
          Сумма — полная файловая свёртка раздела, без разбора вложенных решений.
        </p>
      </div>
      <div className="px-2 py-1">
        {passport.manual_assignments.length === 0 ? (
          <p className="px-2 py-3 text-xs text-fg-tertiary">Ручных решений пока нет.</p>
        ) : (
          passport.manual_assignments.map((assignment) => (
            <ManualAssignmentRow
              key={assignment.position_item_id}
              assignment={assignment}
              onClear={() => clearAssignment(assignment.position_item_id)}
              disabled={disabled}
            />
          ))
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
//  Дерево нераспределённого
// ---------------------------------------------------------------------------

interface UnallocatedTreeNode extends ProjectPassportUnallocatedSection {
  children: UnallocatedTreeNode[];
}

/** Десятичное число в виде строки: `-?цифры[.цифры]` — тот же разбор, что у
 *  `src/lib/decimal.ts`; отдельная копия, а не импорт: там нет функции
 *  сравнения, а заводить её ради одного места использования — лишнее. */
const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Сравнивает две decimal-строки точно, целыми числами (`BigInt`), без
 * перевода в `number` — тот же приём, что у `addDecimalStrings`
 * (AGENTS.md §3: деньги — `Decimal`-строки, `Number()` теряет разряды).
 *
 * Неразбираемый вход считается равным — вызывающий (`compareBySubtreeDesc`)
 * получает данные с сервера, где `subtree_amount` либо `Decimal`, либо `null`,
 * и `null` эта функция никогда не видит: его отсекает вызывающий раньше.
 */
function compareDecimalStrings(a: string, b: string): number {
  const pa = DECIMAL_RE.exec(a.trim());
  const pb = DECIMAL_RE.exec(b.trim());
  if (!pa || !pb) return 0;

  const scale = Math.max(pa[3]?.length ?? 0, pb[3]?.length ?? 0);
  const toBigInt = (m: RegExpExecArray) => {
    const digits = BigInt(`${m[2]}${(m[3] ?? "").padEnd(scale, "0")}`);
    return m[1] === "-" ? -digits : digits;
  };

  const diff = toBigInt(pa) - toBigInt(pb);
  return diff < 0n ? -1 : diff > 0n ? 1 : 0;
}

/** По убыванию `subtree_amount`; `null` — в конец; ничья — по возрастанию
 *  `position_item_id`, чтобы порядок был детерминирован (controller-notes). */
function compareBySubtreeDesc(a: UnallocatedTreeNode, b: UnallocatedTreeNode): number {
  if (a.subtree_amount === null && b.subtree_amount === null) {
    return a.position_item_id - b.position_item_id;
  }
  if (a.subtree_amount === null) return 1;
  if (b.subtree_amount === null) return -1;

  const cmp = compareDecimalStrings(a.subtree_amount, b.subtree_amount);
  return cmp !== 0 ? -cmp : a.position_item_id - b.position_item_id;
}

/**
 * Плоский список `unallocated.sections[]` → дерево по `parent_position_item_id`.
 *
 * Сервер отдаёт список по глубине обхода (level-first) — раздельные ветки
 * дерева перемежаются в массиве, и рисовать его В ПОРЯДКЕ ПРИХОДА нарисовало
 * бы неверное дерево (controller-notes). Узел с `parent_position_item_id: null`
 * — вершина нераспределённой части, НЕ обязательно корень файловой структуры:
 * бэкенд перевешивает родителей внутрь нераспределённой выборки.
 */
function buildUnallocatedTree(
  sections: ProjectPassportUnallocatedSection[]
): UnallocatedTreeNode[] {
  const byId = new Map<number, UnallocatedTreeNode>();
  for (const section of sections) {
    byId.set(section.position_item_id, { ...section, children: [] });
  }

  const roots: UnallocatedTreeNode[] = [];
  for (const section of sections) {
    const node = byId.get(section.position_item_id);
    if (!node) continue;
    if (section.parent_position_item_id === null) {
      roots.push(node);
      continue;
    }
    const parent = byId.get(section.parent_position_item_id);
    if (parent) {
      parent.children.push(node);
    } else {
      // Родителя нет среди sections[] — по правилам бэкенда такого не
      // бывает (родители перевешиваются внутрь нераспределённой выборки,
      // controller-notes), но проглатывать узел молча нельзя (finding I-4):
      // без него раздел и его деньги пропали бы с единственного экрана, где
      // их можно разнести, — без диагностики и без несовпадения счётчика.
      // Деградация в плоский верхний уровень честнее, чем потеря узла.
      roots.push(node);
    }
  }

  function sortRec(nodes: UnallocatedTreeNode[]) {
    nodes.sort(compareBySubtreeDesc);
    for (const node of nodes) sortRec(node.children);
  }
  sortRec(roots);

  return roots;
}

/** «5.1 Раздел «…»» либо просто заголовок, если номера нет — общая подпись
 *  раздела для aria-label кнопок панели (finding I-3: подпись обязана нести
 *  раздел, а не быть одной и той же на N строк). */
function chapterLabel(section: { number: string | null; title: string }): string {
  return section.number ? `${section.number} ${section.title}` : section.title;
}

function SectionRow({
  node,
  depth,
  categoryOptions,
  onPick,
  disabled,
}: {
  node: UnallocatedTreeNode;
  depth: number;
  categoryOptions: ProjectPassportCategoryOption[];
  onPick: (positionItemId: number, option: ProjectPassportCategoryOption) => void;
  disabled: boolean;
}) {
  const indent = 12 + depth * 20;

  return (
    <div data-testid={`unallocated-section-${node.position_item_id}`}>
      <div
        data-testid={`unallocated-section-row-${node.position_item_id}`}
        className="grid grid-cols-[1fr_auto_auto] items-start gap-x-3 gap-y-1 border-b border-border-subtle py-2"
        style={{ paddingLeft: indent }}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-1.5">
            {node.number && (
              <span className="font-mono text-xs text-fg-tertiary">{node.number}</span>
            )}
            <span
              data-testid={`unallocated-section-title-${node.position_item_id}`}
              data-print="clamp"
              className="line-clamp-2 min-w-0"
            >
              {node.title}
            </span>
          </div>
          {node.smr_article_raw !== null && node.smr_article_raw.trim().length > 0 && (
            <p className="text-2xs text-fg-tertiary">в файле стояло: «{node.smr_article_raw}»</p>
          )}
        </div>

        {/*
          ДВЕ суммы, не выбор одной из двух (правка ревью 1, finding C-1):
          спека §1.5 — «у узла нужны две суммы — своя и по поддереву», та же
          пара, что у статей классификатора различает `own`/`total`. Свёртка
          поддерева — ПЕРВИЧНАЯ и всегда на месте (её и защищает testid
          `subtree-amount-{id}`, независимо от того, известно значение или
          нет — тот же принцип, что у `amount-cat-*` в CategoryTable: имя
          testid называет ПОЛЕ, а не факт его наличия, поэтому `—` под этим
          testid не значит подмену смысла, finding M-2). Своя сумма — ВТОРАЯ
          строка, и появляется только когда известна: без неё узел со своими
          деньгами и большим нераспределённым поддеревом ранжировался бы по
          одному числу («цена решения»), а показывал другое.
        */}
        <div className="text-right">
          <span data-testid={`subtree-amount-${node.position_item_id}`}>
            <MoneyCell value={node.subtree_amount} />
          </span>
          {node.amount !== null && (
            <p className="text-2xs text-fg-tertiary">
              своя:{" "}
              <span data-testid={`own-amount-${node.position_item_id}`}>
                <MoneyCell value={node.amount} />
              </span>
            </p>
          )}
        </div>

        <CategoryPicker
          positionItemId={node.position_item_id}
          label={chapterLabel(node)}
          options={categoryOptions}
          disabled={disabled}
          onPick={(option) => onPick(node.position_item_id, option)}
        />
      </div>

      {node.children.map((child) => (
        <SectionRow
          key={child.position_item_id}
          node={child}
          depth={depth + 1}
          categoryOptions={categoryOptions}
          onPick={onPick}
          disabled={disabled}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Выбор статьи — Command внутри Popover, по category_options
// ---------------------------------------------------------------------------

function CategoryPicker({
  positionItemId,
  label,
  options,
  onPick,
  disabled,
}: {
  positionItemId: number;
  /** Раздел, на который действует кнопка — идёт в `aria-label` (finding I-3). */
  label: string;
  options: ProjectPassportCategoryOption[];
  onPick: (option: ProjectPassportCategoryOption) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {/*
        Триггер НЕ несёт `role="combobox"` (в отличие от EntityCombobox): на
        одной панели таких кнопок много, по одной на раздел, и общая
        accessible-роль сделала бы их неотличимыми друг от друга для
        `getByRole`. `data-testid` различает их ТОЛЬКО в тестах — для
        скринридера testid не существует вовсе, различает его именно
        `aria-label` ниже, с номером и названием раздела.
      */}
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={`pick-category-${positionItemId}`}
            aria-label={`Отнести на статью: ${label}`}
            disabled={disabled}
          >
            Отнести на статью…
          </Button>
        }
      />
      <PopoverContent className="w-80 p-0" align="end">
        {/*
          Справочник — 362 строки (докстрока `ProjectPassportCategoryOption`);
          без поиска список неюзабелен. Источник — `category_options`, а НЕ
          `categories`: `build_tree` прячет вложенные узлы без строк, и без
          `category_options` половина справочника до аналитика не дошла бы.
        */}
        <Command>
          <CommandInput placeholder="Код или название статьи…" />
          <CommandList>
            <CommandEmpty>Ничего не найдено</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={option.id}
                  // Код и название вместе — встроенный фильтр `Command` ищет
                  // подстроку/подпоследовательность именно в `value`, и без
                  // названия здесь поиск по названию не работал бы вовсе.
                  value={`${option.code} ${option.title}`}
                  onSelect={() => {
                    onPick(option);
                    setOpen(false);
                  }}
                >
                  <span className="font-mono text-xs text-fg-tertiary">{option.code}</span>
                  <span className="truncate">{option.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
//  Разнесено вручную
// ---------------------------------------------------------------------------

function ManualAssignmentRow({
  assignment,
  onClear,
  disabled,
}: {
  assignment: ProjectPassportManualAssignment;
  onClear: () => void;
  disabled: boolean;
}) {
  return (
    <div
      data-testid={`manual-assignment-${assignment.position_item_id}`}
      className="grid grid-cols-[1fr_auto_auto] items-start gap-x-3 gap-y-1 border-b border-border-subtle px-2 py-2"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-1.5">
          {assignment.number && (
            <span className="font-mono text-xs text-fg-tertiary">{assignment.number}</span>
          )}
          <span data-print="clamp" className="line-clamp-2 min-w-0">
            {assignment.title}
          </span>
        </div>
        <p className="text-2xs text-fg-secondary">
          → {assignment.category_code} «{assignment.category_title}»
        </p>
        <p className="text-2xs text-fg-tertiary">
          {assignment.assigned_by_email} · {formatDate(assignment.assigned_at)}
        </p>
        {assignment.note && <p className="text-2xs text-fg-tertiary">{assignment.note}</p>}
      </div>

      <div className="text-right">
        <span data-testid={`manual-amount-${assignment.position_item_id}`}>
          <MoneyCell value={assignment.subtree_amount} />
        </span>
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled}
        data-testid={`manual-remove-${assignment.position_item_id}`}
        // Тот же довод, что у CategoryPicker (finding I-3): подпись несёт
        // раздел, иначе N кнопок «Снять» звучат для скринридера одинаково.
        aria-label={`Снять решение по разделу: ${chapterLabel(assignment)}`}
        onClick={onClear}
      >
        Снять
      </Button>
    </div>
  );
}
