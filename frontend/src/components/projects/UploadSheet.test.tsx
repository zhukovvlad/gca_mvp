import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";

import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { sampleDocument } from "@/test/fixtures";
import { UploadSheet } from "./UploadSheet";

// Спай на toast.info/success (реальная реализация sonner) — проверяем именно
// ТЕКСТ уведомления, не только факт вызова (паттерн ErrorDocsTab.test.tsx §7).
vi.mock("sonner", async (importOriginal) => {
  /** Реальный модуль sonner с подменённым (spy) toast.info/success. */
  const actual = await importOriginal<typeof import("sonner")>();
  return {
    ...actual,
    toast: { ...actual.toast, info: vi.fn(), success: vi.fn(), error: vi.fn() },
  };
});

/**
 * Загружает файл через скрытый input react-dropzone (паттерн Dropzone.test.tsx).
 * SheetContent рендерится в Radix Portal (document.body), не внутри
 * render()'ного container, поэтому ищем input глобально через `document`.
 */
async function uploadFile(name = "invoice.pdf") {
  const user = userEvent.setup();
  const input = document.querySelector("input[type=file]") as HTMLInputElement;
  const file = new File(["dummy"], name, { type: "application/pdf" });
  await user.upload(input, file);
}

describe("UploadSheet (S1 upload UI перенесён из мёртвой pages/Upload.tsx, смоук PR #37)", () => {
  it("успешная загрузка (202, processing) показывает строку «обрабатывается»", async () => {
    server.use(
      http.post("/api/invoices/upload", () =>
        HttpResponse.json({ ...sampleDocument, status: "processing", invoices: [], duplicate: false })
      ),
      http.get("/api/invoices/documents/:id", () =>
        HttpResponse.json({ ...sampleDocument, status: "processing", invoices: [] })
      )
    );

    renderWithProviders(<UploadSheet projectId={1} open onOpenChange={() => {}} />);
    await uploadFile();

    await waitFor(() => {
      expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
    });
    expect(await screen.findByText(/обрабатывается/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("«invoice.pdf» принят в обработку")
    );
  });

  it("дубликат: тост info с текстом донора + бейдж «Файл уже был загружен»", async () => {
    server.use(
      http.post("/api/invoices/upload", () =>
        HttpResponse.json({ ...sampleDocument, status: "parsed", duplicate: true })
      )
    );

    renderWithProviders(<UploadSheet projectId={1} open onOpenChange={() => {}} />);
    await uploadFile();

    expect(await screen.findByText("Файл уже был загружен")).toBeInTheDocument();
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith("«invoice.pdf» — файл уже был загружен")
    );
  });

  it("провал загрузки (500): toast.error + строка джоба показывает «ошибка» (CodeRabbit, PR #37)", async () => {
    server.use(
      http.post("/api/invoices/upload", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    );

    renderWithProviders(<UploadSheet projectId={1} open onOpenChange={() => {}} />);
    await uploadFile();

    await waitFor(() => {
      expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
    });
    expect(await screen.findByText("ошибка")).toBeInTheDocument();
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("«invoice.pdf» — ошибка загрузки")
    );
  });

  it("шит не закрывается сам после загрузки", async () => {
    server.use(
      http.post("/api/invoices/upload", () =>
        HttpResponse.json({ ...sampleDocument, status: "processing", invoices: [], duplicate: false })
      ),
      http.get("/api/invoices/documents/:id", () =>
        HttpResponse.json({ ...sampleDocument, status: "processing", invoices: [] })
      )
    );

    let closed = false;
    renderWithProviders(
      <UploadSheet projectId={1} open onOpenChange={() => { closed = true; }} />
    );
    await uploadFile();

    await waitFor(() => {
      expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
    });
    expect(closed).toBe(false);
    // Dropzone остаётся смонтированной и доступной для докидывания новых файлов.
    expect(document.querySelector("input[type=file]")).not.toBeNull();
  });
});
