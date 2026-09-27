import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AllProviders } from "@/test/utils";

import { Pager } from "./Pager";

/**
 * `Pager` (спека §2.7): props расширены НЕОБЯЗАТЕЛЬНО, чтобы без них вид
 * не менялся для пяти существующих экранов (`ContractsPage`, `MatrixPage`,
 * `ReviewPage`, `StandardsPage`, `TendersPage`) — это первая группа тестов
 * ниже. Номера страниц и выбор размера — только по явным новым props.
 */
function nav() {
  return screen.getByRole("navigation", { name: "pagination" });
}

/**
 * Видимые числа/многоточия из `<nav>` пагинации, в порядке разметки — БЕЗ
 * «назад»/«вперёд»: те тоже помечены `data-slot="pagination-link"`
 * (`PaginationPrevious`/`Next` построены на том же примитиве), их отсекает
 * их собственный `aria-label` — у номеров страниц `aria-label` нет вовсе.
 */
function pageTokens() {
  const items = within(nav()).getAllByRole("listitem");
  return items
    .map((li) => {
      const ellipsis = li.querySelector('[data-slot="pagination-ellipsis"]');
      if (ellipsis) return "…";
      const link = li.querySelector('[data-slot="pagination-link"]');
      if (!link) return null;
      const label = link.getAttribute("aria-label") ?? "";
      if (label === "Предыдущая страница" || label === "Следующая страница") return null;
      return link.textContent;
    })
    .filter((t): t is string => t !== null);
}

