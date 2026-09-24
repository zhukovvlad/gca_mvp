import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ContextCard } from "@/pages/families/ContextCard";
import {
  CONFLICT_POSITION_ITEM_IDS,
  STALE_POSITION_ITEM_ID,
  handlerState,
} from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * Карточка контекста и операции над ним (спека §2.10) — членства поштучно,
 * `backend/crud/semantic.py::context_card`. Каждый тест
 * открывает СВОЙ контекст фикстуры
 * (`src/test/handlers.ts::initialSemanticContexts`) — состояние, которое он
 * проверяет, названо в id.
 */

const ORDINARY_CONTEXT_ID = 601;
const STALE_CONTEXT_ID = 602;
const CONFLICT_CONTEXT_ID = 603;
const INSUFFICIENT_DESCRIPTION_CONTEXT_ID = 604;
const SYSTEM_CONTEXT_ID = 605;
const ARCHIVED_CONTEXT_ID = 606;

describe("ContextCard", () => {
  it("без выбранного контекста показывает заглушку", () => {
    renderWithProviders(<ContextCard contextId={null} />);
    expect(screen.getByText("Контекст не выбран")).toBeInTheDocument();
  });

  it("обычный контекст: написание, семья по названию, число членств и строки членств", async () => {
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    expect(screen.getByText("Семья работ №1")).toBeInTheDocument();
    expect(screen.getByText("Членств: 3")).toBeInTheDocument();
    expect(screen.getByText("Штукатурка стен, ось А-Б")).toBeInTheDocument();
    expect(screen.getByText("Штукатурка стен, ось В-Г")).toBeInTheDocument();
    expect(screen.queryByText(/список обрезан/)).not.toBeInTheDocument();
  });

  it("обрезанный сервером список членств называет это подписью с числами", async () => {
    const ordinary = handlerState.semanticContexts.find((c) => c.id === ORDINARY_CONTEXT_ID)!;
    ordinary.members_truncated = true;
    ordinary.member_count = 700;
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    expect(
      await screen.findByText(
        `Показаны первые ${ordinary.members.length} из 700 членств — список обрезан.`
      )
    ).toBeInTheDocument();
  });

  it("insufficient_description: подпись сравнимости и подпись семьи называют причину словами", async () => {
    renderWithProviders(<ContextCard contextId={INSUFFICIENT_DESCRIPTION_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Светильники")).toBeInTheDocument());

    expect(
      screen.getByText("состав не описан — сравнение ставок не производится")
    ).toBeInTheDocument();
    expect(
      screen.getByText("семья не назначена, потому что состав не описан")
    ).toBeInTheDocument();
    // Другая подпись семьи здесь появиться не должна — это ДРУГОЙ факт.
    expect(screen.queryByText("нет семьи")).not.toBeInTheDocument();
  });

  it("устаревшая строка несёт ТОЛЬКО действие переноса, без действий конфликта", async () => {
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов, ось 1")
      ).toBeInTheDocument()
    );
    const staleRow = screen
      .getByText("Устройство покрытий полов, ось 1")
      .closest("tr")!;
    expect(
      within(staleRow).getByRole("button", { name: /Принять предложение переноса/ })
    ).toBeInTheDocument();
    expect(
      within(staleRow).queryByRole("button", { name: /Принять решение цели/ })
    ).not.toBeInTheDocument();
    expect(
      within(staleRow).queryByRole("button", { name: /Перенести в другой контекст/ })
    ).not.toBeInTheDocument();
  });

  it("конфликтная строка несёт ДВА действия конфликта, без действия переноса устаревшего", async () => {
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков, ось 1")).toBeInTheDocument()
    );
    const conflictRow = screen.getByText("Отделка потолков, ось 1").closest("tr")!;
    expect(
      within(conflictRow).getByRole("button", { name: /Принять решение цели/ })
    ).toBeInTheDocument();
    expect(
      within(conflictRow).getByRole("button", { name: /Перенести в другой контекст/ })
    ).toBeInTheDocument();
    expect(
      within(conflictRow).queryByRole("button", { name: /Принять предложение переноса/ })
    ).not.toBeInTheDocument();
    // Конфликт называет, ЧЕЙ контекст разошёлся, а не только факт расхождения.
    expect(within(conflictRow).getByText(/с контекстом 601/)).toBeInTheDocument();
  });

  it("устаревшее членство: перенос доходит до сервера с expected_category_id из предложения", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов, ось 1")
      ).toBeInTheDocument()
    );

    await user.click(
      screen.getByRole("button", {
        name: `Принять предложение переноса для позиции ${STALE_POSITION_ITEM_ID}`,
      })
    );

    await waitFor(() =>
      expect(handlerState.lastAcceptTransferRequest).toEqual({
        positionItemId: STALE_POSITION_ITEM_ID,
        body: { expected_category_id: 88 },
      })
    );
  });

  it("конфликт: «принять решение цели» доходит до сервера с id ровно ЭТОЙ строки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков, ось 1")).toBeInTheDocument()
    );

    await user.click(
      screen.getByRole("button", {
        name: `Принять решение цели для позиции ${CONFLICT_POSITION_ITEM_IDS[0]}`,
      })
    );

    await waitFor(() =>
      expect(handlerState.lastAcceptTargetDecisionRequest).toEqual([
        CONFLICT_POSITION_ITEM_IDS[0],
      ])
    );
  });

  it("конфликт: «перенести в другой контекст» открывает форму и доходит до сервера с id ровно ЭТОЙ строки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков, ось 2")).toBeInTheDocument()
    );

    await user.click(
      screen.getByRole("button", {
        name: `Перенести в другой контекст позицию ${CONFLICT_POSITION_ITEM_IDS[1]}`,
      })
    );
    // Целевой контекст — выбор из живых соседей по корзине (П6), не ввод id:
    // фикстура контекста 603 несёт ровно одного живого соседа, id 760.
    await user.click(await screen.findByRole("combobox", { name: "Целевой контекст" }));
    await user.click(await screen.findByText("контекст #760"));
    await user.type(screen.getByLabelText("Причина"), "решение оператора");
    await user.click(screen.getByRole("button", { name: "Перенести" }));

    await waitFor(() =>
      expect(handlerState.lastMoveMembersRequest).toEqual({
        position_item_ids: [CONFLICT_POSITION_ITEM_IDS[1]],
        target_context_id: 760,
        reason: "решение оператора",
      })
    );
  });

  it("разделить/перенести выбранные — по чекбоксам строк, а не по вводу id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен, ось А-Б")).toBeInTheDocument()
    );

    await user.click(screen.getByLabelText("Выбрать позицию 71001"));
    await user.click(screen.getByLabelText("Выбрать позицию 71003"));
    expect(screen.getByText("Выбрано членств: 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));
    await waitFor(() =>
      expect(handlerState.lastSplitContextRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { position_item_ids: [71001, 71003], rule: null },
      })
    );
    // Отметки чекбоксов не переживают успешное разделение (ревью задачи 13, П5):
    // «Выбрано членств» возвращается к нулю, а не ссылается на ушедшие членства.
    await waitFor(() => expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument());
  });

  it("восстановления архивного контекста на экране нет", async () => {
    renderWithProviders(<ContextCard contextId={ARCHIVED_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Гидроизоляция фундамента (снят)")).toBeInTheDocument()
    );
    expect(screen.queryByText(/восстанов/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Архивировать" })).toBeDisabled();
    // Утверждение о НАБОРЕ действий карточки (план задачи 13): поиск по слову
    // «восстанов» пропустил бы кнопку «Разархивировать» или «Вернуть из
    // архива», поэтому набор кнопок архивной карточки сверяется целиком.
    const buttonNames = screen
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent?.trim() ?? "");
    expect(buttonNames.sort()).toEqual(
      [
        "Разделить выбранные",
        "Перенести выбранные",
        "Подтвердить вид",
        "Переопределить роль",
        "Назначить семью",
        "Снять семью",
        "Слить контексты",
        "Архивировать",
      ].sort()
    );
  });

  it("подтверждение вида уходит с выбранным видом — путь переопределения исключений правила вида", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен, ось А-Б")).toBeInTheDocument()
    );

    await user.click(screen.getByRole("combobox", { name: "Вид работы" }));
    await user.click(await screen.findByRole("option", { name: "SYSTEM" }));
    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));

    await waitFor(() =>
      expect(handlerState.lastConfirmKindRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { kind: "SYSTEM" },
      })
    );
  });

  it("подтверждение вида БЕЗ выбора уходит с ТЕКУЩИМ видом карточки, не с WORK по умолчанию", async () => {
    const user = userEvent.setup();
    // Контекст 605 несёт SYSTEM — если бы селект открывался на захардкоженном
    // "WORK", это действие молча переписало бы системный контекст (ревью
    // задачи 13, П1).
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));

    await waitFor(() =>
      expect(handlerState.lastConfirmKindRequest).toEqual({
        contextId: SYSTEM_CONTEXT_ID,
        body: { kind: "SYSTEM" },
      })
    );
  });

  it("переопределение роли БЕЗ выбора уходит с ТЕКУЩЕЙ ролью карточки, не с WORK по умолчанию", async () => {
    const user = userEvent.setup();
    // Контекст 604 несёт GENERIC_WORK.
    renderWithProviders(<ContextCard contextId={INSUFFICIENT_DESCRIPTION_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Светильники")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Переопределить роль" }));

    await waitFor(() =>
      expect(handlerState.lastSetNameRoleRequest).toEqual({
        contextId: INSUFFICIENT_DESCRIPTION_CONTEXT_ID,
        body: { role: "GENERIC_WORK" },
      })
    );
  });

  it("карточка показывает источник назначения семьи рядом с самой семьёй", async () => {
    // Контекст 601 несёт family_source: "manual".
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Семья работ №1")).toBeInTheDocument());
    expect(screen.getByText(/назначена вручную/)).toBeInTheDocument();
  });

  it("разделить выбранные с правилом отправляет rule, а без правила — rule: null (явный выбор, а не подразумеваемый)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен, ось А-Б")).toBeInTheDocument()
    );

    await user.click(screen.getByLabelText("Выбрать позицию 71001"));

    await user.click(screen.getByRole("combobox", { name: "Разделить с правилом или без" }));
    await user.click(await screen.findByText("С правилом"));
    await user.type(screen.getByLabelText("Значение правила"), "Стены");
    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));

    await waitFor(() =>
      expect(handlerState.lastSplitContextRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: {
          position_item_ids: [71001],
          rule: { kind: "nearest_chapter_equals", value: "Стены" },
        },
      })
    );
  });

  it("перенести выбранные — целевой контекст выбирается из живых соседей по корзине, а не вводится id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен, ось А-Б")).toBeInTheDocument()
    );

    await user.click(screen.getByLabelText("Выбрать позицию 71002"));
    // Фикстура контекста 601 несёт ровно одного живого соседа, id 750.
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByText("контекст #750"));
    await user.type(screen.getByLabelText("Причина переноса выбранных"), "перенос оператором");
    await user.click(screen.getByRole("button", { name: "Перенести выбранные" }));

    await waitFor(() =>
      expect(handlerState.lastMoveMembersRequest).toEqual({
        position_item_ids: [71002],
        target_context_id: 750,
        reason: "перенос оператором",
      })
    );
  });

  it("слить контексты — цель выбирается из живых соседей по корзине, а не вводится id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен, ось А-Б")).toBeInTheDocument()
    );

    await user.click(screen.getByRole("combobox", { name: "Слить контекст в целевой" }));
    await user.click(await screen.findByText("контекст #750"));
    await user.click(screen.getByRole("button", { name: "Слить контексты" }));

    await waitFor(() =>
      expect(handlerState.lastMergeContextRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        targetContextId: 750,
      })
    );
  });
});
