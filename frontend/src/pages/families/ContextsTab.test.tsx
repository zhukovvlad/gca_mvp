import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ContextsTab } from "@/pages/families/ContextsTab";
import { renderWithProviders } from "@/test/utils";

/**
 * Очередь контекстов — вкладка «Контексты» (спека §2.10). Шесть фикстур
 * (`initialSemanticContexts`, `src/test/handlers.ts`) покрывают состояния из
 * плана задачи 13: обычный, устаревшее членство, конфликт решений,
 * `insufficient_description` без семьи, пустой контекст, архивный.
 */

async function renderTab(onSelect = vi.fn()) {
  renderWithProviders(<ContextsTab selectedContextId={null} onSelect={onSelect} />);
  await waitFor(() =>
    expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
  );
  return onSelect;
}

describe("ContextsTab", () => {
  it("пустая очередь контекстов — фильтр не находит ни одного", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "нет-такого-текста-в-каталоге");
    expect(await screen.findByText("Контекстов нет")).toBeInTheDocument();
  });

  it("подписи причины сравнимости различаются ТЕКСТОМ, а не наличием узла", async () => {
    await renderTab();

    // «Светильники» (id 604) — insufficient_description, без семьи: подпись
    // называет причину словами, а не молчит пустой ячейкой.
    expect(
      screen.getByText("семья не назначена, потому что состав не описан")
    ).toBeInTheDocument();

    // «Устройство покрытий полов из линолеума» (id 602) — семьи нет, но
    // comparability_reason не выставлен: ДРУГАЯ подпись, не «пустая ячейка»
    // и не подпись insufficient_description. Той же подписью помечены ещё
    // два контекста фикстуры (605, 606) — проверяем строку id 602 точечно.
    const staleRow = screen
      .getByText("Устройство покрытий полов из линолеума")
      .closest("tr")!;
    expect(within(staleRow).getByText("нет семьи")).toBeInTheDocument();
  });

  it("три фильтра-признака — три независимых контрола, каждый сужает очередь по своей оси", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("checkbox", { name: "Есть устаревшие членства" }));
    await waitFor(() => {
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Отделка потолков водоэмульсионным составом")
      ).not.toBeInTheDocument();
    });

    // Снять «устаревшие», включить «конфликтные» — другая проекция, другой контекст.
    await user.click(screen.getByRole("checkbox", { name: "Есть устаревшие членства" }));
    await user.click(screen.getByRole("checkbox", { name: "Есть конфликтные членства" }));
    await waitFor(() => {
      expect(
        screen.getByText("Отделка потолков водоэмульсионным составом")
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Устройство покрытий полов из линолеума")
      ).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("checkbox", { name: "Есть конфликтные членства" }));
    await user.click(screen.getByRole("checkbox", { name: "Нет членств" }));
    await waitFor(() => {
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument();
      expect(
        screen.queryByText("Отделка потолков водоэмульсионным составом")
      ).not.toBeInTheDocument();
    });
  });

  it("клик по строке вызывает onSelect с id контекста", async () => {
    const user = userEvent.setup();
    const onSelect = await renderTab();

    await user.click(screen.getByText("Штукатурка стен цементно-песчаным раствором"));
    expect(onSelect).toHaveBeenCalledWith(601);
  });

  it("архивный контекст в очереди несёт пометку", async () => {
    await renderTab();
    const row = screen.getByText("Гидроизоляция фундамента (снят)").closest("tr")!;
    expect(within(row).getByText("архивный")).toBeInTheDocument();
  });

  it("фильтр по статье сужает очередь (спека §2.10)", async () => {
    const user = userEvent.setup();
    await renderTab();

    // Контекст 604 («Светильники») несёт статью 88, остальные пять — 77.
    await user.type(screen.getByLabelText("Статья (id)"), "88");

    await waitFor(() => {
      expect(screen.getByText("Светильники")).toBeInTheDocument();
      expect(
        screen.queryByText("Штукатурка стен цементно-песчаным раствором")
      ).not.toBeInTheDocument();
    });
  });
});