describe("Pager без новых props — существующий вид пяти экранов", () => {
  it("только «назад / стр. N из M / вперёд»: ни выбора размера, ни номеров страниц", () => {
    render(
      <Pager page={2} total={45} pageSize={10} onPageChange={vi.fn()} />,
      { wrapper: AllProviders }
    );

    expect(screen.getByText("2 / 5")).toBeInTheDocument();
    // `PaginationLink` рендерит `role="button"` (`Button` с `nativeButton={false}`),
    // а не `role="link"`, хотя разметка — `<a data-slot="pagination-link">`.
    expect(screen.getByRole("button", { name: "Предыдущая страница" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Следующая страница" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByText("1")).not.toBeInTheDocument();
  });

  it("total<=pageSize — компонент не рендерится вовсе (прежнее поведение)", () => {
    // Без обёртки провайдеров: `container` обязан остаться ПУСТЫМ, а не нести
    // пустую обёртку `<div class="mt-4 …">` — та на пяти экранах добавила бы
    // отступ под таблицей там, где раньше не было ничего.
    const { container } = render(
      <Pager page={1} total={5} pageSize={20} onPageChange={vi.fn()} />
    );
    expect(screen.queryByRole("navigation", { name: "pagination" })).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Pager с showPageNumbers — последовательности номеров", () => {
  it("5 страниц, текущая 1 → 1, 2, …, 5", () => {
    render(
      <Pager page={1} total={50} pageSize={10} onPageChange={vi.fn()} showPageNumbers />,
      { wrapper: AllProviders }
    );
    expect(pageTokens()).toEqual(["1", "2", "…", "5"]);
  });

  it("5 страниц, текущая 3 → 1, 2, 3, 4, 5", () => {
    render(
      <Pager page={3} total={50} pageSize={10} onPageChange={vi.fn()} showPageNumbers />,
      { wrapper: AllProviders }
    );
    expect(pageTokens()).toEqual(["1", "2", "3", "4", "5"]);
  });

  it("12 страниц, текущая 6 → 1, …, 5, 6, 7, …, 12", () => {
    render(
      <Pager page={6} total={120} pageSize={10} onPageChange={vi.fn()} showPageNumbers />,
      { wrapper: AllProviders }
    );
    expect(pageTokens()).toEqual(["1", "…", "5", "6", "7", "…", "12"]);
  });

  it("клик по номеру страницы вызывает onPageChange с этим номером", async () => {
    const user = userEvent.setup();
    const onPageChange = vi.fn();
    render(
      <Pager page={1} total={50} pageSize={10} onPageChange={onPageChange} showPageNumbers />,
      { wrapper: AllProviders }
    );

    await user.click(screen.getByRole("button", { name: "2" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("текущая страница помечена aria-current=page, остальные — нет", () => {
    render(
      <Pager page={3} total={50} pageSize={10} onPageChange={vi.fn()} showPageNumbers />,
      { wrapper: AllProviders }
    );
    expect(screen.getByRole("button", { name: "3" })).toHaveAttribute("aria-current", "page");
    for (const other of ["1", "2", "4", "5"]) {
      expect(screen.getByRole("button", { name: other })).not.toHaveAttribute("aria-current");
    }
  });

  it("клик по ТЕКУЩЕЙ странице onPageChange не вызывает", async () => {
    const user = userEvent.setup();
    const onPageChange = vi.fn();
    render(
      <Pager page={3} total={50} pageSize={10} onPageChange={onPageChange} showPageNumbers />,
      { wrapper: AllProviders }
    );

    await user.click(screen.getByRole("button", { name: "3" }));
    expect(onPageChange).not.toHaveBeenCalled();
  });
});

describe("Pager с onPageSizeChange — выбор размера страницы", () => {
  it("выбор размера вызывает onPageSizeChange числом из pageSizeOptions", async () => {
    const user = userEvent.setup();
    const onPageSizeChange = vi.fn();
    render(
      <Pager
        page={1}
        total={45}
        pageSize={10}
        onPageChange={vi.fn()}
        onPageSizeChange={onPageSizeChange}
      />,
      { wrapper: AllProviders }
    );

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("50"));

    expect(onPageSizeChange).toHaveBeenCalledWith(50);
  });

  it("на единственной странице выбор размера остаётся виден (иначе выбравший 100 не вернётся к 10)", () => {
    render(
      <Pager
        page={1}
        total={5}
        pageSize={20}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />,
      { wrapper: AllProviders }
    );

    expect(screen.getByRole("combobox", { name: "На странице:" })).toBeInTheDocument();
    // Вытесняемое состояние той же ветки: переключателя страниц на единственной
    // странице нет — показывать нечего (докстрока `Pager`).
    expect(screen.queryByRole("navigation", { name: "pagination" })).not.toBeInTheDocument();
    expect(screen.queryByText("1 / 1")).not.toBeInTheDocument();
  });

  it("поле выбора показывает текущий размер страницы", () => {
    render(
      <Pager
        page={1}
        total={45}
        pageSize={20}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />,
      { wrapper: AllProviders }
    );
    expect(screen.getByRole("combobox", { name: "На странице:" })).toHaveTextContent("20");
  });

  it("умолчание pageSizeOptions — ровно 10 / 20 / 50 / 100 (спека §2.7)", async () => {
    const user = userEvent.setup();
    render(
      <Pager
        page={1}
        total={45}
        pageSize={10}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />,
      { wrapper: AllProviders }
    );

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["10", "20", "50", "100"]);
  });

  it("два Pager с выбором размера на одной странице — у каждой подписи свой триггер", () => {
    // Экран «Семьи и контексты» держит два списка; жёсткий `id` давал бы дубль,
    // и `<label for>` второго указывал бы на триггер первого.
    render(
      <>
        <Pager page={1} total={45} pageSize={10} onPageChange={vi.fn()} onPageSizeChange={vi.fn()} />
        <Pager page={1} total={45} pageSize={20} onPageChange={vi.fn()} onPageSizeChange={vi.fn()} />
      </>,
      { wrapper: AllProviders }
    );

    const triggers = screen.getAllByRole("combobox", { name: "На странице:" });
    expect(triggers).toHaveLength(2);
    expect(triggers[0].id).not.toBe("");
    expect(triggers[0].id).not.toBe(triggers[1].id);
    // Каждая подпись называет СВОЙ триггер: доступное имя берётся из своей
    // `<label for>`, а текст значения у триггеров разный (10 и 20).
    expect(triggers[0]).toHaveTextContent("10");
    expect(triggers[1]).toHaveTextContent("20");
    const labels = screen.getAllByText("На странице:");
    expect(labels.map((l) => l.getAttribute("for"))).toEqual(triggers.map((t) => t.id));
  });

  it("свой pageSizeOptions заменяет умолчание [10,20,50,100]", async () => {
    const user = userEvent.setup();
    render(
      <Pager
        page={1}
        total={45}
        pageSize={5}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
        pageSizeOptions={[5, 15]}
      />,
      { wrapper: AllProviders }
    );

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    expect(await screen.findByText("15")).toBeInTheDocument();
    expect(screen.queryByText("50")).not.toBeInTheDocument();
  });
});
