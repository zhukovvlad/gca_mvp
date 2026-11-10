import { describe, expect, it } from "vitest";

import { nodeTotalsFixture, ourPriceOfferFixture, ourPriceTreeOfferFixture } from "@/test/fixtures";
import type { OurPriceOffer } from "@/types/domain";

import { buildOfferTree, pruneOfferTree, type OfferTreeNode } from "./offerTree.ts";

type Group = Extract<OfferTreeNode, { kind: "category" | "chapter" }>;

function groups(nodes: OfferTreeNode[]): Group[] {
  return nodes.filter((node): node is Group => node.kind !== "line");
}

function lineIds(nodes: OfferTreeNode[]): number[] {
  return nodes.flatMap((node) => (node.kind === "line" ? [node.line.position_item_id] : []));
}

/** Все id строк поддерева в порядке обхода. */
function allLineIds(nodes: OfferTreeNode[]): number[] {
  return nodes.flatMap((node) => (node.kind === "line" ? [node.line.position_item_id] : allLineIds(node.children)));
}

function titles(nodes: OfferTreeNode[]): string[] {
  return groups(nodes).map((node) => node.title);
}

function root(offer: OurPriceOffer, id: number | null): Group {
  const found = groups(buildOfferTree(offer)).find((node) => node.kind === "category" && node.id === id);
  if (found === undefined) throw new Error(`нет корня ${id}`);
  return found;
}

describe("buildOfferTree: скелет статей", () => {
  it("корни — по sort_order, «Нераспределённое» — последним", () => {
    const tree = buildOfferTree(ourPriceTreeOfferFixture());
    expect(titles(tree)).toEqual(["Конструктивные решения", "Отделка", "Нераспределённое"]);
  });

  it("«Нераспределённое» последнее и тогда, когда sort_order корней больше всего", () => {
    const offer = ourPriceTreeOfferFixture();
    offer.categories = offer.categories.map((c) => (c.id === 11 ? { ...c, sort_order: 900 } : c));
    expect(titles(buildOfferTree(offer))).toEqual(["Отделка", "Конструктивные решения", "Нераспределённое"]);
  });

  it("равный sort_order — по коду статьи", () => {
    const offer = ourPriceTreeOfferFixture();
    offer.categories = offer.categories.map((c) => {
      if (c.id === 11) return { ...c, sort_order: 5, code: "09" };
      if (c.id === 12) return { ...c, sort_order: 5, code: "02" };
      return c;
    });
    expect(titles(buildOfferTree(offer))).toEqual(["Отделка", "Конструктивные решения", "Нераспределённое"]);
  });

  it("корень → промежуточная → листья; глубины 0, 1, 2", () => {
    const top = root(ourPriceTreeOfferFixture(), 11);
    expect(top.depth).toBe(0);
    expect(titles(top.children)).toEqual(["Подземная часть"]);
    const middle = groups(top.children)[0];
    expect(middle.depth).toBe(1);
    expect(titles(middle.children)).toEqual(["Фундаментная плита", "Горизонтальные конструкции"]);
    expect(groups(middle.children).map((leaf) => leaf.depth)).toEqual([2, 2]);
  });

  it("у статьи есть код; у «Нераспределённого» его нет", () => {
    const offer = ourPriceTreeOfferFixture();
    const top = root(offer, 11);
    expect(top.kind === "category" && top.code).toBe("04");
    const unallocated = root(offer, null);
    expect(unallocated.kind === "category" && unallocated.code).toBeNull();
  });
});

