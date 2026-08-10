import { useId, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { roundDecimal } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  ProjectPassport,
  ProjectPassportCategory,
  ProjectPassportSection,
  ProjectPassportUnallocated,
} from "@/types/domain";

/**
 * Таблица по статьям классификатора (Ф6 фазы 7, спека §2.9 пп. 5-9, 12, 15;
 * план, задача 8).
 *
 * Референс раскладки — `docs/superpowers/specs/2026-08-09-project-passport-mockup.html`
 * (`renderTable`/`renderNode`), одобренный на гейте 1. Файл там — форма, а не код
 * для копирования: здесь примитивы shadcn/ui и токены темы, а не литеральные цвета.
 *
 * **Сервер отдаёт `categories` ПЛОСКИМ списком** с `parent_id`, порядок внутри —
 * глубина обхода (правило 2 спеки §2.6). Компонент восстанавливает иерархию, но
 * порядок братьев внутри уровня НЕ пересортировывает — только сохраняет порядок
 * исходного списка при разборе по родителям.
 */

interface CategoryNode extends ProjectPassportCategory {
  children: CategoryNode[];
}

/** Плоский список → дерево. Порядок братьев — порядок появления в исходном
 *  списке (уже глубина обхода, сервер его не пересматривает — план §2.6). */
function buildCategoryTree(categories: ProjectPassportCategory[]): CategoryNode[] {
  const byId = new Map<number, CategoryNode>();
  for (const category of categories) {
    byId.set(category.id, { ...category, children: [] });
  }
  const roots: CategoryNode[] = [];
  for (const category of categories) {
    const node = byId.get(category.id);
    if (!node) continue;
    if (category.parent_id === null) {
      roots.push(node);
      continue;
    }
    const parent = byId.get(category.parent_id);
    parent?.children.push(node);
  }
  return roots;
}

/** Ноль ли десятичная строка — теми же знаками, что `PassportHeader.isZeroDecimal`:
 *  без `Number()`, деньги — `Decimal`-строки (AGENTS.md §3). */
function isZeroDecimal(value: string): boolean {
  return /^-?0+(\.0+)?$/.test(value.trim());
}

/** Доля в процентах с точностью 0,01, той же арифметикой (`roundDecimal`), что и
 *  деньги: без `Number()` — знаменатель `totals.amount` считает сервер (§2.6). */
function formatSharePct(value: string | null): string {
  if (value === null) return "—";
  return `${roundDecimal(value, 2).replace(".", ",")} %`;
}

/** Одна подпись раздела: «6.5 «Прочее»». Заголовок, уже несущий кавычки
 *  (справочник Ф6 такие заводит), вторично не оборачивается. */
function sectionLabel(section: ProjectPassportSection): string {
  const title = section.title.trim().startsWith("«") ? section.title : `«${section.title}»`;
  return section.number ? `${section.number} ${title}` : title;
}

/**
 * Подпись служебной строки собственных денег статьи (правило 6 §2.9).
 *
 * Строится ИЗ `own_sections`, а не из кода статьи и не из номера раздела файла:
 * нумерация файла и коды классификатора — разные оси (§1.5 спеки, находка
 * «строка 6.4 несёт статью 6.6»). Пустой список — узел без известных разделов,
 * печатается нейтральный текст мокапа, а не догадка по номеру.
 */
function ownSectionsCaption(sections: ProjectPassportSection[]): string {
  if (sections.length === 0) return "позиции, привязанные прямо к этой статье";
  const word = sections.length === 1 ? "раздел сметы" : "разделы сметы";
  return `${word} ${sections.map(sectionLabel).join(", ")}`;
}

/**
 * Подпись неполноты узла (правило 2 §2.3, спека): стоит, когда `rowsPriced <
 * rows`, и называет ОБЕ причины отдельно — пустую цену и негодное значение.
 * Текст, называющий только одну причину, был бы уже собственного условия
 * (урок Ф4a).
 */
function incompletenessCaption(
  rows: number,
  rowsPriced: number,
  rowsNotFinite: number
): string | null {
  if (rowsPriced >= rows) return null;
  const missingPrice = rows - rowsPriced - rowsNotFinite;
  const parts: string[] = [];
  if (missingPrice > 0) parts.push(`без цены: ${missingPrice}`);
  if (rowsNotFinite > 0) parts.push(`с ошибкой: ${rowsNotFinite}`);
  return parts.length > 0 ? parts.join(", ") : null;
}

/** Подпись «Нераспределённого» (правило 8 §2.9): `chapters` и
 *  `rows_outside_structure` — ДВЕ разные причины одного следствия (§2.2 спеки:
 *  «поправить привязку раздела» ≠ «строка сиротлива»), названы отдельно. */
