import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EntityCombobox } from "./EntityCombobox";

/** Клиентский режим (без `onQueryChange`): регистр, «ё»/«е», порядок, сброс запроса. */

interface Item {
  id: number;
  title: string;
}

const ITEMS: Item[] = [
  { id: 1, title: "Устройство покрытий" },
  { id: 2, title: "Система вентиляции" },
  { id: 3, title: "Ёмкости резервуаров" },
  { id: 4, title: "Подъем лифтов" },
  { id: 5, title: "Армирование" },
];

function renderCombobox(items: Item[] = ITEMS, onChange: (item: Item | null) => void = () => {}) {
  render(
    <EntityCombobox
      items={items}
      value={null}
      onChange={onChange}
      getLabel={(i) => i.title}
      placeholder="Выбрать"
      searchPlaceholder="Найти…"
      emptyText="Ничего не найдено"
    />
  );
}

async function open() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox"));
  return { user, search: await screen.findByPlaceholderText("Найти…") };
}

function shownTitles() {
  return screen.getAllByRole("option").map((o) => o.textContent);
}

describe("EntityCombobox — клиентский поиск", () => {
  it("запрос с заглавной буквы находит подпись с заглавной", async () => {
    renderCombobox();
    const { user, search } = await open();

    await user.type(search, "Сист");

    expect(shownTitles()).toEqual(["Система вентиляции"]);
  });

  it("запрос заглавными буквами находит подпись в любом месте", async () => {
    renderCombobox();
    const { user, search } = await open();

    await user.type(search, "ВЕНТИЛЯЦ");

    expect(shownTitles()).toEqual(["Система вентиляции"]);
  });

  it("«ё» в запросе находит «е» в подписи", async () => {
    renderCombobox();
    const { user, search } = await open();

    await user.type(search, "подъём");

    expect(shownTitles()).toEqual(["Подъем лифтов"]);
  });

  it("«е» в запросе находит «ё» в подписи, в том числе заглавную", async () => {
    renderCombobox();
    const { user, search } = await open();

    await user.type(search, "емкости");

    expect(shownTitles()).toEqual(["Ёмкости резервуаров"]);
  });

  it("варианты приходят вразнобой — показаны по алфавиту (русская сортировка, «Ё» после «Е»)", async () => {
    renderCombobox();
    await open();

    expect(shownTitles()).toEqual([
      "Армирование",
      "Ёмкости резервуаров",
      "Подъем лифтов",
      "Система вентиляции",
      "Устройство покрытий",
    ]);
  });

  it("фильтр сохраняет алфавитный порядок", async () => {
    renderCombobox();
    const { user, search } = await open();

    await user.type(search, "и");

    expect(shownTitles()).toEqual([
      "Армирование",
      "Ёмкости резервуаров",
      "Подъем лифтов",
      "Система вентиляции",
      "Устройство покрытий",
    ]);
    await user.clear(search);
    await user.type(search, "ем");
    expect(shownTitles()).toEqual(["Ёмкости резервуаров", "Подъем лифтов", "Система вентиляции"]);
  });

  it("выбор варианта сбрасывает запрос: следующее открытие показывает весь список", async () => {
    renderCombobox();
    const { user, search } = await open();
    await user.type(search, "арм");
    await user.click(await screen.findByRole("option", { name: "Армирование" }));

    await user.click(screen.getByRole("combobox"));

    expect(await screen.findByPlaceholderText("Найти…")).toHaveValue("");
    expect(screen.getAllByRole("option")).toHaveLength(5);
  });
});

describe("EntityCombobox — серверный поиск", () => {
  it("порядок выдачи сервера не меняется: сортировка только у клиентского списка", async () => {
    render(
      <EntityCombobox
        items={ITEMS}
        value={null}
        onChange={() => {}}
        getLabel={(i) => i.title}
        placeholder="Выбрать"
        searchPlaceholder="Найти…"
        emptyText="Ничего не найдено"
        onQueryChange={() => {}}
      />
    );
    await open();

    expect(shownTitles()).toEqual(ITEMS.map((i) => i.title));
  });
});

/**
 * Длинная подпись: вид (многоточие в кнопке, перенос в списке, всё внутри окна) доказывают
 * снимки стенда при 1280 и 1920; здесь фиксируются классы, которые его дают.
 */
describe("EntityCombobox — длинная подпись", () => {
  const LONG = "Закладные и отделочные элементы из нержавеющей стали и люки";

  it("кнопка не раздвигается: min-w-0, подпись обрезана truncate, полный текст в title", () => {
    render(
      <EntityCombobox
        items={[{ id: 1, title: LONG }]}
        value={1}
        onChange={() => {}}
        getLabel={(i) => i.title}
        placeholder="Выбрать"
        searchPlaceholder="Найти…"
        emptyText="Ничего не найдено"
      />
    );

    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveClass("min-w-0");
    expect(trigger).toHaveAttribute("title", LONG);
    const text = screen.getByText(LONG);
    expect(text).toHaveClass("truncate", "min-w-0");
  });

  it("без выбора title нет (плейсхолдер не дублируется подсказкой)", () => {
    renderCombobox();

    expect(screen.getByRole("combobox")).not.toHaveAttribute("title");
  });

  it("в списке длинное название переносится, а не обрезается", async () => {
    renderCombobox([{ id: 1, title: LONG }]);
    await open();

    const text = within(screen.getByRole("option")).getByText(LONG);
    expect(text).toHaveClass("whitespace-normal");
    expect(text).not.toHaveClass("truncate");
  });
});
