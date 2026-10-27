import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CatalogProgressAlert } from "./CatalogProgressAlert";
import { WAITING_ADMIN_LABEL } from "./ourPriceCopy";

/** Плашка «каталог ещё разбирает» (спека 4а §2.11): видна, пока есть `in_progress`. */
describe("CatalogProgressAlert", () => {
  it("in_progress > 0: плашка и прогресс (всего − в работе) / всего", () => {
    render(<CatalogProgressAlert progress={{ lines_total: 10, in_progress: 3, waiting_admin: 0 }} />);
    expect(screen.getByTestId("catalog-progress")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "70");
    expect(screen.getByTestId("catalog-progress")).toHaveTextContent("7 из 10");
  });

  it("граница: одна строка в работе из десяти — 90 %, а не 100", () => {
    render(<CatalogProgressAlert progress={{ lines_total: 10, in_progress: 1, waiting_admin: 0 }} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "90");
  });

  it("in_progress = 0: плашки нет", () => {
    render(<CatalogProgressAlert progress={{ lines_total: 10, in_progress: 0, waiting_admin: 0 }} />);
    expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument();
  });

  it("lines_total = 0: плашки нет и деления на ноль нет", () => {
    render(<CatalogProgressAlert progress={{ lines_total: 0, in_progress: 0, waiting_admin: 0 }} />);
    expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("lines_total = 0 при in_progress > 0 (несогласованный ответ): плашки нет — сторож делителя держит сам", () => {
    // Предыдущий тест проходит и без сторожа `lines_total`: при `in_progress = 0` плашку гасит
    // второе условие. Здесь условие на `in_progress` пропускает, и пустую плашку с делением
    // на ноль закрывает только проверка `lines_total`.
    render(<CatalogProgressAlert progress={{ lines_total: 0, in_progress: 2, waiting_admin: 0 }} />);
    expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("waiting_admin > 0: подпись называет число и «ждёт решения администратора»", () => {
    render(<CatalogProgressAlert progress={{ lines_total: 10, in_progress: 2, waiting_admin: 4 }} />);
    const alert = screen.getByTestId("catalog-progress");
    expect(alert).toHaveTextContent(WAITING_ADMIN_LABEL);
    expect(alert).toHaveTextContent("4");
  });

  it("waiting_admin = 0: слов про администратора нет", () => {
    render(<CatalogProgressAlert progress={{ lines_total: 10, in_progress: 2, waiting_admin: 0 }} />);
    expect(screen.getByTestId("catalog-progress")).not.toHaveTextContent(WAITING_ADMIN_LABEL);
    expect(WAITING_ADMIN_LABEL).toBe("ждёт решения администратора");
  });
});
