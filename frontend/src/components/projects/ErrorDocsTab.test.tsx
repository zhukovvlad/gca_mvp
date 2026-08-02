import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { ErrorDocsTab } from "./ErrorDocsTab";
import type { DocumentSummary } from "@/types/invoice";

// Спай на toast.success (реальная реализация sonner) — §7: проверяем именно ТЕКСТ
// уведомления «Обработка запущена», не только факт вызова API.
vi.mock("sonner", async (importOriginal) => {
  /** Реальный модуль sonner с подменённым (spy) toast.success/error. */
  const actual = await importOriginal<typeof import("sonner")>();
  return { ...actual, toast: { ...actual.toast, success: vi.fn(), error: vi.fn() } };
});

const makeDoc = (overrides: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: 1,
  project_id: 10,
  filename: "invoice-2024-01.pdf",
  doc_type: "invoice",
  status: "error",
  uploaded_at: "2024-01-15T10:00:00Z",
  invoice_count: 0,
  has_issues: false,
  ai_confidence: null,
  parse_cost_usd: 0,
  parse_count: 1,
  ...overrides,
});

describe("ErrorDocsTab", () => {
  it("shows positive empty state when no error docs", () => {
    const cleanDoc = makeDoc({ status: "parsed", has_issues: false });
    renderWithProviders(<ErrorDocsTab docs={[cleanDoc]} />);
    expect(screen.getByText(/все документы разобраны успешно/i)).toBeInTheDocument();
  });

  it("renders error doc row with filename", () => {
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} />);
    expect(screen.getByText("invoice-2024-01.pdf")).toBeInTheDocument();
  });

  it("renders has_issues doc with 'Проблемы в СФ' status", () => {
    const doc = makeDoc({ status: "parsed", has_issues: true });
    renderWithProviders(<ErrorDocsTab docs={[doc]} />);
    expect(screen.getByText("Проблемы в СФ")).toBeInTheDocument();
  });

  it("renders status=error doc with 'Ошибка парсинга' status", () => {
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} />);
    expect(screen.getByText("Ошибка парсинга")).toBeInTheDocument();
  });

  it("shows last_error reason instead of generic label when present", () => {
    const doc = makeDoc({ last_error: "Разбор счёта №5 неполный" });
    renderWithProviders(<ErrorDocsTab docs={[doc]} />);
    expect(screen.getByText("Разбор счёта №5 неполный")).toBeInTheDocument();
  });

  it("opens delete confirmation dialog before deleting", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} />);
    await user.click(screen.getByRole("button", { name: /удалить/i }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
  });

  it("deletes document after confirmation", async () => {
    const onDelete = vi.fn();
    server.use(
      http.delete("/api/invoices/documents/:id", () => {
        onDelete();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc()]} />);
    await user.click(screen.getByRole("button", { name: /удалить/i }));
    await user.click(await screen.findByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledOnce());
  });

  it("calls reparse endpoint on reparse button click", async () => {
    const onReparse = vi.fn();
    server.use(
      http.post("/api/invoices/documents/:id/reparse", ({ params }) => {
        onReparse(params.id);
        return HttpResponse.json(makeDoc({ id: Number(params.id), status: "parsed" }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc({ id: 1 })]} />);
    // exact-match: "Переразобрать", чтобы не зацепить "Выпрямить и переразобрать"
    await user.click(screen.getByRole("button", { name: /^переразобрать$/i }));
    await waitFor(() => expect(onReparse).toHaveBeenCalledWith("1"));
  });

  it("shows «Обработка запущена» toast on reparse success (спека §7)", async () => {
    server.use(
      http.post("/api/invoices/documents/:id/reparse", ({ params }) =>
        HttpResponse.json(makeDoc({ id: Number(params.id), status: "parsed" })),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc({ id: 1 })]} />);
    await user.click(screen.getByRole("button", { name: /^переразобрать$/i }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Обработка запущена"));
  });

  it("calls deskew-reparse endpoint on button click", async () => {
    const onDeskew = vi.fn();
    server.use(
      http.post("/api/invoices/documents/:id/deskew-reparse", ({ params }) => {
        onDeskew(params.id);
        return HttpResponse.json(makeDoc({ id: Number(params.id), status: "parsed" }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc({ id: 1 })]} />);
    await user.click(screen.getByRole("button", { name: /Выпрямить и переразобрать/i }));
    await waitFor(() => expect(onDeskew).toHaveBeenCalledWith("1"));
  });

  it("shows «Обработка запущена» toast on deskew-reparse success (спека §7)", async () => {
    server.use(
      http.post("/api/invoices/documents/:id/deskew-reparse", ({ params }) =>
        HttpResponse.json(makeDoc({ id: Number(params.id), status: "parsed" })),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<ErrorDocsTab docs={[makeDoc({ id: 1 })]} />);
    await user.click(screen.getByRole("button", { name: /Выпрямить и переразобрать/i }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Обработка запущена"));
  });
});