function unallocatedCaption(unallocated: ProjectPassportUnallocated): string {
  const chapterWord = unallocated.chapters === 1 ? "раздел" : unallocated.chapters % 10 >= 2 && unallocated.chapters % 10 <= 4 && (unallocated.chapters % 100 < 10 || unallocated.chapters % 100 >= 20) ? "раздела" : "разделов";
  const base = `${unallocated.chapters} ${chapterWord} сметы без статьи классификатора`;
  if (unallocated.rows_outside_structure <= 0) return base;
  const rowsWord = unallocated.rows_outside_structure === 1 ? "позиция" : "позиций";
  return `${base}; отдельно — ${unallocated.rows_outside_structure} ${rowsWord} вне структуры сметы (без ссылки на раздел)`;
}

function ExpandToggle({
  expandable,
  expanded,
  code,
  onToggle,
}: {
  expandable: boolean;
  expanded: boolean;
  code: string;
  onToggle: () => void;
}) {
  if (!expandable) {
    return <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />;
  }
  return (
    <button
      type="button"
      // Кнопка раскрытия — управление, а не часть документа: на бумаге шеврон
      // ничего не значит, потому что раскрыть там нечего (спека §2.11
      // «служебные элементы уходят»). CSS скрывает ровно то, что помечено.
      data-print="hide"
      aria-expanded={expanded}
      aria-label={`${expanded ? "Свернуть" : "Развернуть"} статью ${code}`}
      onClick={onToggle}
      className="shrink-0 text-fg-tertiary hover:text-fg"
    >
      <ChevronRight
        aria-hidden="true"
        className={cn("size-3.5 transition-transform", expanded && "rotate-90")}
      />
    </button>
  );
}

function CategoryRow({
  node,
  depth,
  expandedIds,
  onToggle,
  showZero,
}: {
  node: CategoryNode;
  depth: number;
  expandedIds: Set<number>;
  onToggle: (id: number) => void;
  showZero: boolean;
}) {
  const isOpen = expandedIds.has(node.id);
  const hasChildren = node.children.length > 0;
  const hasExtras = node.extras.length > 0;
  const expandable = hasChildren || hasExtras;
  const ownRowVisible = expandable && node.own_rows > 0;
  const incompleteness = incompletenessCaption(node.rows, node.rows_priced, node.rows_not_finite);
  // Те же три правила действуют и для СОБСТВЕННЫХ денег статьи — по own_rows,
  // own_rows_priced и own_rows_not_finite (спека §2.3, последний абзац). Без
  // этого служебная строка показывала бы частичную сумму числом и без единого
  // признака неполноты — ровно то, что §2.3 объявляет недопустимым для узла.
  const ownIncompleteness = incompletenessCaption(
    node.own_rows,
    node.own_rows_priced,
    node.own_rows_not_finite
  );
  const indent = 8 + depth * 20;

  return (
    <>
      <TableRow
        data-testid={`row-cat-${node.code}`}
        data-print="row"
        className={depth === 0 ? "border-t border-border-subtle" : undefined}
      >
        <TableCell className="font-mono text-xs text-fg-secondary">{node.code}</TableCell>
        <TableCell>
          <div className="flex min-w-0 items-start gap-2" style={{ paddingLeft: indent }}>
            <ExpandToggle
              expandable={expandable}
              expanded={isOpen}
              code={node.code}
              onToggle={() => onToggle(node.id)}
            />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-1.5">
                <span
                  data-print="clamp"
                  className={cn("line-clamp-2 min-w-0", depth === 0 && "font-semibold text-fg")}
                >
                  {node.title}
                </span>
                {node.is_bucket && <Badge variant="outline">корзина</Badge>}
              </div>
              {incompleteness && (
                <p data-testid={`incompleteness-cat-${node.code}`} className="mt-0.5 text-2xs text-fg-tertiary">
                  {incompleteness}
                </p>
              )}
            </div>
          </div>
        </TableCell>
        <TableCell data-testid={`amount-cat-${node.code}`} className="text-right">
          <MoneyCell value={node.total} className={depth === 0 ? "font-semibold" : undefined} />
        </TableCell>
        <TableCell className="text-right text-xs text-fg-secondary">
          {formatSharePct(node.share_pct)}
        </TableCell>
        <TableCell className="text-right">
          <MoneyCell value={node.per_sqm} />
        </TableCell>
      </TableRow>

      {isOpen && (
        <>
          {node.children.map((child) => {
            const hiddenAsZero =
              !showZero && child.total !== null && isZeroDecimal(child.total);
            if (hiddenAsZero) return null;
            return (
              <CategoryRow
                key={child.id}
                node={child}
                depth={depth + 1}
                expandedIds={expandedIds}
                onToggle={onToggle}
                showZero={showZero}
              />
            );
          })}

          {node.extras.map((extra) => (
            <TableRow key={extra.id} data-testid={`row-extra-${node.code}-${extra.id}`} data-print="row">
              <TableCell className="font-mono text-xs text-fg-tertiary">·</TableCell>
              <TableCell>
                <div className="flex items-baseline gap-2" style={{ paddingLeft: indent + 20 }}>
                  <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />
                  <span className="italic text-fg-secondary">{extra.title}</span>
                  <Badge variant="outline" className="border-accent-border bg-accent-soft text-accent-text">
                    доп. работы
                  </Badge>
                </div>
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={extra.amount} />
              </TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
            </TableRow>
          ))}

          {ownRowVisible && (
            <TableRow data-testid={`row-own-${node.code}`} data-print="row">
              <TableCell className="font-mono text-xs text-fg-tertiary">·</TableCell>
              <TableCell>
                <div className="flex items-baseline gap-2" style={{ paddingLeft: indent + 20 }}>
                  <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />
                  <div className="min-w-0">
                    <span className="italic text-fg-secondary">
                      {hasChildren ? "Без подстатьи" : "Позиции сметы"}
                    </span>
                    <p
                      data-testid={`own-caption-${node.code}`}
                      className="text-2xs text-fg-tertiary"
                    >
                      {ownSectionsCaption(node.own_sections)}
                    </p>
                    {ownIncompleteness && (
                      <p
                        data-testid={`own-incompleteness-${node.code}`}
                        className="text-2xs text-fg-tertiary"
                      >
                        {ownIncompleteness}
                      </p>
                    )}
                  </div>
                </div>
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={node.own} />
              </TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
            </TableRow>
          )}
        </>
      )}
    </>
  );
}

