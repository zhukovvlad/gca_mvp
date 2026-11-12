import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useLocation } from "react-router-dom";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { parseEntry } from "@/pages/families/entryParams";
import { renderWithProviders } from "@/test/utils";

/**
 * Вход по адресу `/families?tab=journal&entry=<id>` (спека упрощения каталога §2.7): открывается
 * четвёртая вкладка «Журнал ИИ» и карточка записи; некорректный адрес страницу не меняет.
 */

const LATER = { timeout: 8000 };

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.pathname + location.search}</div>;
}

function renderAt(route: string) {
  return renderWithProviders(
    <>
      <FamiliesPage />
      <LocationProbe />
    </>,
    { initialRoute: route }
  );
}

describe("parseEntry: вход в журнал", () => {
  it("tab=journal с id записи — вход в журнал", () => {
    expect(parseEntry(new URLSearchParams("tab=journal&entry=105"))).toEqual({ tab: "journal", entryId: 105 });
  });

  it("tab=journal без entry — вход в журнал без выбранной записи", () => {
    expect(parseEntry(new URLSearchParams("tab=journal"))).toEqual({ tab: "journal", entryId: null });
  });

  it.each(["", "abc", "0", "-3", "1e2", "1.0", " 7"])("entry=%j — не вход", (raw) => {
    expect(parseEntry(new URLSearchParams({ tab: "journal", entry: raw }))).toBeNull();
  });

  it("без tab=journal параметр entry входом не считается", () => {
    expect(parseEntry(new URLSearchParams("entry=105"))).toBeNull();
    expect(parseEntry(new URLSearchParams("tab=suggestions&entry=105"))).toBeNull();
  });

  it("прежние входы не меняются", () => {
    expect(parseEntry(new URLSearchParams("context=7"))).toEqual({ tab: "contexts", contextId: 7 });
  });
});

describe("FamiliesPage: вкладка «Журнал ИИ»", () => {
  it("четвёртая вкладка есть и открывается щелчком", async () => {
    const user = userEvent.setup();
    renderAt("/families");

    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs.slice(0, 4)).toEqual(["Семьи", "Контексты", "Предложения", expect.stringMatching(/^Журнал ИИ/)]);

    await user.click(screen.getByRole("tab", { name: /^Журнал ИИ/ }));
    await waitFor(() => expect(document.querySelector("[data-journal-id]")).not.toBeNull(), LATER);
  });

  it("?tab=journal&entry=<id> открывает вкладку и карточку записи", async () => {
    renderAt("/families?tab=journal&entry=105");

    expect(screen.getByRole("tab", { name: /^Журнал ИИ/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: /стало составным значением из 2 частей/ }, LATER)).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector('[data-journal-id="105"]')).toHaveAttribute("aria-selected", "true"), LATER);
  });

  it("выбор строки пишет запись в адрес, ручная смена вкладки вход снимает", async () => {
    const user = userEvent.setup();
    renderAt("/families?tab=journal&entry=105");
    await waitFor(() => expect(document.querySelector('[data-journal-id="104"]')).not.toBeNull(), LATER);

    await user.click(document.querySelector('[data-journal-id="104"]') as HTMLElement);
    await waitFor(() => expect(screen.getByTestId("loc").textContent).toContain("entry=104"), LATER);

    await user.click(screen.getByRole("tab", { name: "Контексты" }));
    await waitFor(() => expect(screen.getByTestId("loc").textContent).not.toContain("entry="), LATER);
    expect(screen.getByTestId("loc").textContent).not.toContain("tab=journal");
  });

  it("?tab=journal без entry открывает вкладку и ленту, карточка не выбрана", async () => {
    renderAt("/families?tab=journal");

    expect(screen.getByRole("tab", { name: /^Журнал ИИ/ })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.querySelector("[data-journal-id]")).not.toBeNull(), LATER);
    expect(document.querySelector('[aria-selected="true"][data-journal-id]')).toBeNull();
    expect(screen.getByText("Выберите запись слева.")).toBeInTheDocument();
  });

  it("некорректная запись в адресе вкладку не открывает", () => {
    renderAt("/families?tab=journal&entry=abc");

    expect(screen.getByRole("tab", { name: "Семьи" })).toHaveAttribute("aria-selected", "true");
  });
});
