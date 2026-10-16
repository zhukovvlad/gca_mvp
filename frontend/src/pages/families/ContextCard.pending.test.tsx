import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ContextCard } from "@/pages/families/ContextCard";
import {
  BIG_GROUP_CONTEXT_ID,
  CONFLICT_POSITION_ITEM_IDS,
  STALE_POSITION_ITEM_ID,
  handlerState,
} from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { renderWithProviders } from "@/test/utils";

/** Состояния ожидания карточки контекста: кнопки действий, первая загрузка, полоса перечитывания. */

const ORDINARY_CONTEXT_ID = 601;
const STALE_CONTEXT_ID = 602;
const ORDINARY_TITLE = "Штукатурка стен цементно-песчаным раствором";

describe("ContextCard — ожидание действий", () => {
  it("«Подтвердить вид» в окне: кнопка занята на время запроса, ответ пришёл — окно закрыто", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/kind");
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Подтвердить вид" }));

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Подтвердить вид" })).toHaveAttribute("aria-busy", "true")
    );
    expect(within(dialog).getByRole("button", { name: "Подтвердить вид" })).toBeDisabled();

    hold.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("«Не работа»: окно закрыто сразу, а кнопка, открывающая его, занята на время запроса", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/not-work");
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    await user.click(screen.getByRole("button", { name: "Не работа" }));
    await user.click(await screen.findByRole("button", { name: "Отметить как не работу" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Не работа" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Не работа" })).toBeDisabled();
    // Соседнее действие карточки не заблокировано.
    expect(screen.getByRole("button", { name: "Подтвердить вид" })).toBeEnabled();

    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Перенести их» (строка внимания): кнопка занята на время запроса", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/stale-groups/transfer");
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    const button = await screen.findByRole("button", { name: /Перенести их/ });

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(button).toBeDisabled();

    hold.release();
    await waitFor(() => expect(button).not.toHaveAttribute("aria-busy"));
  });

  it("«Разделить выбранные»: кнопка занята, отметки группы заблокированы; «Слить контексты» не крутится", async () => {
    const user = userEvent.setup();
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/split");
    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await screen.findByText("Устройство вентиляционных каналов");
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /9 Инженерные системы/ }));
    await screen.findByText("Вентканал, узел 1");
    const groupBox = screen.getByLabelText(/Выбрать группу/);
    await user.click(groupBox);
    await waitFor(() => expect(screen.getByText(/Выбрано членств: [1-9]/)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Разделить выбранные" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByRole("button", { name: "Разделить выбранные" })).toBeDisabled();
    expect(groupBox).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByLabelText("Выбрать позицию 80001")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Слить контексты" })).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Разделить выбранные" })).not.toHaveAttribute("aria-busy")
    );
    expect(groupBox).not.toHaveAttribute("aria-disabled", "true");
  });
});

