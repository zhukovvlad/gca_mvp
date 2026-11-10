import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ourPriceTreeOfferFixture } from "@/test/fixtures";
import type { OurPriceOffer } from "@/types/domain";

import { OfferTree } from "./OfferTree.tsx";
import type { NoPriceMode, OfferFilter } from "./offerFilter";
import { buildOfferTree } from "./offerTree.ts";

interface Options {
  offer?: OurPriceOffer;
  vat?: "net" | "shown";
  mode?: NoPriceMode;
  filter?: OfferFilter;
  defaultOpen?: boolean;
  onOpenLine?: (id: number) => void;
}

/** Дети корня 11 «Конструктивные решения»: промежуточная статья «Подземная часть» и всё под ней. */
function renderTree(options: Options = {}) {
  const offer = options.offer ?? ourPriceTreeOfferFixture();
  const root = buildOfferTree(offer).find((node) => node.kind === "category" && node.id === 11);
  if (root === undefined || root.kind === "line") throw new Error("нет корня 11");
  return render(
    <OfferTree
      nodes={root.children}
      trail={[root.title]}
      vat={options.vat ?? "net"}
      rateClass="Класс 2"
      mode={options.mode ?? "show"}
      filter={options.filter ?? "all"}
      defaultOpen={options.defaultOpen ?? false}
      onOpenLine={options.onOpenLine ?? (() => undefined)}
    />
  );
}

const lineIds = () =>
  screen
    .queryAllByTestId(/^line-\d+$/)
    .map((row) => Number(row.getAttribute("data-testid")?.slice("line-".length)))
    .sort((a, b) => a - b);

const groupKeys = () =>
  screen.queryAllByTestId(/^node-(cat|chap)-/).map((row) => row.getAttribute("data-testid")?.slice("node-".length));

describe("свёрнутое и раскрытое", () => {
  it("по умолчанию виден только первый уровень, строк в DOM нет", () => {
    renderTree();
    expect(groupKeys()).toEqual(["cat-21"]);
    expect(lineIds()).toEqual([]);
    expect(screen.getByTestId("node-toggle-cat-21")).toHaveAttribute("aria-expanded", "false");
  });

  it("щелчок раскрывает уровень на один шаг; повторный — сворачивает", () => {
    renderTree();
    fireEvent.click(screen.getByTestId("node-toggle-cat-21"));
    expect(groupKeys()).toEqual(["cat-21", "cat-31", "cat-32"]);
    expect(lineIds()).toEqual([]);
    fireEvent.click(screen.getByTestId("node-toggle-cat-31"));
    // Дети листа — в порядке появления в смете: «1 Этап», «2 Этап», строка 4 прямо у статьи, «Опалубка».
    expect(groupKeys()).toEqual(["cat-21", "cat-31", "chap-6001", "chap-6011", "chap-6022", "cat-32"]);
    expect(lineIds()).toEqual([4]);
    fireEvent.click(screen.getByTestId("node-toggle-cat-21"));
    expect(groupKeys()).toEqual(["cat-21"]);
  });

  it("раскрыто целиком: все строки ветки на месте, вложенный раздел — внутри предка", () => {
    renderTree({ defaultOpen: true });
    expect(lineIds()).toEqual([1, 2, 3, 4, 5, 8, 9]);
    expect(groupKeys()).toEqual([
      "cat-21",
      "cat-31",
      "chap-6001",
      "chap-6002",
      "chap-6011",
      "chap-6012",
      "chap-6022",
      "cat-32",
      "chap-6001",
      "chap-6030",
    ]);
  });

  it("при раскрытом по умолчанию щелчок сворачивает именно этот уровень", () => {
    renderTree({ defaultOpen: true });
    fireEvent.click(screen.getByTestId("node-toggle-cat-32"));
    expect(lineIds()).toEqual([1, 2, 3, 4, 8, 9]);
    expect(screen.getByTestId("node-toggle-cat-21")).toHaveAttribute("aria-expanded", "true");
  });

  it("раздел сметы помечен «в смете», статья — нет", () => {
    renderTree({ defaultOpen: true });
    expect(within(screen.getAllByTestId("node-chap-6001")[0]).getByText("в смете")).toBeInTheDocument();
    expect(within(screen.getByTestId("node-cat-31")).queryByText("в смете")).toBeNull();
  });

  it("код статьи печатается, номеров сметы в таблице нет", () => {
    renderTree({ defaultOpen: true });
    expect(within(screen.getByTestId("node-cat-31")).getByText("4.1.1")).toBeInTheDocument();
    expect(screen.queryByText("Смета КП")).toBeNull();
  });
});

