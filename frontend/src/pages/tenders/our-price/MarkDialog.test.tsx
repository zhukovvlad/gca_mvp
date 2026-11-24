import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { MarkDialog } from "./MarkDialog";

function renderDialog(props: Partial<React.ComponentProps<typeof MarkDialog>> = {}) {
  const onSubmit = vi.fn();
  const onOpenChange = vi.fn();
  renderWithProviders(
    <MarkDialog
      kind="price_error"
      title="Ошибка в цене — не аналог нигде"
      scopeText="Строка «Плинтус» перестанет быть аналогом во всех тендерах."
      open
      onOpenChange={onOpenChange}
      onSubmit={onSubmit}
      busy={false}
      {...props}
    />
  );
  return { onSubmit, onOpenChange };
}

const field = () => screen.getByLabelText("Причина");
const submit = () => screen.getByRole("button", { name: "Исключить" });

describe("окно отметки", () => {
  it("название, текст области действия, поле причины и счётчик", () => {
    renderDialog();
    expect(screen.getByText("Ошибка в цене — не аналог нигде")).toBeInTheDocument();
    expect(screen.getByTestId("mark-scope")).toHaveTextContent("Строка «Плинтус» перестанет быть аналогом во всех тендерах.");
    expect(field()).toBeInTheDocument();
    expect(screen.getByTestId("mark-counter")).toHaveTextContent("0 / 300");
  });

  it("«Исключить» недоступно при пустой причине и при одних пробелах", async () => {
    const { onSubmit } = renderDialog();
    expect(submit()).toBeDisabled();
    await userEvent.type(field(), "   ");
    expect(submit()).toBeDisabled();
    fireEvent.click(submit());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("граница 300 с обеих сторон: 300 символов можно, 301 — нет; счётчик считает обрезанную причину", () => {
    renderDialog();
    fireEvent.change(field(), { target: { value: "а".repeat(300) } });
    expect(submit()).toBeEnabled();
    expect(screen.getByTestId("mark-counter")).toHaveTextContent("300 / 300");
    fireEvent.change(field(), { target: { value: "а".repeat(301) } });
    expect(submit()).toBeDisabled();
    expect(screen.getByTestId("mark-counter")).toHaveTextContent("301 / 300");
    fireEvent.change(field(), { target: { value: `  ${"а".repeat(300)}  ` } });
    expect(submit()).toBeEnabled();
  });

  it("«Исключить» отдаёт причину без пробелов по краям", async () => {
    const { onSubmit } = renderDialog();
    await userEvent.type(field(), "  цена за м²  ");
    await userEvent.click(submit());
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("цена за м²");
  });

  it("занято до ответа: кнопка погашена, повторного вызова нет", async () => {
    const { onSubmit } = renderDialog({ busy: true });
    fireEvent.change(field(), { target: { value: "причина" } });
    expect(submit()).toBeDisabled();
    fireEvent.click(submit());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("«Отмена» закрывает окно и ничего не отправляет", async () => {
    const { onSubmit, onOpenChange } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onOpenChange).toHaveBeenCalledTimes(1);
    expect(onOpenChange.mock.calls[0][0]).toBe(false);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submitLabel заменяет подпись кнопки подтверждения; kind=not_error — свой идентификатор поля", async () => {
    const { onSubmit } = renderDialog({ kind: "not_error", title: "Не ошибка", submitLabel: "Сохранить" });
    expect(screen.queryByRole("button", { name: "Исключить" })).not.toBeInTheDocument();
    expect(field()).toHaveAttribute("id", "mark-reason-not_error");
    await userEvent.type(field(), "цена верная");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(onSubmit).toHaveBeenCalledWith("цена верная");
  });

  it("без submitLabel подпись прежняя — «Исключить»", () => {
    renderDialog();
    expect(submit()).toBeInTheDocument();
  });

  it("закрыто — ничего не нарисовано", () => {
    renderDialog({ open: false });
    expect(screen.queryByTestId("mark-scope")).not.toBeInTheDocument();
  });
});
