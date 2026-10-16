import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { NotWorkView } from "@/types/domain";

import { NotWorkGroup } from "./NotWorkGroup";

/** Группа «Не работа» открытия: построчно по наименованию, все отмечены, первые 20 видны. */

function groupOf(count: number): NotWorkView {
  return {
    id: 9,
    names: Array.from({ length: count }, (_, i) => ({
      title: `Примечание ${i + 1}`,
      contexts: i === 0 ? 2 : 1,
      context_ids: i === 0 ? [100, 101] : [100 + i + 1],
    })),
  };
}

function renderGroup(
  group: NotWorkView,
  unchecked: ReadonlySet<string> = new Set(),
  handlers: { onToggle?: () => void; onToggleAll?: () => void } = {}
) {
  const onToggle = handlers.onToggle ?? vi.fn();
  const onToggleAll = handlers.onToggleAll ?? vi.fn();
  render(
    <NotWorkGroup group={group} unchecked={unchecked} onToggle={onToggle} onToggleAll={onToggleAll} />
  );
  return { onToggle, onToggleAll };
}

describe("NotWorkGroup", () => {
  it("все наименования отмечены, счётчик «N из N отмечены»", () => {
    renderGroup(groupOf(5));

    expect(screen.getAllByTestId("not-work-row")).toHaveLength(5);
    for (const box of screen.getAllByRole("checkbox", { name: "Отметить строку" })) {
      expect(box).toBeChecked();
    }
    expect(screen.getByText(/из 5 отмечены/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Отметить все строки «Не работа»" })).toBeChecked();
  });

  it("первые 20 видны, остальные под «Показать все»; граница 20 — без кнопки", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <NotWorkGroup group={groupOf(20)} unchecked={new Set()} onToggle={vi.fn()} onToggleAll={vi.fn()} />
    );
    expect(screen.getAllByTestId("not-work-row")).toHaveLength(20);
    expect(screen.queryByRole("button", { name: "Показать все" })).not.toBeInTheDocument();
    unmount();

    renderGroup(groupOf(25));
    expect(screen.getAllByTestId("not-work-row")).toHaveLength(20);
    expect(screen.getByText(/… и ещё 5, отмечены/)).toBeInTheDocument();
    expect(screen.queryByText("Примечание 21")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Показать все" }));

    expect(screen.getAllByTestId("not-work-row")).toHaveLength(25);
    expect(screen.getByText("Примечание 25")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Показать все" })).not.toBeInTheDocument();
  });

  it("строка показывает число контекстов наименования, только если их больше одного", () => {
    renderGroup(groupOf(3));

    const rows = screen.getAllByTestId("not-work-row");
    expect(within(rows[0]).getByText("2 контекста")).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/контекст/)).not.toBeInTheDocument();
  });

  it("снятая отметка: строка не отмечена, счётчик уменьшился, спрятанные считаются отдельно", () => {
    renderGroup(groupOf(25), new Set(["Примечание 2", "Примечание 24"]));

    const rows = screen.getAllByTestId("not-work-row");
    expect(within(rows[1]).getByRole("checkbox")).not.toBeChecked();
    expect(within(rows[0]).getByRole("checkbox")).toBeChecked();
    expect(screen.getByText(/из 25 отмечены/)).toBeInTheDocument();
    expect(screen.getByText(/… и ещё 5, отмечено 4/)).toBeInTheDocument();
  });

  it("клик по строке сообщает наименование и новое состояние, по галочке группы — снять все", async () => {
    const user = userEvent.setup();
    const { onToggle, onToggleAll } = renderGroup(groupOf(3));

    await user.click(within(screen.getAllByTestId("not-work-row")[2]).getByRole("checkbox"));
    expect(onToggle).toHaveBeenCalledWith("Примечание 3", false);

    await user.click(screen.getByRole("checkbox", { name: "Отметить все строки «Не работа»" }));
    expect(onToggleAll).toHaveBeenCalledWith(false);
  });

  it("когда не отмечено ничего, галочка группы предлагает отметить все", async () => {
    const user = userEvent.setup();
    const { onToggleAll } = renderGroup(groupOf(2), new Set(["Примечание 1", "Примечание 2"]));

    const groupBox = screen.getByRole("checkbox", { name: "Отметить все строки «Не работа»" });
    expect(groupBox).not.toBeChecked();
    await user.click(groupBox);
    expect(onToggleAll).toHaveBeenCalledWith(true);
  });
});