describe("buildOfferTree: разделы сметы и строки", () => {
  const leaf = () => {
    const middle = groups(root(ourPriceTreeOfferFixture(), 11).children)[0];
    return groups(middle.children)[0];
  };

  it("внутри листа — разделы без корня сметы; вложенный раздел — внутри своего предка", () => {
    const stages = groups(leaf().children).filter((node) => node.kind === "chapter");
    // «Опалубка» — раздел под скрытым повтором статьи: стоит прямо у статьи, как и «этапы».
    expect(stages.map((node) => node.title)).toEqual(["1 Этап ЖК", "2 Этап БЦ", "Опалубка"]);
    expect(stages.map((node) => node.depth)).toEqual([3, 3, 3]);
    const inner = groups(stages[0].children)[0];
    expect(inner.kind).toBe("chapter");
    expect(inner.title).toBe("Ж/Б конструкции подземной части");
    expect(inner.depth).toBe(4);
    expect(lineIds(inner.children)).toEqual([1, 2]);
    expect(inner.children.map((node) => node.depth)).toEqual([5, 5]);
  });

  it("корень сметы нигде не появляется узлом", () => {
    const seen = JSON.stringify(buildOfferTree(ourPriceTreeOfferFixture()), (key, value) => (key === "line" ? undefined : value));
    expect(seen).not.toContain("Смета КП");
  });

  it("раздел, повторяющий название статьи (регистр, пробелы, двоеточие), скрыт: его строка — у статьи", () => {
    const direct = lineIds(leaf().children);
    expect(direct).toContain(4);
    expect(titles(leaf().children)).not.toContain("Фундаментная плита:");
  });

  it("скрытый раздел-повтор отдаёт своих детей родителю: «Опалубка» стоит прямо у статьи", () => {
    const formwork = groups(leaf().children).find((node) => node.title === "Опалубка");
    expect(formwork).toBeDefined();
    expect(formwork?.kind).toBe("chapter");
    expect(formwork?.depth).toBe(3);
    expect(lineIds(formwork?.children ?? [])).toEqual([8, 9]);
    expect(titles(leaf().children)).not.toContain("ФУНДАМЕНТНАЯ  плита");
  });

  it("повтор сравнивается с названием СВОЕЙ статьи: название чужой статьи раздел не прячет", () => {
    const offer = ourPriceTreeOfferFixture();
    // Раздел строки 4 в статье 31 зовётся «Отделка», как статья 12: чужой статье он не повтор и показан;
    // в самой статье 12 раздел «Отделка» (строка 6) скрыт как повтор.
    offer.lines = offer.lines.map((line) =>
      line.position_item_id === 4
        ? { ...line, chapter_path: [line.chapter_path[0], { chapter_item_id: 6099, title: "Отделка", number: null }] }
        : line
    );
    offer.node_totals.chapters["6099"] = nodeTotalsFixture(1, 0);
    const middle = groups(root(offer, 11).children)[0];
    const plate = groups(middle.children)[0];
    expect(titles(plate.children)).toContain("Отделка");
    expect(groups(root(offer, 12).children)).toEqual([]);
  });

  it("лист без своих разделов держит строки прямо под собой, в порядке ответа", () => {
    const shop = root(ourPriceTreeOfferFixture(), 12);
    expect(lineIds(shop.children)).toEqual([6]);
    expect(shop.children[0].depth).toBe(1);
  });

  it("путь из одного корня сметы — строка прямо у статьи", () => {
    const offer = ourPriceOfferFixture();
    const tree = buildOfferTree(offer);
    expect(tree).toHaveLength(1);
    expect(lineIds((tree[0] as Group).children)).toEqual([offer.lines[0].position_item_id]);
  });

  it("«Нераспределённое»: свои разделы и строки; id = null", () => {
    const unallocated = root(ourPriceTreeOfferFixture(), null);
    expect(unallocated.title).toBe("Нераспределённое");
    expect(titles(unallocated.children)).toEqual(["Прочее"]);
    expect(lineIds(groups(unallocated.children)[0].children)).toEqual([7]);
  });

  it("каждая строка ответа попадает в дерево ровно один раз", () => {
    const offer = ourPriceTreeOfferFixture();
    expect(allLineIds(buildOfferTree(offer)).sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("пустой ответ — пустое дерево", () => {
    expect(buildOfferTree({ ...ourPriceTreeOfferFixture(), lines: [] })).toEqual([]);
  });
});

describe("buildOfferTree: итоги узлов — из ответа, текстом", () => {
  it("статья и раздел несут ровно ту пару, что пришла в node_totals, без пересчёта", () => {
    const offer = ourPriceTreeOfferFixture();
    const top = root(offer, 11);
    expect(top.totals).toEqual({
      lines: 7,
      no_price: 3,
      kp_total: { net: "4667321049.37", shown: "5694131880.23" },
    });
    const middle = groups(top.children)[0];
    const plate = groups(middle.children)[0];
    const stage = groups(plate.children).find((node) => node.title === "1 Этап ЖК");
    expect(stage?.totals.kp_total).toEqual({ net: "626723621.40", shown: "764602817.10" });
    expect(stage?.totals.no_price).toBe(2);
  });

  it("итоги «Нераспределённого» берутся по ключу «null»", () => {
    expect(root(ourPriceTreeOfferFixture(), null).totals).toEqual({
      lines: 1,
      no_price: 0,
      kp_total: { net: "12233054.50", shown: "14924326.49" },
    });
  });

  it("итог раздела-повтора никуда не попадает: у родителя — его собственный итог", () => {
    const offer = ourPriceTreeOfferFixture();
    const plate = groups(groups(root(offer, 11).children)[0].children)[0];
    expect(plate.totals.lines).toBe(6);
    const formwork = groups(plate.children).find((node) => node.title === "Опалубка");
    expect(formwork?.totals.lines).toBe(2);
  });

  it("узел без итога в ответе — ошибка, а не молчаливый ноль", () => {
    const offer = ourPriceTreeOfferFixture();
    delete offer.node_totals.chapters["6001"];
    expect(() => buildOfferTree(offer)).toThrow(/6001/);
  });
});

describe("pruneOfferTree: видимость строк и уровней", () => {
  const offer = ourPriceTreeOfferFixture();
  const tree = buildOfferTree(offer);
  const noPrice = (line: { state: string }) => line.state === "no_price";

  it("только строки без цены: остаются 2, 5, 9 и их пути; пустые уровни уходят", () => {
    const only = pruneOfferTree(tree, (line) => noPrice(line));
    expect(allLineIds(only).sort((a, b) => a - b)).toEqual([2, 5, 9]);
    expect(titles(only)).toEqual(["Конструктивные решения"]);
  });

  it("скрыть строки без цены: остальные на месте, раздел из одних таких строк уходит", () => {
    const hidden = pruneOfferTree(tree, (line) => !noPrice(line));
    expect(allLineIds(hidden).sort((a, b) => a - b)).toEqual([1, 3, 4, 6, 7, 8]);
    const rest = groups(groups(groups(hidden)[0].children)[0].children);
    // «Горизонтальные конструкции» (одна строка без цены) опустела и ушла целиком.
    expect(rest.map((node) => node.title)).toEqual(["Фундаментная плита"]);
  });

  it("итоги уровней при любой видимости — те же объекты ответа", () => {
    const only = pruneOfferTree(tree, (line) => noPrice(line));
    const before = groups(groups(root(offer, 11).children)[0].children)[0].totals;
    const after = groups(groups(groups(only)[0].children)[0].children)[0].totals;
    expect(after).toBe(before);
    expect(after.lines).toBe(6);
  });

  it("всё видно — дерево то же; ничего не видно — пусто", () => {
    expect(pruneOfferTree(tree, () => true)).toEqual(tree);
    expect(pruneOfferTree(tree, () => false)).toEqual([]);
  });

  it("исходное дерево не меняется", () => {
    const before = JSON.stringify(tree);
    pruneOfferTree(tree, (line) => noPrice(line));
    expect(JSON.stringify(tree)).toBe(before);
  });
});
