import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState, schemaParameter, schemaValue } from "@/test/handlers";
import {
  JOURNAL_ADMIN_EMAIL,
  JOURNAL_BARE_LOCK_SPLIT_ID,
  JOURNAL_COMPOSITE_ID,
  JOURNAL_EQUATED_ID,
  JOURNAL_LOCKED_SPLIT_ID,
  JOURNAL_RESTORED_EQUATED_ID,
  JOURNAL_SPLIT_ID,
  JOURNAL_UNDONE_COMPOSITE_ID,
  JOURNAL_VALUE_ADDED_ID,
} from "@/test/journalFixtures";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { JournalTab } from "./JournalTab";

/**
 * Вкладка «Журнал ИИ» (спека упрощения каталога §2.7): лента по дням с отборами и карточка записи
 * справа. Обработчики MSW меняют фикстуру, как менял бы сервер, поэтому состояние после действия
 * проверяется перечитанным экраном, а запросы — по журналу `handlerState`.
 */

const LATER = { timeout: 8000 };

function renderTab(entryId?: number) {
  return renderWithProviders(<JournalTab />, {
    initialRoute: entryId === undefined ? "/families?tab=journal" : `/families?tab=journal&entry=${entryId}`,
  });
}

function rowOf(id: number): HTMLElement {
  const row = document.querySelector<HTMLElement>(`[data-journal-id="${id}"]`);
  if (row === null) throw new Error(`нет строки записи ${id}`);
  return row;
}

function rowIds(): number[] {
  return [...document.querySelectorAll<HTMLElement>("[data-journal-id]")].map((row) =>
    Number(row.dataset.journalId)
  );
}

async function listed() {
  await waitFor(() => expect(rowIds().length).toBeGreaterThan(0), LATER);
}

async function open(user: ReturnType<typeof userEvent.setup>, id: number) {
  await listed();
  await user.click(rowOf(id));
  return screen.findByRole("region", { name: "Запись журнала" }, LATER);
}

function lastRequest(): URLSearchParams {
  return new URLSearchParams(handlerState.journalRequests.at(-1));
}

