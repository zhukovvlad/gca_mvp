import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { UploadJobRow, type JobState } from "./UploadJobRow";

const baseDoc = {
  id: 7, project_id: 1, filename: "a.pdf", doc_type: "invoice", status: "processing",
  last_error: null, uploaded_at: "2026-07-19T10:00:00", invoice_count: 0, has_issues: false,
  ai_confidence: null, parse_cost_usd: 0, parse_count: 0, invoices: [],
};

/** Рендер строки job'а с провайдерами react-query и роутера (паттерн ErrorDocsTab.test.tsx). */
function renderRow(job: JobState) {
  return renderWithProviders(<UploadJobRow job={job} />);
}

describe("UploadJobRow (S1-6, AC-S1-5)", () => {
  it("после 202 показывает «обрабатывается» из статуса документа", async () => {
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json(baseDoc)));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: false } });
    expect(await screen.findByText(/обрабатывается/i)).toBeInTheDocument();
  });

  it("пока документ обрабатывается — «Проверить» задизейблена, а не ссылка (Codex P2, fix 2)", async () => {
    // Свежий 202 содержит invoices: [] — Review трактует их отсутствие как
    // «Документ не найден». Клик по ссылке во время обработки вёл бы туда.
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json(baseDoc)));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: false } });

    const btn = await screen.findByRole("button", { name: "Проверить" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Документ обрабатывается — дождитесь завершения");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("после завершения показывает СФ из данных polling'а (query-кэш, не снапшот ответа)", async () => {
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json({
      ...baseDoc, status: "parsed", invoice_count: 1,
      invoices: [{ id: 11, document_id: 7, number: "СФ-1", date: "2026-07-01",
                   supplier_name: null, supplier_inn: null, vat_rate: 20, ai_confidence: 0.9,
                   verified: false, verified_at: null, has_issues: false, items: [] }],
    })));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: false } });
    expect(await screen.findByText(/СФ № СФ-1/)).toBeInTheDocument();

    // Документ дошёл до parsed — «Проверить» теперь ссылка, не задизейбленная кнопка.
    const link = screen.getByRole("link", { name: "Проверить" });
    expect(link).toHaveAttribute("href", "/documents/7");
  });

  it("при ошибке обработки «Проверить» не рендерится (ретрай — только через reparse)", async () => {
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json({
      ...baseDoc, status: "error", last_error: "Не удалось распознать документ",
    })));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: false } });

    await screen.findByText("Не удалось распознать документ");
    expect(screen.queryByRole("button", { name: "Проверить" })).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("дубликат: бейдж «Файл уже был загружен» + ссылка на документ, не error-стиль", () => {
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, status: "parsed", duplicate: true } });
    expect(screen.getByText("Файл уже был загружен")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/documents/7");
  });

  it("дубликат упавшего файла: ссылка «К ошибкам проекта» — путь ретрая через reparse (Codex P2, fix 3)", async () => {
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json({
      ...baseDoc, status: "error", last_error: "Не удалось распознать документ",
    })));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: true } });

    await screen.findByText("Файл уже был загружен");
    const link = await screen.findByRole("link", { name: "К ошибкам проекта" });
    expect(link).toHaveAttribute("href", "/projects/1?direction=all&view=errors&tab=errors");
  });
});