describe("итоги уровней — текстом из ответа", () => {
  it("число строк, «N без цены» и сумма КП; деньги — тем же форматом, что соседние суммы", () => {
    renderTree();
    expect(screen.getByTestId("node-lines-cat-21")).toHaveTextContent("7 строк");
    expect(screen.getByTestId("node-no-price-cat-21")).toHaveTextContent("3 без цены");
    expect(screen.getByTestId("node-kp-cat-21")).toHaveTextContent("КП 4 667 321 049,37 ₽");
  });

  it("в виде «с НДС» — поле shown, не пересчёт нетто", () => {
    renderTree({ vat: "shown" });
    expect(screen.getByTestId("node-kp-cat-21")).toHaveTextContent("КП 5 694 131 880,23 ₽");
  });

  it("нулевая сумма — «0,00 ₽», а не прочерк; у раздела без плохих строк счётчика «без цены» нет", () => {
    renderTree({ defaultOpen: true });
    expect(screen.getByTestId("node-kp-cat-32")).toHaveTextContent("КП 0,00 ₽");
    expect(screen.queryByTestId("node-no-price-chap-6011")).toBeNull();
  });

  it("форма числа строк: 1 строка, 2 строки, 7 строк", () => {
    renderTree({ defaultOpen: true });
    expect(screen.getByTestId("node-lines-cat-32")).toHaveTextContent("1 строка");
    expect(screen.getByTestId("node-lines-chap-6002")).toHaveTextContent("2 строки");
    expect(screen.getByTestId("node-lines-cat-21")).toHaveTextContent("7 строк");
  });

  it("база НДС неизвестна — kp_total = null: сумма «—», строки и «без цены» остаются", () => {
    const offer = ourPriceTreeOfferFixture();
    offer.node_totals.categories["21"] = { lines: 7, no_price: 3, kp_total: null };
    renderTree({ offer });
    expect(screen.getByTestId("node-kp-cat-21")).toHaveTextContent("КП —");
    expect(screen.getByTestId("node-lines-cat-21")).toHaveTextContent("7 строк");
  });

  it("вид «с НДС» без ставки (shown = null) — тоже «—»", () => {
    const offer = ourPriceTreeOfferFixture();
    offer.node_totals.categories["21"] = {
      lines: 7,
      no_price: 3,
      kp_total: { net: "4667321049.37", shown: null },
    };
    renderTree({ offer, vat: "shown" });
    expect(screen.getByTestId("node-kp-cat-21")).toHaveTextContent("КП —");
  });

  it("на нижних уровнях нет ₽/м², вилки и «статьи целиком»", () => {
    renderTree({ defaultOpen: true });
    expect(screen.queryByTestId(/^art-/)).toBeNull();
  });
});