describe("JournalTab: лента", () => {
  it("печатает записи по дням, с меткой вида словами", async () => {
    renderTab();
    await listed();

    expect(screen.getByText("11 сентября 2026")).toBeInTheDocument();
    expect(screen.getByText("10 сентября 2026")).toBeInTheDocument();
    expect(rowIds()).toHaveLength(8);
    expect(within(rowOf(JOURNAL_VALUE_ADDED_ID)).getByText("Новое значение")).toBeInTheDocument();
    expect(within(rowOf(JOURNAL_SPLIT_ID)).getByText("Деление")).toBeInTheDocument();
    expect(within(rowOf(JOURNAL_COMPOSITE_ID)).getByText("Склейка → составное")).toBeInTheDocument();
    expect(within(rowOf(JOURNAL_EQUATED_ID)).getByText("Приравнено")).toBeInTheDocument();
  });

  it("строка пересказывает запись: части деления, склейка, приравнивание", async () => {
    renderTab();
    await listed();

    expect(rowOf(JOURNAL_SPLIT_ID)).toHaveTextContent(
      "Контекст «Устройство кровли» разделён на 2: Скатная кровля, Плоская кровля"
    );
    expect(rowOf(JOURNAL_COMPOSITE_ID)).toHaveTextContent("«профнастил, металлочерепица» → «профнастил», «металлочерепица»");
    expect(rowOf(JOURNAL_EQUATED_ID)).toHaveTextContent("«профлист» = «профнастил» в «Материал»");
    expect(rowOf(JOURNAL_VALUE_ADDED_ID)).toHaveTextContent("Добавлено «металло-черепица» в «Материал»");
    expect(rowOf(JOURNAL_SPLIT_ID)).toHaveTextContent("Кровельные работы");
  });

  it("вид — переключатель с числами из ответа; выбор шлёт kind, числа остаются", async () => {
    const user = userEvent.setup();
    renderTab();
    await listed();

    expect(screen.getByRole("button", { name: "Все 8" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Деления 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Склейки 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Новые значения 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Приравнено 2" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Деления 3" }));

    await waitFor(() => expect(lastRequest().get("kind")).toBe("split"), LATER);
    await waitFor(() => expect(rowIds()).toHaveLength(3), LATER);
    expect(screen.getByRole("button", { name: "Приравнено 2" })).toBeInTheDocument();
  });

  it("состояние — выбор из списка, уходит параметром state", async () => {
    const user = userEvent.setup();
    renderTab();
    await listed();

    await user.click(screen.getByRole("combobox", { name: "Состояние" }));
    await user.click(await screen.findByRole("option", { name: "Отменённые" }));

    await waitFor(() => expect(lastRequest().get("state")).toBe("undone"), LATER);
    await waitFor(() => expect(rowIds().sort()).toEqual([JOURNAL_RESTORED_EQUATED_ID, JOURNAL_UNDONE_COMPOSITE_ID]), LATER);
  });

  it("семья — выбор из списка семей, уходит параметром family_id", async () => {
    const user = userEvent.setup();
    renderTab();
    await listed();

    await user.click(screen.getByRole("combobox", { name: "Семья" }));
    await user.click(await screen.findByRole("option", { name: /^Устройство покрытий полов/ }));

    await waitFor(() => expect(lastRequest().get("family_id")).toBe("2"), LATER);
    await waitFor(() => expect(rowIds().sort()).toEqual([JOURNAL_LOCKED_SPLIT_ID, JOURNAL_BARE_LOCK_SPLIT_ID]), LATER);
  });

  it("поиск уходит с задержкой и одним запросом на набранный текст", async () => {
    const user = userEvent.setup();
    renderTab();
    await listed();
    const before = handlerState.journalRequests.length;

    await user.type(screen.getByRole("textbox", { name: "Поиск по журналу" }), "профлист");
    // Пока человек печатает, запросов с q нет.
    expect(handlerState.journalRequests.slice(before).some((s) => new URLSearchParams(s).has("q"))).toBe(false);

    await waitFor(() => expect(lastRequest().get("q")).toBe("профлист"), LATER);
    expect(handlerState.journalRequests.filter((s) => new URLSearchParams(s).has("q"))).toHaveLength(1);
    await waitFor(() => expect(rowIds()).toEqual([JOURNAL_EQUATED_ID]), LATER);
  });

  it("поиск без совпадений — «Записей с такими отборами нет»", async () => {
    const user = userEvent.setup();
    renderTab();
    await listed();

    await user.type(screen.getByRole("textbox", { name: "Поиск по журналу" }), "нет такого текста");

    expect(await screen.findByText("Записей с такими отборами нет", {}, LATER)).toBeInTheDocument();
  });

  it("вторая страница уходит со смещением, первая — без пересечений", async () => {
    const user = userEvent.setup();
    const template = handlerState.journal[0];
    handlerState.journal = Array.from({ length: 25 }, (_, i) => ({
      ...template,
      id: 1000 + i,
      created_at: `2026-11-09T${String(10 + (i % 10)).padStart(2, "0")}:00:${String(i).padStart(2, "0")}+00:00`,
    }));
    renderTab();
    await waitFor(() => expect(rowIds()).toHaveLength(20), LATER);

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));

    await waitFor(() => expect(lastRequest().get("offset")).toBe("20"), LATER);
    await waitFor(() => expect(rowIds()).toHaveLength(5), LATER);
  });
});