/** Ревью пачки 2: по входу на каждое действие карточки, у которого его не было. */
describe("ContextCard — ожидание остальных действий", () => {
  const CONFLICT_CONTEXT_ID = 603;
  const SYSTEM_CONTEXT_ID = 605;
  const INSUFFICIENT_DESCRIPTION_CONTEXT_ID = 604;
  const busy = (button: HTMLElement) => {
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
  };

  async function openOrdinaryMemberships(user: ReturnType<typeof userEvent.setup>) {
    const ordinary = handlerState.semanticContexts.find((c) => c.id === ORDINARY_CONTEXT_ID)!;
    ordinary.bucket_contexts = [{ id: 749, is_default: false, archived_at: null, member_count: 0 }, ...ordinary.bucket_contexts];
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
  }

  it("«Перенести выбранные»: кнопка занята, отметки позиций заблокированы; «Разделить выбранные» не крутится", async () => {
    const user = userEvent.setup();
    await openOrdinaryMemberships(user);
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await screen.findByText("Штукатурка стен, ось Б-В");
    await user.click(screen.getByLabelText("Выбрать позицию 71002"));
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: "контекст #749" }));
    const hold = holdRequests("post", "/api/v1/semantic/members/move");

    await user.click(screen.getByRole("button", { name: "Перенести выбранные" }));

    await waitFor(() => busy(screen.getByRole("button", { name: "Перенести выбранные" })));
    expect(screen.getByLabelText("Выбрать позицию 71001")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Разделить выбранные" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Разделить выбранные" })).not.toHaveAttribute("aria-busy");

    hold.release();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Перенести выбранные" })).not.toHaveAttribute("aria-busy")
    );
  });

  it("«Разделить выбранные» при выбранной цели переноса: «Перенести выбранные» заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    await openOrdinaryMemberships(user);
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await screen.findByText("Штукатурка стен, ось Б-В");
    await user.click(screen.getByLabelText("Выбрать позицию 71002"));
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: "контекст #749" }));
    expect(screen.getByRole("button", { name: "Перенести выбранные" })).toBeEnabled();
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/split");

    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));

    await waitFor(() => busy(screen.getByRole("button", { name: "Разделить выбранные" })));
    expect(screen.getByRole("button", { name: "Перенести выбранные" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Перенести выбранные" })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Слить контексты»: кнопка занята на время запроса", async () => {
    const user = userEvent.setup();
    await openOrdinaryMemberships(user);
    await user.click(screen.getByRole("combobox", { name: "Слить контекст в целевой" }));
    await user.click(await screen.findByRole("option", { name: "контекст #749" }));
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/merge");

    await user.click(screen.getByRole("button", { name: "Слить контексты" }));

    await waitFor(() => busy(screen.getByRole("button", { name: "Слить контексты" })));
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Архивировать»: окно закрыто сразу, а кнопка, открывающая его, занята на время запроса", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await screen.findByText("Разборка временных перегородок");
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/archive");
    await user.click(screen.getByRole("button", { name: "Архивировать" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Архивировать" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await waitFor(() => busy(screen.getByRole("button", { name: "Архивировать" })));
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Снять подтверждение» в окне вида: она занята, «Подтвердить вид» заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await screen.findByText("Разборка временных перегородок");
    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const dialog = await screen.findByRole("dialog");
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/kind");

    await user.click(within(dialog).getByRole("button", { name: "Снять подтверждение" }));

    await waitFor(() => busy(within(dialog).getByRole("button", { name: "Снять подтверждение" })));
    expect(within(dialog).getByRole("button", { name: "Подтвердить вид" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Подтвердить вид" })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Подтвердить вид» в окне: «Снять подтверждение» рядом заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await screen.findByText("Разборка временных перегородок");
    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const dialog = await screen.findByRole("dialog");
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/kind");

    await user.click(within(dialog).getByRole("button", { name: "Подтвердить вид" }));

    await waitFor(() => busy(within(dialog).getByRole("button", { name: "Подтвердить вид" })));
    expect(within(dialog).getByRole("button", { name: "Снять подтверждение" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Снять подтверждение" })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Переопределить роль» в окне: кнопка занята на время запроса", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={INSUFFICIENT_DESCRIPTION_CONTEXT_ID} />);
    await screen.findByText("Светильники");
    await user.click(screen.getByRole("button", { name: "Изменить «что называет»…" }));
    const dialog = await screen.findByRole("dialog");
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/name-role");

    await user.click(within(dialog).getByRole("button", { name: "Переопределить роль" }));

    await waitFor(() => busy(within(dialog).getByRole("button", { name: "Переопределить роль" })));
    hold.release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("«Назначить семью» в окне: она занята, «Снять семью» заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    await user.click(screen.getByRole("button", { name: "Другая семья…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Семья" }));
    await user.click(await screen.findByRole("option", { name: "Кровельные работы" }));
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/family");

    await user.click(within(dialog).getByRole("button", { name: "Назначить семью" }));

    await waitFor(() => busy(within(dialog).getByRole("button", { name: "Назначить семью" })));
    expect(within(dialog).getByRole("button", { name: "Снять семью" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Снять семью" })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Снять семью» в окне: она занята на время запроса", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    await user.click(screen.getByRole("button", { name: "Другая семья…" }));
    const dialog = await screen.findByRole("dialog");
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/family");

    await user.click(within(dialog).getByRole("button", { name: "Снять семью" }));

    await waitFor(() => busy(within(dialog).getByRole("button", { name: "Снять семью" })));
    expect(within(dialog).getByRole("button", { name: "Назначить семью" })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Принять предложение переноса» в строке членства: крутится нажатая, соседняя устаревшая заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    // Вторая устаревшая строка той же группы — иначе «крутится только нажатая» не с чем сравнить.
    const staleContext = handlerState.semanticContexts.find((c) => c.id === STALE_CONTEXT_ID)!;
    const neighbourMember = staleContext.members.find((m) => m.position_item_id !== STALE_POSITION_ITEM_ID)!;
    neighbourMember.membership_state = "STALE";
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await screen.findByText("Устройство покрытий полов из линолеума");
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await screen.findByText("Устройство покрытий полов, ось 1");
    const hold = holdRequests("post", "/api/v1/semantic/members/:id/transfer");
    const name = `Принять предложение переноса для позиции ${STALE_POSITION_ITEM_ID}`;
    const neighbour = `Принять предложение переноса для позиции ${neighbourMember.position_item_id}`;
    expect(screen.getByRole("button", { name: neighbour })).toBeEnabled();

    await user.click(screen.getByRole("button", { name }));

    await waitFor(() => busy(screen.getByRole("button", { name })));
    expect(screen.getByRole("button", { name: neighbour })).toBeDisabled();
    expect(screen.getByRole("button", { name: neighbour })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Принять решение цели» в строке: крутится нажатая, соседняя строка заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await screen.findByText("Отделка потолков водоэмульсионным составом");
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await screen.findByText("Отделка потолков, ось 1");
    const hold = holdRequests("post", "/api/v1/semantic/members/accept-target-decision");
    const own = `Принять решение цели для позиции ${CONFLICT_POSITION_ITEM_IDS[0]}`;
    const neighbour = `Принять решение цели для позиции ${CONFLICT_POSITION_ITEM_IDS[1]}`;

    await user.click(screen.getByRole("button", { name: own }));

    await waitFor(() => busy(screen.getByRole("button", { name: own })));
    expect(screen.getByRole("button", { name: neighbour })).toBeDisabled();
    expect(screen.getByRole("button", { name: neighbour })).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Перенести их» при двух строках внимания: крутится нажатая, соседняя заблокирована без крутилки", async () => {
    const user = userEvent.setup();
    const stale = handlerState.semanticContexts.find((c) => c.id === STALE_CONTEXT_ID)!;
    stale.stale_groups = [
      ...stale.stale_groups,
      {
        chapter_item_ids: [],
        path: [],
        count: 2,
        target_category_id: 89,
        target_category_code: "07.02",
        target_category_title: "Слаботочные системы",
      },
    ];
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Перенести их/ })).toHaveLength(2));
    const pressedName = "Перенести их в контекст «Устройство покрытий полов из линолеума × 07.02» — раздел без раздела";
    const hold = holdRequests("post", "/api/v1/semantic/contexts/:id/stale-groups/transfer");

    await user.click(screen.getByRole("button", { name: pressedName }));

    await waitFor(() => busy(screen.getByRole("button", { name: pressedName })));
    const other = screen.getAllByRole("button", { name: /Перенести их/ }).find((b) => b.getAttribute("aria-label") !== pressedName)!;
    expect(other).toBeDisabled();
    expect(other).not.toHaveAttribute("aria-busy");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });

  it("«Перенести» в окне конфликтной строки: пока перенос идёт, повторно открытое окно занято", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await screen.findByText("Отделка потолков водоэмульсионным составом");
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    const opener = (id: number) =>
      screen.getByRole("button", { name: `Перенести в другой контекст позицию ${id}` });
    await screen.findByText("Отделка потолков, ось 2");
    await user.click(opener(CONFLICT_POSITION_ITEM_IDS[1]));
    await user.click(await screen.findByRole("combobox", { name: "Целевой контекст" }));
    await user.click(await screen.findByRole("option", { name: "контекст #760" }));
    const hold = holdRequests("post", "/api/v1/semantic/members/move");
    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await waitFor(() => expect(hold.seen.count).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(opener(CONFLICT_POSITION_ITEM_IDS[0]));

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => busy(within(dialog).getByRole("button", { name: "Перенести" })));
    hold.release();
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Перенести" })).not.toHaveAttribute("aria-busy")
    );
  });

  it("«Принять решение цели (все конфликтные)»: кнопка занята на время запроса", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    const name = "Принять решение цели (все конфликтные)";
    await screen.findByRole("button", { name });
    const hold = holdRequests("post", "/api/v1/semantic/members/accept-target-decision");

    await user.click(screen.getByRole("button", { name }));

    await waitFor(() => busy(screen.getByRole("button", { name })));
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
    // Круг 2 ревью: признак «запрос от кнопки "все"» снимается по концу запроса, крутилка не залипает.
    await waitFor(() => expect(screen.getByRole("button", { name })).not.toHaveAttribute("aria-busy"));
  });
});

describe("ContextCard — первая загрузка и перечитывание", () => {
  it("до ответа заглушки; ответ пришёл — карточка без заглушек", async () => {
    const hold = holdRequests("get", "/api/v1/semantic/contexts/:id");
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText(ORDINARY_TITLE)).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText(ORDINARY_TITLE)).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("раскрытая группа членств: до ответа заглушки, ответ пришёл — позиции без заглушек", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    const hold = holdRequests("get", "/api/v1/semantic/contexts/:id/members");

    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));

    expect(await screen.findByTestId("list-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Штукатурка стен, ось А-Б")).not.toBeInTheDocument();

    hold.release();
    expect(await screen.findByText("Штукатурка стен, ось А-Б")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();
  });

  it("раскрытая группа членств после действия: позиции на месте, полоса внутри группы видна, затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await screen.findByText("Устройство покрытий полов из линолеума");
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await screen.findByText("Устройство покрытий полов, ось 1");
    const hold = holdRequests("get", "/api/v1/semantic/contexts/:id/members");

    await user.click(
      screen.getByRole("button", { name: `Принять предложение переноса для позиции ${STALE_POSITION_ITEM_ID}` })
    );

    const bar = await screen.findByRole("progressbar", { name: "Обновление данных" });
    // Полоса — своя у группы, а не общая полоса карточки.
    expect(bar.closest("[data-slot=collapsible-content]")).not.toBeNull();
    expect(screen.getByText("Устройство покрытий полов, ось 1")).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });

  it("после действия карточка на месте, пока едут новые данные; полоса видна, затем пропадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await screen.findByText(ORDINARY_TITLE);
    expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const dialog = await screen.findByRole("dialog");
    const hold = holdRequests("get", "/api/v1/semantic/contexts/:id");

    await user.click(within(dialog).getByRole("button", { name: "Подтвердить вид" }));

    expect(await screen.findByRole("progressbar", { name: "Обновление данных" })).toBeInTheDocument();
    expect(screen.getByText(ORDINARY_TITLE)).toBeInTheDocument();
    expect(screen.queryByTestId("list-skeleton")).not.toBeInTheDocument();

    hold.release();
    await waitFor(() =>
      expect(screen.queryByRole("progressbar", { name: "Обновление данных" })).not.toBeInTheDocument()
    );
  });
});

describe("ContextCard — одна мутация на несколько кнопок", () => {
  const ALL = "Принять решение цели (все конфликтные)";

  async function openConflictGroup(user: ReturnType<typeof userEvent.setup>) {
    renderWithProviders(<ContextCard contextId={603} />);
    await screen.findByRole("button", { name: ALL });
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8/ }));
  }

  it("«Принять решение цели» в строке: «все конфликтные» не крутится, но заблокирована", async () => {
    const user = userEvent.setup();
    await openConflictGroup(user);
    const own = `Принять решение цели для позиции ${CONFLICT_POSITION_ITEM_IDS[0]}`;
    await screen.findByRole("button", { name: own });
    const hold = holdRequests("post", "/api/v1/semantic/members/accept-target-decision");

    await user.click(screen.getByRole("button", { name: own }));

    await waitFor(() => expect(screen.getByRole("button", { name: own })).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByRole("button", { name: ALL })).not.toHaveAttribute("aria-busy");
    expect(screen.getByRole("button", { name: ALL })).toBeDisabled();
    hold.release();
  });

  it("перенос из окна конфликтной строки: «Перенести выбранные» не крутится, отметки не заблокированы", async () => {
    const user = userEvent.setup();
    await openConflictGroup(user);
    const opener = `Перенести в другой контекст позицию ${CONFLICT_POSITION_ITEM_IDS[1]}`;
    await user.click(await screen.findByRole("button", { name: opener }));
    await user.click(await screen.findByRole("combobox", { name: "Целевой контекст" }));
    await user.click(await screen.findByRole("option", { name: "контекст #760" }));
    const hold = holdRequests("post", "/api/v1/semantic/members/move");

    await user.click(screen.getByRole("button", { name: "Перенести" }));

    await waitFor(() => expect(hold.seen.count).toBe(1));
    expect(screen.getByRole("button", { name: "Перенести выбранные" })).not.toHaveAttribute("aria-busy");
    expect(screen.getByLabelText(/Выбрать группу/)).not.toHaveAttribute("aria-disabled", "true");
    hold.release();
  });

  // Круг 2 ревью: признак «перенос из окна» снимается по концу запроса — следующий массовый перенос снова крутится.
  it("после переноса из окна конфликтной строки следующий «Перенести выбранные» занят и блокирует отметки", async () => {
    const user = userEvent.setup();
    await openConflictGroup(user);
    const opener = `Перенести в другой контекст позицию ${CONFLICT_POSITION_ITEM_IDS[1]}`;
    await user.click(await screen.findByRole("button", { name: opener }));
    await user.click(await screen.findByRole("combobox", { name: "Целевой контекст" }));
    await user.click(await screen.findByRole("option", { name: "контекст #760" }));
    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Первый перенос завершён: повторно открытое окно больше не занято (мутация не в пути), окно закрыто.
    await user.click(
      screen.getByRole("button", { name: `Перенести в другой контекст позицию ${CONFLICT_POSITION_ITEM_IDS[0]}` })
    );
    const again = await screen.findByRole("dialog");
    await waitFor(() => expect(within(again).getByRole("button", { name: "Перенести" })).not.toHaveAttribute("aria-busy"), {
      timeout: 8000,
    });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByLabelText(`Выбрать позицию ${CONFLICT_POSITION_ITEM_IDS[0]}`));
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: "контекст #760" }));
    const hold = holdRequests("post", "/api/v1/semantic/members/move");
    await user.click(screen.getByRole("button", { name: "Перенести выбранные" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Перенести выбранные" })).toHaveAttribute("aria-busy", "true")
    );
    expect(screen.getByLabelText(/Выбрать группу/)).toHaveAttribute("aria-disabled", "true");
    hold.release();
    await waitFor(() => expect(hold.seen.count).toBe(1));
  });
});

