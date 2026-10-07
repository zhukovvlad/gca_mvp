import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { MarkPositionDialog } from "./MarkPositionDialog";

/**
 * «Пометить написание целиком…» (спека `2026-10-02-catalog-variants-design.md` §2.11,
 * §2.12): предупреждение о будущих вхождениях, глобальная пометка `HEADER`/`TRASH`, отказ
 * `409 position_has_standards` с перечнем нормативов — диалог остаётся открытым.
 */

const POSITION_ID = 8001;
const LATER = { timeout: 8000 };

function renderDialog(onOpenChange = vi.fn()) {
  renderWithProviders(
    <MarkPositionDialog
      positionId={POSITION_ID}
      title="Штукатурка стен цементно-песчаным раствором"
      open
      onOpenChange={onOpenChange}
    />
  );
  return onOpenChange;
}

describe("MarkPositionDialog", () => {
  it("предупреждает, что пометка касается всех будущих вхождений и снимает семьи, варианты и значения", async () => {
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("всем будущим вхождениям этого написания");
    expect(dialog).toHaveTextContent("снимает семьи, варианты и значения у всех его контекстов");
  });

  it("«Пометить заголовком» шлёт HEADER и закрывает диалог", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderDialog();

    await user.click(await screen.findByRole("button", { name: "Пометить заголовком" }));

    await waitFor(
      () => expect(handlerState.positionKindRequests).toEqual([{ positionId: POSITION_ID, kind: "HEADER" }]),
      LATER
    );
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), LATER);
  });

  it("«Пометить мусором» шлёт TRASH", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: "Пометить мусором" }));

    await waitFor(
      () => expect(handlerState.positionKindRequests).toEqual([{ positionId: POSITION_ID, kind: "TRASH" }]),
      LATER
    );
  });

  it("при 409 с нормативами печатает их перечнем без кодов и остаётся открытым", async () => {
    handlerState.positionStandards[POSITION_ID] = [
      { id: 71, rate_class_id: 3, rate_class_title: "Класс Б3", valid_from: "2026-03-01", valid_to: null },
      { id: 72, rate_class_id: 2, rate_class_title: "Класс Б2", valid_from: "2025-01-01", valid_to: "2025-07-01" },
    ];
    const user = userEvent.setup();
    const onOpenChange = renderDialog();

    await user.click(await screen.findByRole("button", { name: "Пометить заголовком" }));

    const alert = await screen.findByRole("alert", {}, LATER);
    expect(alert).toHaveTextContent("У строки есть нормативы");
    expect(within(alert).getAllByRole("listitem")).toHaveLength(2);
    expect(within(alert).getByText(/Класс Б3/)).toHaveTextContent("с 01.03.2026, бессрочно");
    expect(within(alert).getByText(/Класс Б2/)).toHaveTextContent("с 01.01.2025 по 01.07.2025");
    expect(document.body.textContent).not.toContain("position_has_standards");
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пометить мусором" })).toBeEnabled();
  });

  it("другой отказ — подпись по коду, код на экран не выходит", async () => {
    handlerState.contextRefusal = { action: "position-kind", code: "position_not_position", status: 409 };
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: "Пометить заголовком" }));

    expect(
      await screen.findByText("Строка уже не ждёт решения: обновите экран.", {}, LATER)
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("position_not_position");
  });

  it("неизвестный код отказа — общая подпись", async () => {
    handlerState.contextRefusal = { action: "position-kind", code: "some_future_code", status: 409 };
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: "Пометить заголовком" }));

    expect(
      await screen.findByText("Не удалось выполнить действие. Обновите экран и повторите.", {}, LATER)
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("some_future_code");
  });

  it("после отказа новая попытка начинает с чистого экрана: прежний отказ снят", async () => {
    handlerState.contextRefusal = { action: "position-kind", code: "position_not_position", status: 409 };
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: "Пометить заголовком" }));
    await screen.findByText("Строка уже не ждёт решения: обновите экран.", {}, LATER);
    await user.click(screen.getByRole("button", { name: "Пометить мусором" }));

    await waitFor(
      () =>
        expect(screen.queryByText("Строка уже не ждёт решения: обновите экран.")).not.toBeInTheDocument(),
      LATER
    );
  });
});