describe("JournalTab: деление", () => {
  it("карточка печатает части с нынешним числом строк", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_SPLIT_ID);

    expect(await within(card).findByRole("heading", { name: "Контекст «Устройство кровли» разделён" }, LATER)).toBeInTheDocument();
    const first = within(card).getByText("Скатная кровля").closest("li")!;
    expect(first).toHaveTextContent("14");
    const second = within(card).getByText("Плоская кровля").closest("li")!;
    expect(second).toHaveTextContent("9");
  });

  it("«Отменить — вернуть как было» спрашивает с нынешними числами и отменяет", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_SPLIT_ID);

    await user.click(await within(card).findByRole("button", { name: "Отменить — вернуть как было" }, LATER));
    const dialog = await screen.findByRole("alertdialog");
    await waitForDialogFocus("alertdialog");

    // 14 + 9 = 23: все нынешние строки частей возвращаются в исходный контекст.
    expect(dialog).toHaveTextContent("«Скатная кровля» — 14 строк");
    expect(dialog).toHaveTextContent("«Плоская кровля» — 9 строк");
    expect(dialog).toHaveTextContent("23 строк вернутся в один контекст «Устройство кровли»");

    await user.click(within(dialog).getByRole("button", { name: "Вернуть как было" }));

    await waitFor(() => expect(handlerState.journalActions).toEqual([{ id: JOURNAL_SPLIT_ID, action: "undo", body: null }]), LATER);
    // Отменённая запись остаётся в ленте зачёркнутой, а не исчезает.
    await waitFor(() => expect(rowOf(JOURNAL_SPLIT_ID).querySelector(".line-through")).not.toBeNull(), LATER);
    expect(within(rowOf(JOURNAL_SPLIT_ID)).getByText("отменено")).toBeInTheDocument();
  });

  it("«Не отменять» закрывает диалог без запроса", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_SPLIT_ID);

    await user.click(await within(card).findByRole("button", { name: "Отменить — вернуть как было" }, LATER));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Не отменять" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(handlerState.journalActions).toEqual([]);
    expect(rowOf(JOURNAL_SPLIT_ID).querySelector(".line-through")).toBeNull();
  });

  it("при замке кнопки недоступны, причина — кто, что и когда", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_LOCKED_SPLIT_ID);

    const undo = await within(card).findByRole("button", { name: "Отменить — вернуть как было" }, LATER);
    expect(undo).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Поправить" })).toBeDisabled();
    const note = within(card).getByText(/Отмена недоступна/);
    expect(note).toHaveTextContent("перенос строк");
    expect(note).toHaveTextContent(JOURNAL_ADMIN_EMAIL);
    expect(note).toHaveTextContent("10.09.2026");
  });

  it("замок без события (поля null) показывается нейтрально, без «null»", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_BARE_LOCK_SPLIT_ID);

    const undo = await within(card).findByRole("button", { name: "Отменить — вернуть как было" }, LATER);
    expect(undo).toBeDisabled();
    const note = within(card).getByText(/Отмена недоступна/);
    expect(note.textContent).not.toMatch(/null|undefined|Invalid/);
    expect(note).not.toHaveTextContent("@");
  });

  it("замок, наступивший после показа: отказ 409 несёт locked_by, экран читает код", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_SPLIT_ID);
    await within(card).findByRole("button", { name: "Отменить — вернуть как было" }, LATER);

    // Между показом и нажатием администратор перенёс строки: сервер теперь отказывает.
    const item = handlerState.journal.find((entry) => entry.id === JOURNAL_SPLIT_ID)!;
    item.state = "locked";
    item.undo = {
      available: false,
      locked_by: { event_type: "members_moved", actor_email: JOURNAL_ADMIN_EMAIL, at: "2026-09-11T08:00:00+00:00" },
    };
    await user.click(within(card).getByRole("button", { name: "Отменить — вернуть как было" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Вернуть как было" }));

    const alert = await screen.findByRole("alert", {}, LATER);
    expect(alert).toHaveTextContent("Отмена недоступна");
    expect(alert).toHaveTextContent(JOURNAL_ADMIN_EMAIL);
    expect(alert).not.toHaveTextContent("server text");
  });

  it("«Поправить» отправляет выбранный уровень; новое деление придёт отдельной записью", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_SPLIT_ID);

    await user.click(await within(card).findByRole("button", { name: "Поправить" }, LATER));
    await user.click(within(card).getByRole("combobox", { name: "Уровень разделов" }));
    await user.click(await screen.findByRole("option", { name: "2-й уровень от корня" }));
    await user.click(within(card).getByRole("button", { name: "Отменить и разделить заново" }));

    await waitFor(
      () => expect(handlerState.journalActions).toEqual([{ id: JOURNAL_SPLIT_ID, action: "resplit", body: { level: 1 } }]),
      LATER
    );
    await waitFor(() => expect(rowOf(JOURNAL_SPLIT_ID).querySelector(".line-through")).not.toBeNull(), LATER);
  });

  it("уровень, на котором делить не на что, — подпись по коду, а не текст сервера", async () => {
    const user = userEvent.setup();
    handlerState.journalRefusal = { action: "resplit", status: 409, code: "journal_resplit_no_groups" };
    renderTab();
    const card = await open(user, JOURNAL_SPLIT_ID);

    await user.click(await within(card).findByRole("button", { name: "Поправить" }, LATER));
    await user.click(within(card).getByRole("button", { name: "Отменить и разделить заново" }));

    const alert = await within(card).findByRole("alert", {}, LATER);
    expect(alert).toHaveTextContent("На этом уровне делить не на что");
    expect(alert).not.toHaveTextContent("server text");
    // Запись не изменилась: отказ ничего не отменил.
    expect(rowOf(JOURNAL_SPLIT_ID).querySelector(".line-through")).toBeNull();
  });
});