describe("строки без цены", () => {
  it("строка в состоянии no_price красная и помечена; остальные — нет", () => {
    renderTree({ defaultOpen: true });
    expect(screen.getByTestId("line-2")).toHaveAttribute("data-no-price", "true");
    expect(screen.getByTestId("line-5")).toHaveAttribute("data-no-price", "true");
    expect(screen.getByTestId("line-1")).not.toHaveAttribute("data-no-price");
    expect(screen.getByTestId("line-2")).toHaveClass("text-danger-text");
    expect(screen.getByTestId("line-1")).not.toHaveClass("text-danger-text");
  });

  it("«скрыть»: строк без цены нет, опустевший уровень ушёл, итоги уровней прежние", () => {
    renderTree({ defaultOpen: true, mode: "hide" });
    expect(lineIds()).toEqual([1, 3, 4, 8]);
    expect(screen.queryByTestId("node-cat-32")).toBeNull();
    expect(screen.queryByTestId("node-chap-6030")).toBeNull();
    // Раздел «1 Этап ЖК» потерял строку 5, но не 2: итог по-прежнему 3 строки, 2 без цены.
    expect(screen.getAllByTestId("node-lines-chap-6001")[0]).toHaveTextContent("3 строки");
    expect(screen.getAllByTestId("node-no-price-chap-6001")[0]).toHaveTextContent("2 без цены");
    expect(screen.getByTestId("node-kp-cat-21")).toHaveTextContent("КП 4 667 321 049,37 ₽");
  });

  it("«только они»: видны лишь строки без цены и их пути; итоги те же", () => {
    renderTree({ defaultOpen: true, mode: "only" });
    expect(lineIds()).toEqual([2, 5, 9]);
    expect(groupKeys()).toEqual(["cat-21", "cat-31", "chap-6001", "chap-6002", "chap-6022", "cat-32", "chap-6001", "chap-6030"]);
    expect(screen.getByTestId("node-lines-cat-31")).toHaveTextContent("6 строк");
    expect(screen.getByTestId("node-no-price-cat-31")).toHaveTextContent("2 без цены");
  });

  it("«показывать»: все строки", () => {
    renderTree({ defaultOpen: true, mode: "show" });
    expect(lineIds()).toEqual([1, 2, 3, 4, 5, 8, 9]);
  });

  it("плитка-фильтр и режим складываются: нет строк под фильтр — подпись вместо таблицы", () => {
    renderTree({ defaultOpen: true, filter: "unit_mismatch" });
    expect(screen.getByTestId("lines-empty")).toBeInTheDocument();
    expect(groupKeys()).toEqual([]);
  });
});

describe("сортировка по столбцу выпрямляет дерево", () => {
  it("щелчок по заголовку — список без уровней, под названием строки путь из статей и разделов", () => {
    renderTree({ defaultOpen: true });
    fireEvent.click(screen.getByRole("button", { name: /Работа в КП/ }));
    expect(groupKeys()).toEqual([]);
    expect(lineIds()).toEqual([1, 2, 3, 4, 5, 8, 9]);
    expect(screen.getByTestId("path-1")).toHaveTextContent(
      "Конструктивные решения › Подземная часть › Фундаментная плита › 1 Этап ЖК › Ж/Б конструкции подземной части"
    );
    expect(screen.getByTestId("path-4")).toHaveTextContent("Конструктивные решения › Подземная часть › Фундаментная плита");
    expect(screen.getByTestId("path-4")).not.toHaveTextContent("Фундаментная плита:");
  });

  it("порядок плоского списка — по выбранному столбцу", () => {
    renderTree({ defaultOpen: true });
    fireEvent.click(screen.getByRole("button", { name: /Работа в КП/ }));
    const order = screen
      .getAllByTestId(/^line-\d+$/)
      .map((row) => row.getAttribute("data-testid")?.slice("line-".length));
    // По возрастанию названия: «Армирование», «Опалубка плиты», «Плита корпуса 8», …
    expect(order.slice(0, 3)).toEqual(["9", "8", "2"]);
  });

  it("«Вернуть дерево» возвращает уровни", () => {
    renderTree({ defaultOpen: true });
    fireEvent.click(screen.getByRole("button", { name: /Работа в КП/ }));
    fireEvent.click(screen.getByTestId("tree-restore"));
    expect(groupKeys()).toContain("cat-31");
    expect(screen.queryByTestId("tree-restore")).toBeNull();
  });

  it("пока столбец не выбран, пути под названием нет и кнопки возврата нет", () => {
    renderTree({ defaultOpen: true });
    expect(screen.queryByTestId("path-1")).toBeNull();
    expect(screen.queryByTestId("tree-restore")).toBeNull();
  });
});

describe("открытие строки", () => {
  it("щелчок по строке отдаёт её position_item_id", () => {
    const onOpenLine = vi.fn();
    renderTree({ defaultOpen: true, onOpenLine });
    fireEvent.click(screen.getByTestId("line-3"));
    expect(onOpenLine).toHaveBeenCalledWith(3);
  });

  it("щелчок по уровню строку не открывает", () => {
    const onOpenLine = vi.fn();
    renderTree({ onOpenLine });
    fireEvent.click(screen.getByTestId("node-toggle-cat-21"));
    expect(onOpenLine).not.toHaveBeenCalled();
  });
});
