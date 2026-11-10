import type {
  OurPriceCategory,
  OurPriceChapterStep,
  OurPriceLine,
  OurPriceNodeTotals,
  OurPriceOffer,
} from "@/types/domain";

import type { NoPriceMode } from "./offerFilter";
import { UNALLOCATED_TITLE } from "./ourPriceCopy";

export type { NoPriceMode };

/**
 * Узел дерева страницы участника (спека упрощения каталога §2.10): статья классификатора
 * (`id = null` — «Нераспределённое»), раздел сметы или строка. `depth` — глубина от корня статей.
 * Итоги узла — пара, пришедшая с сервера, как есть: деньги на клиенте не складываются.
 */
export type OfferTreeNode =
  | {
      kind: "category";
      id: number | null;
      depth: number;
      title: string;
      code: string | null;
      totals: OurPriceNodeTotals;
      children: OfferTreeNode[];
    }
  | { kind: "chapter"; id: number; depth: number; title: string; totals: OurPriceNodeTotals; children: OfferTreeNode[] }
  | { kind: "line"; line: OurPriceLine; depth: number };

/** Ключ «Нераспределённого» в `node_totals.categories`: `None` сервера уходит в JSON строкой. */
const UNALLOCATED_TOTALS_KEY = "null";

/** Форма названия для сравнения разделов со статьёй: регистр, пробелы и конечное двоеточие не различают. */
function normalizedTitle(title: string): string {
  return title.replace(/\s+/g, " ").trim().replace(/\s*:$/, "").toLowerCase();
}

function totalsOf(totals: Record<string, OurPriceNodeTotals>, key: string, what: string): OurPriceNodeTotals {
  const found = totals[key];
  if (found === undefined) throw new Error(`В ответе нет итога узла «${what}» ${key}`);
  return found;
}

/** Узел-черновик: порядок детей — порядок появления, разделы находятся по `chapter_item_id`. */
interface Container {
  items: Array<ChapterDraft | OurPriceLine>;
  chapters: Map<number, ChapterDraft>;
}

interface ChapterDraft extends Container {
  step: OurPriceChapterStep;
}

interface CategoryDraft extends Container {
  id: number | null;
  title: string;
  code: string | null;
  sortOrder: number | null;
  children: Map<number, CategoryDraft>;
}

function compareCategories(a: CategoryDraft, b: CategoryDraft): number {
  if (a.sortOrder !== b.sortOrder) {
    if (a.sortOrder === null) return 1;
    if (b.sortOrder === null) return -1;
    return a.sortOrder - b.sortOrder;
  }
  return (a.code ?? "").localeCompare(b.code ?? "", "ru", { numeric: true });
}

function draftOf(category: OurPriceCategory | null): CategoryDraft {
  return {
    id: category === null ? null : category.id,
    title: category === null ? UNALLOCATED_TITLE : category.title,
    code: category === null ? null : category.code,
    sortOrder: category === null ? null : category.sort_order,
    children: new Map(),
    items: [],
    chapters: new Map(),
  };
}

function isChapter(item: ChapterDraft | OurPriceLine): item is ChapterDraft {
  return "step" in item;
}

/**
 * Дерево страницы участника из `categories`, `lines[].category_id` и `lines[].chapter_path`.
 * Скелет — статьи классификатора во всю глубину; внутри статьи строки — по разделам сметы. Корень сметы
 * и раздел, чьё название повторяет название статьи строки (регистр, пробелы, конечное двоеточие), узлами не
 * становятся: его строки и вложенные разделы стоят у родителя. «Нераспределённое» — последним корнем.
 */
export function buildOfferTree(offer: OurPriceOffer): OfferTreeNode[] {
  const meta = new Map(offer.categories.map((category) => [category.id, category]));
  const roots = new Map<number, CategoryDraft>();
  const drafts = new Map<number, CategoryDraft>();
  let unallocated: CategoryDraft | null = null;

  /** Черновик статьи; предки заводятся по пути вверх, первый из них — корень. */
  function categoryDraft(id: number): CategoryDraft {
    const existing = drafts.get(id);
    if (existing !== undefined) return existing;
    const category = meta.get(id);
    if (category === undefined) throw new Error(`В ответе нет статьи ${id}`);
    const draft = draftOf(category);
    drafts.set(id, draft);
    if (category.parent_id === null) roots.set(id, draft);
    else categoryDraft(category.parent_id).children.set(id, draft);
    return draft;
  }

  for (const line of offer.lines) {
    let container: CategoryDraft;
    if (line.category_id === null) {
      unallocated ??= draftOf(null);
      container = unallocated;
    } else {
      container = categoryDraft(line.category_id);
    }
    const hidden = normalizedTitle(container.title);
    let place: Container = container;
    for (const step of line.chapter_path.slice(1)) {
      if (normalizedTitle(step.title) === hidden) continue;
      let chapter = place.chapters.get(step.chapter_item_id);
      if (chapter === undefined) {
        chapter = { step, items: [], chapters: new Map() };
        place.chapters.set(step.chapter_item_id, chapter);
        place.items.push(chapter);
      }
      place = chapter;
    }
    place.items.push(line);
  }

  /** Раздел показан внутри статьи, и его итог берётся в разрезе этой статьи. */
  function itemNodes(items: Array<ChapterDraft | OurPriceLine>, depth: number, categoryKey: string): OfferTreeNode[] {
    return items.map((item): OfferTreeNode => {
      if (!isChapter(item)) return { kind: "line", line: item, depth };
      return {
        kind: "chapter",
        id: item.step.chapter_item_id,
        depth,
        title: item.step.title,
        totals: totalsOf(
          offer.node_totals.chapters[categoryKey] ?? {},
          String(item.step.chapter_item_id),
          "раздела"
        ),
        children: itemNodes(item.items, depth + 1, categoryKey),
      };
    });
  }

  function categoryNode(draft: CategoryDraft, depth: number): OfferTreeNode {
    const key = draft.id === null ? UNALLOCATED_TOTALS_KEY : String(draft.id);
    return {
      kind: "category",
      id: draft.id,
      depth,
      title: draft.title,
      code: draft.code,
      totals: totalsOf(offer.node_totals.categories, key, "статьи"),
      children: [
        ...[...draft.children.values()].sort(compareCategories).map((child) => categoryNode(child, depth + 1)),
        ...itemNodes(draft.items, depth + 1, key),
      ],
    };
  }

  const tree = [...roots.values()].sort(compareCategories).map((root) => categoryNode(root, 0));
  return unallocated === null ? tree : [...tree, categoryNode(unallocated, 0)];
}

/**
 * Оставляет строки, для которых `keep` истинно, и уровни, в которых что-то осталось. Итоги уровней — те же
 * объекты ответа: видимость строк на них не влияет. Исходное дерево не меняется.
 */
export function pruneOfferTree(nodes: OfferTreeNode[], keep: (line: OurPriceLine) => boolean): OfferTreeNode[] {
  return nodes.flatMap((node): OfferTreeNode[] => {
    if (node.kind === "line") return keep(node.line) ? [node] : [];
    const children = pruneOfferTree(node.children, keep);
    return children.length === 0 ? [] : [{ ...node, children }];
  });
}

/** Строки поддерева в порядке обхода. */
export function collectLines(nodes: OfferTreeNode[]): OurPriceLine[] {
  return nodes.flatMap((node) => (node.kind === "line" ? [node.line] : collectLines(node.children)));
}
