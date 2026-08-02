import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import { sampleDashboardInvoices } from "@/test/fixtures";

import { InvoiceTable } from "./InvoiceTable";

/**
 * InvoiceTable key invariants:
 * 1. Row checkbox selects the row; header checkbox selects all.
 * 2. Bulk delete toolbar appears only when ≥1 row is selected.
 * 3. Bulk delete requires AlertDialog confirmation — API not called on first click.
 * 4. After confirmation, DELETE /api/invoices/bulk is called with selected ids.
 * 5. Single-row delete (Trash icon) opens its own AlertDialog.
 */
describe("InvoiceTable", () => {
  const invoices = sampleDashboardInvoices;

  it("renders all invoices", () => {
    renderWithProviders(<InvoiceTable invoices={invoices} />);
    expect(screen.getByText("СФ-CONFIRMED")).toBeInTheDocument();
    expect(screen.getByText("СФ-REVIEW")).toBeInTheDocument();
    expect(screen.getByText("СФ-PENDING")).toBeInTheDocument();
  });

  it("no bulk toolbar when nothing selected", () => {
    renderWithProviders(<InvoiceTable invoices={invoices} />);
    expect(screen.queryByText(/Выбрано/)).not.toBeInTheDocument();
  });

  it("selecting a row shows the bulk toolbar", async () => {
    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-PENDING/i }));

    expect(await screen.findByText(/Выбрано/)).toBeInTheDocument();
    expect(screen.getByText("1", { selector: ".font-medium" })).toBeInTheDocument();
  });

  it("header checkbox selects all rows", async () => {
    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    await user.click(screen.getByRole("checkbox", { name: /Выбрать все/i }));

    expect(await screen.findByText(/Выбрано/)).toBeInTheDocument();
    // all 3 invoices selected
    expect(screen.getByText(String(invoices.length), { selector: ".font-medium" })).toBeInTheDocument();
  });

  it("bulk delete requires confirmation — API not called on first click", async () => {
    const onBulkDelete = vi.fn();
    server.use(
      http.delete("/api/invoices/bulk", async ({ request }) => {
        const body = (await request.json()) as { ids: number[] };
        onBulkDelete(body.ids);
        return HttpResponse.json({ deleted: body.ids.length, skipped: [] });
      })
    );

    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-PENDING/i }));
    await user.click(await screen.findByRole("button", { name: /Удалить выбранные/i }));

    expect(await screen.findByText(/Удалить 1 СФ/)).toBeInTheDocument();
    expect(onBulkDelete).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByText(/Удалить 1 СФ/)).not.toBeInTheDocument();
    expect(onBulkDelete).not.toHaveBeenCalled();
  });

  it("bulk delete calls API after confirmation with selected ids", async () => {
    const onBulkDelete = vi.fn();
    server.use(
      http.delete("/api/invoices/bulk", async ({ request }) => {
        const body = (await request.json()) as { ids: number[] };
        onBulkDelete(body.ids);
        return HttpResponse.json({ deleted: body.ids.length, skipped: [] });
      })
    );

    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    // Select two rows
    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-REVIEW/i }));
    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-PENDING/i }));
    await user.click(await screen.findByRole("button", { name: /Удалить выбранные/i }));

    const dialog = await screen.findByText(/Удалить 2 СФ/);
    const alertDialog = dialog.closest('[role="alertdialog"]')!;
    await user.click(alertDialog.querySelector('[data-slot="alert-dialog-action"]')!);

    await waitFor(() => {
      expect(onBulkDelete).toHaveBeenCalledWith(
        expect.arrayContaining([202, 203])
      );
    });
  });

  it("effectiveSelectedIds: count shows only visible rows after search filter", async () => {
    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    // Select all 3 rows
    await user.click(screen.getByRole("checkbox", { name: /Выбрать все/i }));
    expect(await screen.findByText(String(invoices.length), { selector: ".font-medium" })).toBeInTheDocument();

    // Apply search that hides 2 rows — only "СФ-PENDING" (Поставщик В) remains
    const searchInput = screen.getByPlaceholderText(/Номер, поставщик/i);
    await user.type(searchInput, "Поставщик В");

    // Counter must drop to 1
    await waitFor(() => {
      expect(screen.getByText("1", { selector: ".font-medium" })).toBeInTheDocument();
    });
  });

  it("effectiveSelectedIds: bulk delete sends only visible ids after filter", async () => {
    const onBulkDelete = vi.fn();
    server.use(
      http.delete("/api/invoices/bulk", async ({ request }) => {
        const body = (await request.json()) as { ids: number[] };
        onBulkDelete(body.ids);
        return HttpResponse.json({ deleted: body.ids.length, skipped: [] });
      })
    );

    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    // Select all 3 rows
    await user.click(screen.getByRole("checkbox", { name: /Выбрать все/i }));
    expect(await screen.findByText(String(invoices.length), { selector: ".font-medium" })).toBeInTheDocument();

    // Filter to only СФ-PENDING (id=203)
    const searchInput = screen.getByPlaceholderText(/Номер, поставщик/i);
    await user.type(searchInput, "Поставщик В");
    await waitFor(() => {
      expect(screen.getByText("1", { selector: ".font-medium" })).toBeInTheDocument();
    });

    // Confirm bulk delete — should send only the visible id
    await user.click(screen.getByRole("button", { name: /Удалить выбранные/i }));
    const dialog = await screen.findByText(/Удалить 1 СФ/);
    const alertDialog = dialog.closest('[role="alertdialog"]')!;
    await user.click(alertDialog.querySelector('[data-slot="alert-dialog-action"]')!);

    await waitFor(() => {
      expect(onBulkDelete).toHaveBeenCalledWith([203]);
      expect(onBulkDelete).not.toHaveBeenCalledWith(expect.arrayContaining([201, 202]));
    });
  });

  it("single row delete calls DELETE /:id after confirmation", async () => {
    const onDelete = vi.fn();
    server.use(
      http.delete("/api/invoices/:id", ({ params }) => {
        onDelete(Number(params.id));
        return HttpResponse.json({ message: "СФ удалена" });
      })
    );

    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    // Click the first enabled (non-confirmed) delete button
    const enabledDeletes = screen
      .getAllByRole("button", { name: "Удалить" })
      .filter((b) => !b.hasAttribute("disabled"));
    await user.click(enabledDeletes[0]);

    const dialog = await screen.findByText(/Удалить СФ «/);
    expect(dialog).toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();

    const alertDialog = dialog.closest('[role="alertdialog"]')!;
    await user.click(alertDialog.querySelector('[data-slot="alert-dialog-action"]')!);

    await waitFor(() => {
      expect(onDelete).toHaveBeenCalledTimes(1);
    });
  });

  // ── busyDocIds: disable mutations on busy (processing) documents (спека §6) ──

  it("delete button disabled for a row whose document is busy (busyDocIds)", async () => {
    // СФ-PENDING (id=203) belongs to document_id=12 — mark it busy.
    renderWithProviders(<InvoiceTable invoices={invoices} busyDocIds={new Set([12])} />);

    const row = screen.getByText("СФ-PENDING").closest("tr")!;
    const deleteButton = within(row).getByRole("button", { name: "Удалить" });
    expect(deleteButton).toBeDisabled();
  });

  it("delete buttons stay enabled when busyDocIds is omitted (default empty set)", () => {
    renderWithProviders(<InvoiceTable invoices={invoices} />);

    const row = screen.getByText("СФ-PENDING").closest("tr")!;
    const deleteButton = within(row).getByRole("button", { name: "Удалить" });
    expect(deleteButton).not.toBeDisabled();
  });

  it("bulk delete trigger disabled when any selected row belongs to a busy document", async () => {
    // document_id=12 (СФ-PENDING) busy; select it together with a non-busy row.
    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} busyDocIds={new Set([12])} />);

    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-REVIEW/i }));
    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-PENDING/i }));

    const bulkButton = await screen.findByRole("button", { name: /Удалить выбранные/i });
    expect(bulkButton).toBeDisabled();
  });

  it("bulk delete trigger stays enabled when no selected row belongs to a busy document", async () => {
    // document_id=12 busy, but only the unrelated СФ-REVIEW row (document_id=11) is selected.
    const user = userEvent.setup();
    renderWithProviders(<InvoiceTable invoices={invoices} busyDocIds={new Set([12])} />);

    await user.click(screen.getByRole("checkbox", { name: /Выбрать СФ СФ-REVIEW/i }));

    const bulkButton = await screen.findByRole("button", { name: /Удалить выбранные/i });
    expect(bulkButton).not.toBeDisabled();
  });
});