describe("ContextCard — перенос из окна и массовый перенос делят мутацию", () => {
  const ALL = "Принять решение цели (все конфликтные)";
  const OPT = "контекст #760";

  async function startConflictMove(user: ReturnType<typeof userEvent.setup>) {
    renderWithProviders(<ContextCard contextId={603} />);
    await screen.findByRole("button", { name: ALL });
    await user.click(screen.getByRole("tab", { name: /^Членства/ }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8/ }));
    await user.click(
      await screen.findByRole("button", { name: `Перенести в другой контекст позицию ${CONFLICT_POSITION_ITEM_IDS[1]}` })
    );
    await user.click(await screen.findByRole("combobox", { name: "Целевой контекст" }));
    await user.click(await screen.findByRole("option", { name: OPT }));
    const hold = holdRequests("post", "/api/v1/semantic/members/move");
    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await waitFor(() => expect(hold.seen.count).toBe(1));
    return hold;
  }

  async function prepareBulk(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByLabelText(`Выбрать позицию ${CONFLICT_POSITION_ITEM_IDS[0]}`));
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: OPT }));
  }

  it("пока идёт перенос из окна, «Перенести выбранные» заблокирована", async () => {
    const user = userEvent.setup();
    const hold = await startConflictMove(user);

    await prepareBulk(user);

    expect(screen.getByRole("button", { name: "Перенести выбранные" })).toBeDisabled();
    hold.release();
  });

  it("после переноса из окна массовый перенос снова крутится при своём запросе", async () => {
    const user = userEvent.setup();
    const hold = await startConflictMove(user);
    hold.release();
    await waitFor(() => expect(screen.getByLabelText(/Выбрать группу/)).not.toHaveAttribute("aria-disabled", "true"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Перенести выбранные" })).not.toHaveAttribute("aria-busy"));
    const hold2 = holdRequests("post", "/api/v1/semantic/members/move");
    await prepareBulk(user);

    await user.click(screen.getByRole("button", { name: "Перенести выбранные" }));

    await waitFor(() => expect(hold2.seen.count).toBe(1));
    expect(screen.getByRole("button", { name: "Перенести выбранные" })).toHaveAttribute("aria-busy", "true");
    hold2.release();
  });
});
