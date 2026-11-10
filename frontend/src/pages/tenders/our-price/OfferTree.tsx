import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { TableCell, TableRow } from "@/components/ui/table";

import { INDENT_REM, OfferLinesTable, type LayoutEntry } from "./OfferLinesTable";
import { matchesFilter, matchesNoPriceMode, type NoPriceMode, type OfferFilter } from "./offerFilter";
import { collectLines, pruneOfferTree, type OfferTreeNode } from "./offerTree.ts";
import { formatMoney, pick, type VatView } from "./money";
import {
  CHAPTER_MARK,
  nodeKpLabel,
  noPriceCountLabel,
  PATH_SEPARATOR,
  rowsLabel,
  UNALLOCATED_ARTICLE,
} from "./ourPriceCopy";

export interface OfferTreeProps {
  /** Дети корневой статьи: промежуточные статьи, разделы сметы и строки. */
  nodes: OfferTreeNode[];
  /** Названия предков `nodes` (корневая статья): начало пути в плоском списке. */
  trail: string[];
  vat: VatView;
  rateClass: string;
  canResolve?: boolean;
  mode: NoPriceMode;
  filter: OfferFilter;
  /** Уровни раскрыты при входе (`?article=` и «только они»); щелчок по уровню переворачивает его. */
  defaultOpen: boolean;
  onOpenLine: (positionItemId: number) => void;
}

type Group = Exclude<OfferTreeNode, { kind: "line" }>;

/** Метка узла в идентификаторах теста и ключах: у «Нераспределённого» нет id. */
function nodeLabel(node: Group): string {
  return node.kind === "category" ? `cat-${node.id ?? UNALLOCATED_ARTICLE}` : `chap-${node.id}`;
}

/** Путь каждой строки: статьи и разделы от корня, номеров сметы нет. */
function pathsOf(nodes: OfferTreeNode[], trail: string[]): Map<number, string> {
  const paths = new Map<number, string>();
  const walk = (level: OfferTreeNode[], titles: string[]) => {
    for (const node of level) {
      if (node.kind === "line") paths.set(node.line.position_item_id, titles.join(PATH_SEPARATOR));
      else walk(node.children, [...titles, node.title]);
    }
  };
  walk(nodes, trail);
  return paths;
}

interface GroupRowProps {
  node: Group;
  testKey: string;
  open: boolean;
  vat: VatView;
  colSpan: number;
  onToggle: () => void;
}

/** Уровень дерева: название, число строк, сумма КП и «N без цены» — всё из ответа, ничего не считается. */
function GroupRow({ node, testKey, open, vat, colSpan, onToggle }: GroupRowProps) {
  const { totals } = node;
  const kp = pick(totals.kp_total, vat);
  return (
    <TableRow data-testid={`node-${testKey}`} className="bg-surface-sunken">
      <TableCell colSpan={colSpan} style={{ paddingLeft: `${node.depth * INDENT_REM + 0.5}rem` }}>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <button
            type="button"
            data-testid={`node-toggle-${testKey}`}
            aria-expanded={open}
            className="flex items-center gap-1 whitespace-normal text-left font-medium"
            onClick={onToggle}
          >
            {open ? <ChevronDown className="size-4 shrink-0" aria-hidden /> : <ChevronRight className="size-4 shrink-0" aria-hidden />}
            {node.kind === "chapter" && (
              <span className="rounded border px-1 text-[0.6875rem] font-normal text-fg-secondary">{CHAPTER_MARK}</span>
            )}
            {node.kind === "category" && node.code !== null && (
              <span className="font-mono text-xs font-normal text-fg-muted">{node.code}</span>
            )}
            {node.title}
          </button>
          <span className="flex flex-wrap items-baseline gap-x-2 text-xs text-fg-secondary">
            {totals.no_price > 0 && (
              <span data-testid={`node-no-price-${testKey}`} className="font-medium text-danger-text">
                {noPriceCountLabel(totals.no_price)}
              </span>
            )}
            <span data-testid={`node-lines-${testKey}`}>{rowsLabel(totals.lines)}</span>
            <span data-testid={`node-kp-${testKey}`} className="font-mono tabular-nums">
              {nodeKpLabel(kp === null ? "—" : formatMoney(kp))}
            </span>
          </span>
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * Содержимое раскрытой корневой статьи: промежуточные и конечные статьи, разделы сметы и строки КП в одной
 * таблице (спека упрощения каталога §2.10). Фильтры (плитка, «без цены») меняют только видимость строк и
 * уровней; итоги уровней — те же по всем строкам узла.
 */
export function OfferTree({
  nodes,
  trail,
  vat,
  rateClass,
  canResolve,
  mode,
  filter,
  defaultOpen,
  onOpenLine,
}: OfferTreeProps) {
  const visible = useMemo(
    () => pruneOfferTree(nodes, (line) => matchesFilter(line, filter) && matchesNoPriceMode(line, mode)),
    [nodes, filter, mode]
  );
  const lines = useMemo(() => collectLines(visible), [visible]);
  const paths = useMemo(() => pathsOf(visible, trail), [visible, trail]);
  const [flipped, setFlipped] = useState<ReadonlySet<string>>(new Set());

  function toggle(key: string) {
    setFlipped((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  const entries: LayoutEntry[] = [];
  const walk = (level: OfferTreeNode[], parentKey: string) => {
    for (const node of level) {
      if (node.kind === "line") {
        entries.push({ kind: "line", line: node.line, depth: node.depth });
        continue;
      }
      const testKey = nodeLabel(node);
      const key = `${parentKey}/${testKey}`;
      const open = defaultOpen !== flipped.has(key);
      entries.push({
        kind: "group",
        key,
        render: (colSpan) => (
          <GroupRow node={node} testKey={testKey} open={open} vat={vat} colSpan={colSpan} onToggle={() => toggle(key)} />
        ),
      });
      if (open) walk(node.children, key);
    }
  };
  walk(visible, "");

  return (
    <OfferLinesTable
      lines={lines}
      layout={{ entries, paths }}
      vat={vat}
      rateClass={rateClass}
      canResolve={canResolve}
      onOpenLine={onOpenLine}
    />
  );
}