describe("JournalTab: склейка, новое значение, приравнивание", () => {
  it("склейка: было → стало, части и контексты с параметром", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_COMPOSITE_ID);

    expect(await within(card).findByRole("heading", { name: /стало составным значением из 2 частей/ }, LATER)).toBeInTheDocument();
    const was = within(card).getByTestId("journal-was");
    expect(was).toHaveTextContent("профнастил, металлочерепица");
    const now = within(card).getByTestId("journal-now");
    expect(now).toHaveTextContent("профнастил");
    expect(now).toHaveTextContent("+");
    expect(now).toHaveTextContent("металлочерепица");
    const contexts = within(card).getAllByTestId("journal-context");
    expect(contexts).toHaveLength(2);
    expect(contexts[0]).toHaveTextContent("Устройство кровли, скат");
    expect(contexts[0]).toHaveTextContent("Материал");
    expect(contexts[0]).toHaveTextContent("профнастил + металлочерепица");
  });

  it("знак между частями берётся из вида составного, а не из текста контекстов", async () => {
    const user = userEvent.setup();
    const item = handlerState.journal.find((entry) => entry.id === JOURNAL_COMPOSITE_ID)!;
    item.details = {
      contexts: [{ id: 621, title: "Устройство кровли, скат", parameter_value: "профнастил" }],
      undo_text: "профнастил, металлочерепица",
      composite_kind: "any",
    };
    renderTab();
    const card = await open(user, JOURNAL_COMPOSITE_ID);

    const now = await within(card).findByTestId("journal-now", {}, LATER);
    expect(now).toHaveTextContent("или");
    expect(now).not.toHaveTextContent("+");
  });

  it("склейка: отмена — снова простое значение, с подтверждением", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_COMPOSITE_ID);

    await user.click(await within(card).findByRole("button", { name: "Отменить — снова простое значение" }, LATER));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("2 контекста остаются на нём");
    await user.click(within(dialog).getByRole("button", { name: "Снова простое значение" }));

    await waitFor(() => expect(handlerState.journalActions).toEqual([{ id: JOURNAL_COMPOSITE_ID, action: "undo", body: null }]), LATER);
    await waitFor(() => expect(rowOf(JOURNAL_COMPOSITE_ID).querySelector(".line-through")).not.toBeNull(), LATER);
  });

  it("новое значение: «Слить с…» открывает окно слияния с этим значением источником", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_VALUE_ADDED_ID);

    const merge = await within(card).findByRole("button", { name: /^Слить с/ }, LATER);
    // Кнопка ждёт список значений параметра: до его прихода слить нечего.
    await waitFor(() => expect(merge).toBeEnabled(), LATER);
    await user.click(merge);
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    expect(within(dialog).getByRole("combobox", { name: "Параметр" })).toHaveTextContent("Материал");
    expect(within(dialog).getByRole("combobox", { name: "Источник" })).toHaveTextContent("металло-черепица");

    await user.click(within(dialog).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(
      () =>
        expect(handlerState.schemaRequests).toContainEqual({
          action: "merge",
          familyId: SCHEMA_FAMILY_ID,
          body: { parameter_id: 101, source_value_id: 1003, target_value_id: 1002 },
        }),
      LATER
    );
  });

  it("новое значение, которого уже нет в схеме: «Слить с…» недоступно, причина названа", async () => {
    const user = userEvent.setup();
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters = [schemaParameter(101, 1, "Материал", [schemaValue(1001, "профнастил"), schemaValue(1002, "металлочерепица")])];
    renderTab();
    const card = await open(user, JOURNAL_VALUE_ADDED_ID);

    expect(await within(card).findByText(/в списке параметра его больше нет/, {}, LATER)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /^Слить с/ })).toBeDisabled();
  });

  it("новое значение, у которого value null: «Слить с…» недоступно", async () => {
    const user = userEvent.setup();
    handlerState.journal.find((entry) => entry.id === JOURNAL_VALUE_ADDED_ID)!.value = null;
    renderTab();
    const card = await open(user, JOURNAL_VALUE_ADDED_ID);

    expect(await within(card).findByText(/в списке параметра его больше нет/, {}, LATER)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /^Слить с/ })).toBeDisabled();
  });

  it("состояние: триггер выбора достаточно широк для самого длинного названия", async () => {
    renderTab();
    await listed();

    expect(screen.getByRole("combobox", { name: "Состояние" }).className).toContain("w-60");
  });

  it("приравнивание: «Отвязать» — это отмена, с подтверждением", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_EQUATED_ID);

    expect(await within(card).findByRole("heading", { name: "«профлист» приравнено к «профнастил»" }, LATER)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: /^Отвязать/ }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("2 контекста перейдут с «профнастил» на него");
    await user.click(within(dialog).getByRole("button", { name: "Отвязать" }));

    await waitFor(() => expect(handlerState.journalActions).toEqual([{ id: JOURNAL_EQUATED_ID, action: "undo", body: null }]), LATER);
    await waitFor(() => expect(rowOf(JOURNAL_EQUATED_ID).querySelector(".line-through")).not.toBeNull(), LATER);
  });

  it("отказ отмены читается по коду: текст занят — своя подпись", async () => {
    const user = userEvent.setup();
    handlerState.journalRefusal = { action: "undo", status: 409, code: "journal_undo_conflict" };
    renderTab();
    const card = await open(user, JOURNAL_EQUATED_ID);

    await user.click(await within(card).findByRole("button", { name: /^Отвязать/ }, LATER));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Отвязать" }));

    const alert = await within(card).findByRole("alert", {}, LATER);
    expect(alert).toHaveTextContent("Исходный текст уже занят другим значением");
    expect(alert).not.toHaveTextContent("server text");
  });
});

