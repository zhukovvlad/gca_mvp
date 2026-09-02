import type { JSX, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format";
import type { ProjectPassportCategoryOption } from "@/types/domain";

import { CategoryPicker } from "./CategoryPicker";

/**
 * Презентационное ядро панели-верстака разноса (спека этапного разноса
 * §2.7; план, задача 10) — дерево разделов без статьи плюс список ручных
 * решений. Выделено из `UnallocatedPanel.tsx` (панель паспорта, задача 8),
 * чтобы тендерный `UnallocatedSheet` (задача 11) мог переиспользовать ту же
 * разметку с другой правой колонкой (счётчик позиций вместо денег) и другой
 * сортировкой (файловый порядок вместо убывания свёртки).
 *
 * Разметка дерева, popover выбора статьи и строк «Разнесено вручную»
 * переехали сюда из `UnallocatedPanel.tsx` ДОСЛОВНО — testid, aria-label,
 * триггер, плейсхолдер, `data-print="clamp"`, `line-clamp-2` и отступ
 * `12 + depth * 20` не изменились ни в одном месте (гейт задачи: тесты
 * `UnallocatedPanel.test.tsx` остаются зелёными без единой правки).
 *
 * Корень `<section data-testid="unallocated-panel" data-print="hide">`
 * ОСТАЁТСЯ в обёртке паспорта — один существующий тест проверяет именно его;
 * здесь — обычный `<div>`.
 */

export interface WorkbenchSection {
  key: string;
  parentKey: string | null;
  number: string | null;
  title: string;
  smr_article_raw: string | null;
}

export interface WorkbenchManualRow {
  key: string;
  number: string | null;
  title: string;
  category_code: string;
  category_title: string;
  assigned_by_email: string;
  assigned_at: string;
  note: string | null;
}

export interface WorkbenchCopy {
  sectionsHeading: string;
  sectionsHint: string;
  sectionsEmpty: string;
  manualHeading: string;
  manualHint: string;
  manualEmpty: string;
}

/**
 * `false` — поля «Заметка» в пикере нет (паспорт: по-сметный маршрут
 * заметку с экрана не принимает). Иначе — функция, отдающая существующие
 * заметки офер-смет раунда для конкретного раздела (тендер, §2.7).
 */
export type NoteField<S> = false | { existingNotes: (section: S) => (string | null)[] };

export interface UnallocatedWorkbenchProps<
  S extends WorkbenchSection,
  M extends WorkbenchManualRow,
> {
  sections: S[];
  manual: M[];
  categoryOptions: ProjectPassportCategoryOption[];
  copy: WorkbenchCopy;
  /** Хвост data-testid строк и кнопок: паспорт — position_item_id, тендер —
   *  `${lot_key}:${position_key}`. */
  testId: (section: { key: string }) => string;
  /** Порядок сиблингов; undefined — порядок прихода (файловый, тендер). */
  compareSiblings?: (a: S, b: S) => number;
  /** Правая колонка дерева: деньги (паспорт) или «N позиций» (тендер). */
  renderAside: (section: S) => ReactNode;
  renderManualAside: (row: M) => ReactNode;
  /** Пометка под названием: partial/conflict (тендер); паспорту не нужна. */
  renderMark?: (section: S) => ReactNode;
  noteField: NoteField<S>;
  onPick: (section: S, option: ProjectPassportCategoryOption, note: string | null) => void;
  onClear: (row: M) => void;
  disabled: boolean;
  /** Блоки после «Разнесено вручную» (диагностика — тендер). */
  children?: ReactNode;
}

/** «14 SHELL & CORE» либо просто заголовок, если номера нет — общая подпись
 *  раздела для aria-label кнопок панели/Sheet (finding I-3 ревью панели
 *  паспорта, сохранено дословно).
 *
 * Модуль-приватная функция, а не экспорт: единственные два вызова — внутри
 * этого файла (`SectionRow`, `ManualRow`); экспорт нетронул бы поведение, но
 * `react-refresh/only-export-components` не пропускает соседний экспорт
 * функции, не являющейся компонентом, рядом с `UnallocatedWorkbench` —
 * правило проверено эмпирически (репорт задачи 10, ревью): оно срабатывает
 * на самом ЭКСПОРТЕ, а не на функции как таковой. Если задаче 11 понадобится
 * эта подпись извне ядра — её место в модуле копий Sheet, который план уже
 * заводит (`roundUnallocatedCopy.ts`), а не в одноимённом файле-обёртке. */
function chapterLabel(section: { number: string | null; title: string }): string {
  return section.number ? `${section.number} ${section.title}` : section.title;
}

// ---------------------------------------------------------------------------
//  Дерево — построение по parentKey
// ---------------------------------------------------------------------------

interface TreeNode<S extends WorkbenchSection> {
  data: S;
  children: TreeNode<S>[];
}

/**
 * Плоский список секций → дерево по `parentKey`. Узел, чей `parentKey` не
 * находится среди `sections[]` (или равен `null`), становится ВЕРХНИМ
 * уровнем — деградация в плоский верхний уровень честнее, чем потеря узла
 * (finding I-4 ревью панели паспорта: раньше такой узел молча пропадал бы
 * с единственного экрана, где его можно разнести).
 */
function buildTree<S extends WorkbenchSection>(
  sections: S[],
  compareSiblings?: (a: S, b: S) => number
): TreeNode<S>[] {
  const byKey = new Map<string, TreeNode<S>>();
  for (const section of sections) {
    byKey.set(section.key, { data: section, children: [] });
  }

  const roots: TreeNode<S>[] = [];
  for (const section of sections) {
    const node = byKey.get(section.key);
    if (!node) continue;
    if (section.parentKey === null) {
      roots.push(node);
      continue;
    }
    const parent = byKey.get(section.parentKey);
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  if (compareSiblings) {
    const sortRec = (nodes: TreeNode<S>[]) => {
      nodes.sort((a, b) => compareSiblings(a.data, b.data));
      for (const node of nodes) sortRec(node.children);
    };
    sortRec(roots);
  }

  return roots;
}

// ---------------------------------------------------------------------------
//  Строка дерева
// ---------------------------------------------------------------------------

function SectionRow<S extends WorkbenchSection>({
  node,
  depth,
  categoryOptions,
  testId,
  renderAside,
  renderMark,
  noteField,
  onPick,
  disabled,
}: {
  node: TreeNode<S>;
  depth: number;
  categoryOptions: ProjectPassportCategoryOption[];
  testId: (section: { key: string }) => string;
  renderAside: (section: S) => ReactNode;
  renderMark?: (section: S) => ReactNode;
  noteField: NoteField<S>;
  onPick: (section: S, option: ProjectPassportCategoryOption, note: string | null) => void;
  disabled: boolean;
}) {
  const section = node.data;
  const id = testId(section);
  const indent = 12 + depth * 20;

  return (
    <div data-testid={`unallocated-section-${id}`}>
      <div
        data-testid={`unallocated-section-row-${id}`}
        className="grid grid-cols-[1fr_auto_auto] items-start gap-x-3 gap-y-1 border-b border-border-subtle py-2"
        style={{ paddingLeft: indent }}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-1.5">
            {section.number && (
              <span className="font-mono text-xs text-fg-tertiary">{section.number}</span>
            )}
            <span
              data-testid={`unallocated-section-title-${id}`}
              data-print="clamp"
              className="line-clamp-2 min-w-0"
            >
              {section.title}
            </span>
          </div>
          {section.smr_article_raw !== null && section.smr_article_raw.trim().length > 0 && (
            <p className="text-2xs text-fg-tertiary">
              в файле стояло: «{section.smr_article_raw}»
            </p>
          )}
          {renderMark?.(section)}
        </div>

        {renderAside(section)}

        <CategoryPicker
          testKey={id}
          label={chapterLabel(section)}
          options={categoryOptions}
          disabled={disabled}
          noteField={noteField === false ? false : { existingNotes: noteField.existingNotes(section) }}
          onPick={(option, note) => onPick(section, option, note)}
        />
      </div>

      {node.children.map((child) => (
        <SectionRow
          key={testId(child.data)}
          node={child}
          depth={depth + 1}
          categoryOptions={categoryOptions}
          testId={testId}
          renderAside={renderAside}
          renderMark={renderMark}
          noteField={noteField}
          onPick={onPick}
          disabled={disabled}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Разнесено вручную
// ---------------------------------------------------------------------------

function ManualRow<M extends WorkbenchManualRow>({
  row,
  testId,
  renderAside,
  onClear,
  disabled,
}: {
  row: M;
  testId: (section: { key: string }) => string;
  renderAside: (row: M) => ReactNode;
  onClear: (row: M) => void;
  disabled: boolean;
}) {
  const id = testId(row);

  return (
    <div
      data-testid={`manual-assignment-${id}`}
      className="grid grid-cols-[1fr_auto_auto] items-start gap-x-3 gap-y-1 border-b border-border-subtle px-2 py-2"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-1.5">
          {row.number && <span className="font-mono text-xs text-fg-tertiary">{row.number}</span>}
          <span data-print="clamp" className="line-clamp-2 min-w-0">
            {row.title}
          </span>
        </div>
        <p className="text-2xs text-fg-secondary">
          → {row.category_code} «{row.category_title}»
        </p>
        <p className="text-2xs text-fg-tertiary">
          {row.assigned_by_email} · {formatDate(row.assigned_at)}
        </p>
        {row.note && <p className="text-2xs text-fg-tertiary">{row.note}</p>}
      </div>

      {renderAside(row)}

      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled}
        data-testid={`manual-remove-${id}`}
        // Тот же довод, что у CategoryPicker (finding I-3): подпись несёт
        // раздел, иначе N кнопок «Снять» звучат для скринридера одинаково.
        aria-label={`Снять решение по разделу: ${chapterLabel(row)}`}
        onClick={() => onClear(row)}
      >
        Снять
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Ядро
// ---------------------------------------------------------------------------

export function UnallocatedWorkbench<S extends WorkbenchSection, M extends WorkbenchManualRow>(
  props: UnallocatedWorkbenchProps<S, M>
): JSX.Element {
  const {
    sections,
    manual,
    categoryOptions,
    copy,
    testId,
    compareSiblings,
    renderAside,
    renderManualAside,
    renderMark,
    noteField,
    onPick,
    onClear,
    disabled,
    children,
  } = props;

  const roots = buildTree(sections, compareSiblings);

  return (
    <div>
      <div className="border-b border-border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-fg">{copy.sectionsHeading}</h3>
        <p className="text-2xs text-fg-tertiary">{copy.sectionsHint}</p>
      </div>
      <div className="px-2 py-1">
        {roots.length === 0 ? (
          <p className="px-2 py-3 text-xs text-fg-tertiary">{copy.sectionsEmpty}</p>
        ) : (
          roots.map((node) => (
            <SectionRow
              key={testId(node.data)}
              node={node}
              depth={0}
              categoryOptions={categoryOptions}
              testId={testId}
              renderAside={renderAside}
              renderMark={renderMark}
              noteField={noteField}
              onPick={onPick}
              disabled={disabled}
            />
          ))
        )}
      </div>

      <div className="border-t border-b border-border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-fg">{copy.manualHeading}</h3>
        <p className="text-2xs text-fg-tertiary">{copy.manualHint}</p>
      </div>
      <div className="px-2 py-1">
        {manual.length === 0 ? (
          <p className="px-2 py-3 text-xs text-fg-tertiary">{copy.manualEmpty}</p>
        ) : (
          manual.map((row) => (
            <ManualRow
              key={testId(row)}
              row={row}
              testId={testId}
              renderAside={renderManualAside}
              onClear={onClear}
              disabled={disabled}
            />
          ))
        )}
      </div>

      {children}
    </div>
  );
}
