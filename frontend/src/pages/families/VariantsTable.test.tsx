import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

import { VariantsTable } from "./VariantsTable";

/**
 * Таблица вариантов семьи (спека `2026-10-02-catalog-variants-design.md` §2.12):
 * набор значений по параметрам (пустое значение — «не уточнено»), число
 * контекстов, статус словом.
 */

const LATER = { timeout: 8000 };

function rowsOfTable() {
  const table = screen.getByRole("table", { name: "Варианты семьи" });
  return within(table).getAllByRole("row").slice(1);
}

describe("VariantsTable", () => {
  it("строка на вариант: набор, число контекстов, статус словом; архивный вариант виден", async () => {
    renderWithProviders(<VariantsTable familyId={SCHEMA_FAMILY_ID} />);
    await screen.findByRole("table", { name: "Варианты семьи" }, LATER);

    const rows = rowsOfTable();
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("профнастил · 0,5 мм");
    expect(within(rows[0]).getByText("3")).toBeInTheDocument();
    expect(within(rows[0]).getByText("активен")).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("не уточнено · не уточнено");
    expect(within(rows[2]).getByText("0")).toBeInTheDocument();
    expect(within(rows[2]).getByText("в архиве")).toBeInTheDocument();
  });

  it("пустое значение одного параметра читается «не уточнено», остальные остаются текстом", async () => {
    renderWithProviders(<VariantsTable familyId={SCHEMA_FAMILY_ID} />);
    await screen.findByRole("table", { name: "Варианты семьи" }, LATER);

    expect(rowsOfTable()[1]).toHaveTextContent("металлочерепица · не уточнено");
  });

  it("вариант без параметров читается «не уточнено», а не пустой ячейкой", async () => {
    handlerState.familyVariants[SCHEMA_FAMILY_ID] = [{ id: 9, values: [], contexts: 2, status: "active" }];
    renderWithProviders(<VariantsTable familyId={SCHEMA_FAMILY_ID} />);
    await screen.findByRole("table", { name: "Варианты семьи" }, LATER);

    expect(rowsOfTable()[0]).toHaveTextContent("не уточнено");
  });

  it("статус не выходит кодом: «active» и «archived» на экране нет", async () => {
    renderWithProviders(<VariantsTable familyId={SCHEMA_FAMILY_ID} />);
    await screen.findByRole("table", { name: "Варианты семьи" }, LATER);

    expect(document.body.textContent).not.toMatch(/active|archived/);
  });

  it("у семьи без вариантов таблицы нет, есть слова", async () => {
    renderWithProviders(<VariantsTable familyId={2} />);

    expect(await screen.findByText("Вариантов пока нет.", {}, LATER)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("отказ чтения — подпись, а не пустое место", async () => {
    server.use(
      http.get("/api/v1/semantic/families/:id/variants", () =>
        HttpResponse.json({ detail: { code: "family_not_found", message: "x" } }, { status: 404 })
      )
    );
    renderWithProviders(<VariantsTable familyId={SCHEMA_FAMILY_ID} />);

    expect(await screen.findByText("Не удалось получить варианты семьи.", {}, LATER)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("family_not_found");
  });
});