describe("JournalTab: отменённые записи", () => {
  it("зачёркнута в ленте; карточка называет, кто и когда отменил; «Вернуть автоматику» снимает решение", async () => {
    const user = userEvent.setup();
    renderTab();
    await listed();
    expect(rowOf(JOURNAL_UNDONE_COMPOSITE_ID).querySelector(".line-through")).not.toBeNull();
    expect(rowOf(JOURNAL_COMPOSITE_ID).querySelector(".line-through")).toBeNull();

    const card = await open(user, JOURNAL_UNDONE_COMPOSITE_ID);
    expect(await within(card).findByText(/Отменено/, {}, LATER)).toHaveTextContent(JOURNAL_ADMIN_EMAIL);

    await user.click(within(card).getByRole("button", { name: "Вернуть автоматику" }));

    await waitFor(
      () => expect(handlerState.journalActions).toEqual([{ id: JOURNAL_UNDONE_COMPOSITE_ID, action: "restore-auto", body: null }]),
      LATER
    );
    expect(await within(card).findByText(/Автоматика возвращена/, {}, LATER)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Вернуть автоматику" })).not.toBeInTheDocument();
  });

  it("после возврата автоматики кнопки нет, остаётся подпись", async () => {
    const user = userEvent.setup();
    renderTab();
    const card = await open(user, JOURNAL_RESTORED_EQUATED_ID);

    expect(await within(card).findByText(/Автоматика возвращена/, {}, LATER)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Вернуть автоматику" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /^Отвязать/ })).not.toBeInTheDocument();
  });

  it("чтение записи ничего не пишет: открытие карточек не шлёт действий", async () => {
    const user = userEvent.setup();
    renderTab();
    await open(user, JOURNAL_SPLIT_ID);
    await user.click(rowOf(JOURNAL_COMPOSITE_ID));
    await screen.findByRole("heading", { name: /стало составным значением/ }, LATER);

    expect(handlerState.journalActions).toEqual([]);
  });
});