export function CategoryTable({ passport }: { passport: ProjectPassport }) {
  const { categories, unallocated, totals } = passport;
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => new Set());
  // Правило 12 §2.9: переключатель по умолчанию ВЫКЛЮЧЕН — нулевые подстатьи
  // скрыты, пока пользователь их не запросит явно.
  const [showZero, setShowZero] = useState(false);
  const zeroToggleId = useId();

  const roots = useMemo(() => buildCategoryTree(categories), [categories]);

  function toggle(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    // data-print="sheet" НЕ здесь: он на корне документа (ProjectPassportPage,
    // задача 10) — таблица лишь часть листа, а не лист целиком.
    <section className="border border-border-subtle bg-surface">
      {/*
        Метка стоит на ВСЕЙ полосе, а не на одном переключателе: пометив только
        сам `Switch`, мы бы убрали с бумаги орган управления и оставили висеть
        его осиротевшую подпись «показывать нулевые подстатьи» вместе с
        разделительной чертой — то есть напечатали бы половину служебного
        элемента.
      */}
      <div
        data-print="hide"
        className="flex items-center justify-end gap-2 border-b border-border-subtle px-6 py-2.5"
      >
        <Label htmlFor={zeroToggleId} className="text-xs font-normal text-fg-secondary">
          показывать нулевые подстатьи
        </Label>
        <Switch id={zeroToggleId} checked={showZero} onCheckedChange={setShowZero} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16 pl-6">Код</TableHead>
            <TableHead>Статья классификатора</TableHead>
            <TableHead className="text-right">Итого, ₽</TableHead>
            <TableHead className="text-right">Доля</TableHead>
            <TableHead className="text-right pr-6">₽ / м²</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {roots.map((root) => (
            <CategoryRow
              key={root.id}
              node={root}
              depth={0}
              expandedIds={expandedIds}
              onToggle={toggle}
              showZero={showZero}
            />
          ))}

          <TableRow
            data-testid="row-unallocated"
            data-print="row"
            className="border-y border-warning-border bg-warning-soft"
          >
            <TableCell className="text-warning-text">⚠</TableCell>
            <TableCell>
              <p className="font-semibold text-warning-text">Нераспределённое</p>
              <p data-testid="unallocated-caption" className="text-2xs text-warning-text">
                {unallocatedCaption(unallocated)}
              </p>
            </TableCell>
            <TableCell className="text-right">
              <MoneyCell value={unallocated.amount} className="text-warning-text" />
            </TableCell>
            <TableCell className="text-right text-xs text-warning-text">
              {formatSharePct(unallocated.share_pct)}
            </TableCell>
            <TableCell className="text-right">
              <MoneyCell value={unallocated.per_sqm} className="text-warning-text" />
            </TableCell>
          </TableRow>

          <TableRow data-testid="row-grand-total" data-print="row" className="border-t-2 border-fg">
            <TableCell />
            <TableCell className="font-serif text-base font-semibold text-fg">
              Итого по договору
            </TableCell>
            <TableCell className="text-right">
              <MoneyCell value={totals.amount} className="font-semibold" />
            </TableCell>
            {/*
              Доля итога — тавтологические 100 %, но ТОЛЬКО когда итог вообще
              пригоден как знаменатель. Литерал «100,00 %» здесь означал бы
              «сто процентов от неизвестной суммы» при пустом итоге и «сто
              процентов от нуля» при нулевом — ровно то, что правило 5 §2.6
              запрещает всем остальным строкам, чей `share_pct` в этих случаях
              приходит `null`. Прочерк ставится по тому же признаку, что и у
              них: сумма непригодна как знаменатель.
            */}
            <TableCell className="text-right text-sm font-semibold text-fg">
              {totals.amount === null || isZeroDecimal(totals.amount) ? "—" : "100,00 %"}
            </TableCell>
            <TableCell className="text-right">
              <MoneyCell value={totals.per_sqm} className="font-semibold" />
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </section>
  );
}
