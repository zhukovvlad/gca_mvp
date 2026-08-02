import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, delay, type JsonBodyType } from "msw";
import { Link, Routes, Route, useParams, useNavigate } from "react-router-dom";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import {
  sampleProject,
  sampleReferencePrice,
  sampleDashboardInvoices,
  sampleDashboardSummaryMulti,
  sampleDashboardSummaryEmpty,
} from "@/test/fixtures";
import ProjectPage from "./ProjectPage";

// MSW handlers provide:
//   GET /api/projects        → [sampleProject]  (id=1, name="ЖК Радуга")
//   GET /api/dashboard/*     → sampleDashboardSummary / []
//   GET /api/reference-prices → []
//   GET /api/material-classes → [sampleMaterialClass]

/** Render ProjectPage inside a proper Route so useParams() extracts the id. */
function renderProject(id: string = "1", search = "") {
  return renderWithProviders(
    <Routes>
      <Route path="/projects/:id" element={<ProjectPage />} />
    </Routes>,
    { initialRoute: `/projects/${id}${search}` },
  );
}

function mockSummary(payload: JsonBodyType) {
  server.use(http.get("/api/dashboard/summary", () => HttpResponse.json(payload)));
}

/**
 * ProjectPage + кнопка-навигатор в одном матче Route — симулирует
 * внутристраничный переход (клик по ссылке ретрая из UploadJobRow меняет
 * только query string, ProjectPage НЕ размонтируется). Обычный rerender с
 * новым initialRoute этого не проверяет — MemoryRouter пересоздаёт историю.
 */
function NavHarness({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(to)}>go</button>
      <ProjectPage />
    </>
  );
}

/** Render ProjectPage вместе с NavHarness внутри того же Route (для in-page навигации). */
function renderProjectWithNav(to: string, id: string = "1", search = "") {
  return renderWithProviders(
    <Routes>
      <Route path="/projects/:id" element={<NavHarness to={to} />} />
    </Routes>,
    { initialRoute: `/projects/${id}${search}` },
  );
}

describe("ProjectPage", () => {
  it("renders project name in header", async () => {
    renderProject();
    await waitFor(() => {
      // Name appears in both breadcrumb and page heading
      expect(screen.getAllByText("ЖК Радуга").length).toBeGreaterThan(0);
    });
  });

  it("renders all four tabs", async () => {
    renderProject();
    await waitFor(() => {
      expect(screen.getByTestId("project-tab-overview")).toBeInTheDocument();
      expect(screen.getByTestId("project-tab-invoices")).toBeInTheDocument();
      expect(screen.getByTestId("project-tab-prices")).toBeInTheDocument();
      expect(screen.getByTestId("project-tab-suppliers")).toBeInTheDocument();
      expect(screen.getByTestId("project-tab-monthly")).toBeInTheDocument();
    });
  });

  it("?tab=errors открывает вкладку «Ошибки» напрямую (deep-link ретрая дубликата, Codex P2)", async () => {
    renderProject("1", "?tab=errors");
    await waitFor(() => {
      expect(screen.getByTestId("project-tab-errors")).toHaveAttribute("data-active");
    });
    // sampleDocument (дефолтный мок /api/invoices/documents) без ошибок — виден пустой стейт вкладки.
    expect(await screen.findByText("Все документы разобраны успешно")).toBeInTheDocument();
  });

  it("?tab=unknown деградирует к «Обзор» (валидация против известных вкладок)", async () => {
    renderProject("1", "?tab=unknown");
    await waitFor(() => {
      expect(screen.getByTestId("project-tab-overview")).toHaveAttribute("data-active");
    });
  });

  it("внутристраничный переход на ?tab=errors переключает вкладку без размонтирования (Codex P2)", async () => {
    // Legacy-режим (пустые directions) — direction всегда резолвится в "all"
    // независимо от URL, поэтому Tabs остаются смонтированными и tab=errors —
    // единственный работающий параметр (см. комментарий у href в UploadJobRow).
    mockSummary(sampleDashboardSummaryEmpty);
    const user = userEvent.setup();
    renderProjectWithNav("/projects/1?direction=all&view=errors&tab=errors");

    // Смонтировано без tab= — активна «Обзор» (дефолт lazy-инициализатора).
    await waitFor(() => {
      expect(screen.getByTestId("project-tab-overview")).toHaveAttribute("data-active");
    });

    // Клик меняет query string у уже смонтированного ProjectPage (тот же Route,
    // без размонтирования) — только lazy-инициализатор useState это не ловит.
    await user.click(screen.getByRole("button", { name: "go" }));

    await waitFor(() => {
      expect(screen.getByTestId("project-tab-errors")).toHaveAttribute("data-active");
    });
    expect(await screen.findByText("Все документы разобраны успешно")).toBeInTheDocument();
  });

  it("shows KPI cards after summary loads", async () => {
    renderProject();
    await waitFor(() => {
      expect(screen.getByText("Оборот, ₽ с НДС")).toBeInTheDocument();
      expect(screen.getByText("Счетов")).toBeInTheDocument();
    });
  });

  it("switches to Базовые цены tab", async () => {
    const user = userEvent.setup();
    renderProject();
    const tab = await screen.findByTestId("project-tab-prices");
    await user.click(tab);
    await waitFor(() => {
      expect(screen.getByText(/Базовые цены/)).toBeInTheDocument();
    });
  });

  it("shows empty state for invalid project id", async () => {
    renderProject("abc");
    await waitFor(() => {
      expect(screen.getByText("Объект не найден")).toBeInTheDocument();
    });
  });

  it("clicking configure price button switches to prices tab", async () => {
    server.use(
      http.get("/api/dashboard/calculations", () =>
        HttpResponse.json([
          {
            id: 1,
            project_id: 1,
            material_class_id: 1,
            material_class_name: "В25",
            direction: "concrete",
            period_start: "2026-04-01",
            period_end: "2026-04-30",
            total_qty: 10,
            material_total: 80000,
            delivery_total: 0,
            avg_price: 8000,
            invoice_count: 1,
            reference_price: null,
            deviation_pct: null,
            deviation_amount: null,
          },
        ])
      )
    );

    const user = userEvent.setup();
    renderProject();

    const configureBtn = await screen.findByRole("button", { name: /настроить/i });
    await user.click(configureBtn);

    await waitFor(() => {
      expect(screen.getByText("Нет базовых цен")).toBeInTheDocument();
    });
  });

  it("shows edit and delete buttons when reference prices exist", async () => {
    server.use(
      http.get("/api/reference-prices", () =>
        HttpResponse.json([sampleReferencePrice])
      )
    );
    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-prices");
    await user.click(tab);

    await waitFor(() => {
      expect(screen.getByTestId(`rp-edit-${sampleReferencePrice.id}`)).toBeInTheDocument();
      expect(screen.getByTestId(`rp-delete-${sampleReferencePrice.id}`)).toBeInTheDocument();
    });
  });

  it("opens edit dialog when pencil button is clicked", async () => {
    server.use(
      http.get("/api/reference-prices", () =>
        HttpResponse.json([sampleReferencePrice])
      )
    );
    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-prices");
    await user.click(tab);

    const editBtn = await screen.findByTestId(`rp-edit-${sampleReferencePrice.id}`);
    await user.click(editBtn);

    await waitFor(() => {
      expect(
        screen.getByText("Редактировать базовую цену")
      ).toBeInTheDocument();
    });
  });

  it("calls delete API when delete confirmed via dialog", async () => {
    let deleteCalled = false;
    server.use(
      http.get("/api/reference-prices", () =>
        HttpResponse.json([sampleReferencePrice])
      ),
      http.delete("/api/reference-prices/:id", () => {
        deleteCalled = true;
        return HttpResponse.json({ message: "Удалено" });
      })
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-prices");
    await user.click(tab);

    const deleteBtn = await screen.findByTestId(`rp-delete-${sampleReferencePrice.id}`);
    await user.click(deleteBtn);

    // Confirmation dialog appears
    const confirmBtn = await screen.findByTestId("rp-delete-confirm");
    await user.click(confirmBtn);

    await waitFor(() => expect(deleteCalled).toBe(true));
  });

  it("shows all three invoice stages in the invoices tab", async () => {
    server.use(
      // только три stage-репрезентативных счёта (без счёта-сироты — он здесь шум)
      http.get("/api/dashboard/invoices", () =>
        HttpResponse.json(sampleDashboardInvoices.filter((i) => i.directions.length > 0))
      )
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-invoices");
    await user.click(tab);

    await waitFor(() => {
      expect(screen.getByText("Подтверждён")).toBeInTheDocument();
      expect(screen.getByText("Разобрать")).toBeInTheDocument();
      expect(screen.getByText("Ожидает")).toBeInTheDocument();
    });
  });

  it("uses configured confidence threshold for stage classification", async () => {
    // At threshold 0.9, invoice id=203 (ai_confidence=0.88) becomes
    // "Разобрать" instead of "Ожидает", so two invoices show "Разобрать".
    server.use(
      http.get("/api/settings", () =>
        HttpResponse.json({ api_key_set: true, model: "m", confidence_threshold: 0.9 })
      ),
      // без счёта-сироты — тест про порог классификации на трёх счетах
      http.get("/api/dashboard/invoices", () =>
        HttpResponse.json(sampleDashboardInvoices.filter((i) => i.directions.length > 0))
      )
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-invoices");
    await user.click(tab);

    await waitFor(() => {
      expect(screen.getAllByText("Разобрать")).toHaveLength(2);
      expect(screen.queryByText("Ожидает")).not.toBeInTheDocument();
    });
  });

  it("disables delete for an invoice row whose document is busy (processing)", async () => {
    // document_id=11 (СФ-REVIEW) обрабатывается — busyDocIds должен долететь до InvoiceTable (§6).
    server.use(
      http.get("/api/dashboard/invoices", () =>
        HttpResponse.json(sampleDashboardInvoices.filter((i) => i.directions.length > 0))
      ),
      http.get("/api/invoices/documents", () =>
        HttpResponse.json([
          { id: 10, project_id: 1, filename: "a.pdf", doc_type: "invoice", status: "parsed", uploaded_at: "2026-01-01T00:00:00", invoice_count: 1, has_issues: false, ai_confidence: 0.9, parse_cost_usd: 0, parse_count: 1 },
          { id: 11, project_id: 1, filename: "b.pdf", doc_type: "invoice", status: "processing", uploaded_at: "2026-01-01T00:00:00", invoice_count: 1, has_issues: false, ai_confidence: null, parse_cost_usd: 0, parse_count: 1 },
          { id: 12, project_id: 1, filename: "c.pdf", doc_type: "invoice", status: "parsed", uploaded_at: "2026-01-01T00:00:00", invoice_count: 1, has_issues: false, ai_confidence: 0.5, parse_cost_usd: 0, parse_count: 1 },
        ])
      ),
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-invoices");
    await user.click(tab);

    const busyRow = (await screen.findByText("СФ-REVIEW")).closest("tr")!;
    const busyDelete = within(busyRow).getByRole("button", { name: "Удалить" });
    expect(busyDelete).toBeDisabled();

    const freeRow = screen.getByText("СФ-PENDING").closest("tr")!;
    const freeDelete = within(freeRow).getByRole("button", { name: "Удалить" });
    expect(freeDelete).not.toBeDisabled();
  });

  // ── По месяцам tab ──────────────────────────────────────────────────────

  it("renders По месяцам tab and shows month rows", async () => {
    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-monthly");
    await user.click(tab);

    // Январь и Март присутствуют из фикстуры
    await waitFor(() => {
      expect(screen.getByText(/Январь 2026/)).toBeInTheDocument();
      expect(screen.getByText(/Март 2026/)).toBeInTheDocument();
    });
  });

  it("shows empty month row (Февраль) between data months", async () => {
    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-monthly");
    await user.click(tab);

    await waitFor(() => {
      expect(screen.getByText(/Февраль 2026/)).toBeInTheDocument();
    });
  });

  it("shows empty state in По месяцам when no invoices", async () => {
    server.use(
      http.get("/api/dashboard/monthly-summary", () => HttpResponse.json([]))
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-monthly");
    await user.click(tab);

    await waitFor(() => {
      expect(screen.getByText(/Нет счетов по этому объекту/)).toBeInTheDocument();
    });
  });

  it("clicking a month row navigates to Счета tab with filter applied", async () => {
    server.use(
      http.get("/api/dashboard/invoices", () =>
        HttpResponse.json(sampleDashboardInvoices)
      )
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-monthly");
    await user.click(tab);

    // Click the Январь row (non-empty)
    const janRow = await screen.findByText(/Январь 2026/);
    await user.click(janRow);

    // Should now be on Счета tab with a month filter badge visible
    await waitFor(() => {
      expect(screen.getByText(/Фильтр: Январь 2026/)).toBeInTheDocument();
    });
  });

  it("month filter reset button clears filter", async () => {
    server.use(
      http.get("/api/dashboard/invoices", () =>
        HttpResponse.json(sampleDashboardInvoices)
      )
    );

    const user = userEvent.setup();
    renderProject();

    const tab = await screen.findByTestId("project-tab-monthly");
    await user.click(tab);

    const janRow = await screen.findByText(/Январь 2026/);
    await user.click(janRow);

    const resetBtn = await screen.findByText("Сбросить");
    await user.click(resetBtn);

    await waitFor(() => {
      expect(screen.queryByText(/Фильтр:/)).not.toBeInTheDocument();
    });
  });

  it("CSV export button triggers download with correct filename and BOM content", async () => {
    const user = userEvent.setup();
    renderProject();

    // Navigate to monthly tab before mocking anything (avoids interfering with render)
    const tab = await screen.findByTestId("project-tab-monthly");
    await user.click(tab);
    const exportBtn = await screen.findByRole("button", { name: /Экспорт CSV/i });

    // Set up mocks only after the component has rendered
    const capturedBlobParts: string[] = [];
    const anchorClicks: { download: string }[] = [];
    const origCreateObjectURL = URL.createObjectURL;

    try {
      URL.createObjectURL = (_blob: Blob) => "blob:fake"; // eslint-disable-line @typescript-eslint/no-unused-vars

      const realBlob = globalThis.Blob;
      const capturedBlobPartsRef = capturedBlobParts;
      class CsvCapturingBlob extends realBlob {
        constructor(parts?: BlobPart[], opts?: BlobPropertyBag) {
          super(parts ?? [], opts);
          if (opts?.type?.includes("csv") && parts) {
            parts.forEach((p) => typeof p === "string" && capturedBlobPartsRef.push(p));
          }
        }
      }
      vi.stubGlobal("Blob", CsvCapturingBlob);

      const origCreateElement = document.createElement.bind(document);
      vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
        const el = origCreateElement(tag);
        if (tag === "a") {
          vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {
            anchorClicks.push({ download: (el as HTMLAnchorElement).download });
          });
        }
        return el;
      });

      await user.click(exportBtn);

      // Filename is correct
      expect(anchorClicks).toHaveLength(1);
      expect(anchorClicks[0].download).toMatch(/закупки-по-месяцам-.*\.csv/);

      // Blob starts with UTF-8 BOM and contains expected semicolon-delimited CSV structure
      expect(capturedBlobParts).toHaveLength(1);
      expect(capturedBlobParts[0].startsWith("\uFEFF")).toBe(true);
      expect(capturedBlobParts[0]).toContain("Период;Оборот (₽);Объём (м³);Счетов");
      expect(capturedBlobParts[0]).toContain("Январь 2026");
      expect(capturedBlobParts[0]).toContain("Март 2026");
    } finally {
      URL.createObjectURL = origCreateObjectURL;
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    }
  });

  // ── Period filter ───────────────────────────────────────────────────────

  it("period filter inputs send period_start/period_end to calculations API", async () => {
    const receivedParams: URLSearchParams[] = [];
    server.use(
      http.get("/api/dashboard/calculations", ({ request }) => {
        receivedParams.push(new URL(request.url).searchParams);
        return HttpResponse.json([]);
      })
    );

    renderProject();

    const startInput = await screen.findByTestId("period-start-input");
    const endInput = screen.getByTestId("period-end-input");

    // fireEvent.change: инпуты предзаполнены дефолтным диапазоном, поэтому
    // посегментный user.type на type=date в jsdom не заменяет значение начисто.
    fireEvent.change(startInput, { target: { value: "2025-01-01" } });
    fireEvent.change(endInput, { target: { value: "2025-03-31" } });

    // Wait for debounce to fire and a request with both params to arrive
    await waitFor(() => {
      const withBoth = receivedParams.find(
        (p) => p.get("period_start") === "2025-01-01" && p.get("period_end") === "2025-03-31"
      );
      expect(withBoth).toBeDefined();
    }, { timeout: 2000 });
  });

  it("period reset button clears inputs and removes period params from calculations API", async () => {
    const sampleCalc = {
      material_class_id: 1,
      material_class_name: "В%25",
      direction: "concrete",
      period_start: "2025-01-01",
      period_end: "2025-01-31",
      total_qty: 10,
      material_total: 80000,
      delivery_total: 0,
      avg_price: 8000,
      invoice_count: 1,
      reference_price: null,
      deviation_pct: null,
      deviation_amount: null,
    };
    const receivedParams: URLSearchParams[] = [];
    server.use(
      http.get("/api/dashboard/calculations", ({ request }) => {
        receivedParams.push(new URL(request.url).searchParams);
        return HttpResponse.json([sampleCalc]);
      })
    );

    const user = userEvent.setup();
    renderProject();

    const startInput = await screen.findByTestId("period-start-input");
    const endInput = screen.getByTestId("period-end-input");

    // fireEvent.change: инпуты предзаполнены дефолтным диапазоном, поэтому
    // посегментный user.type на type=date в jsdom не заменяет значение начисто.
    fireEvent.change(startInput, { target: { value: "2025-01-01" } });
    fireEvent.change(endInput, { target: { value: "2025-03-31" } });

    await waitFor(() => {
      expect(receivedParams.some((p) => p.get("period_start") === "2025-01-01")).toBe(true);
    }, { timeout: 2000 });

    receivedParams.length = 0;

    const resetBtn = screen.getByTestId("period-reset-button");
    await user.click(resetBtn);

    await waitFor(() => {
      const withoutPeriod = receivedParams.find(
        (p) => !p.get("period_start") && !p.get("period_end")
      );
      expect(withoutPeriod).toBeDefined();
    }, { timeout: 2000 });

    // После сброса инпуты возвращаются к дефолтному диапазону (первая/последняя СФ
    // из summary), а не к пустым плейсхолдерам — native type=date их игнорирует.
    expect((startInput as HTMLInputElement).value).toBe("2026-01-01");
    expect((endInput as HTMLInputElement).value).toBe("2026-04-15");
  });

  it("invalid date shows error banner and does not send period params to API", async () => {
    const receivedParams: URLSearchParams[] = [];
    server.use(
      http.get("/api/dashboard/calculations", ({ request }) => {
        receivedParams.push(new URL(request.url).searchParams);
        return HttpResponse.json([]);
      })
    );

    const user = userEvent.setup();
    renderProject();

    const endInput = await screen.findByTestId("period-end-input");

    // Simulate badInput — jsdom doesn't implement date segment validation fully,
    // so we trigger the native validity by firing an input event with badInput set.
    Object.defineProperty(endInput, "validity", {
      get: () => ({ badInput: true, valid: false, valueMissing: false }),
      configurable: true,
    });
    await user.click(endInput);
    act(() => {
      endInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent("Некорректная дата");
    });

    // API must not have received any period_end param
    expect(receivedParams.every((p) => !p.get("period_end"))).toBe(true);
  });

  it("reset button clears invalid error state", async () => {
    const user = userEvent.setup();
    renderProject();

    const endInput = await screen.findByTestId("period-end-input");

    Object.defineProperty(endInput, "validity", {
      get: () => ({ badInput: true, valid: false, valueMissing: false }),
      configurable: true,
    });
    await user.click(endInput);
    act(() => {
      endInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    // Restore validity to valid, then click reset
    Object.defineProperty(endInput, "validity", {
      get: () => ({ badInput: false, valid: true, valueMissing: false }),
      configurable: true,
    });
    const resetBtn = screen.getByTestId("period-reset-button");
    await user.click(resetBtn);

    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  // ── Excel export ────────────────────────────────────────────────────────

  it("renders an Экспорт button", async () => {
    renderProject();
    const btn = await screen.findByRole("button", { name: /экспорт/i });
    expect(btn).toBeInTheDocument();
  });

  it("Экспорт button is enabled on load", async () => {
    renderProject();
    const btn = await screen.findByRole("button", { name: /экспорт/i });
    expect(btn).not.toBeDisabled();
  });

  it("clicking Экспорт calls GET /api/export/excel with project_id", async () => {
    const requests: Request[] = [];
    server.use(
      http.get("/api/export/excel", ({ request }) => {
        requests.push(request);
        return HttpResponse.arrayBuffer(new ArrayBuffer(8), {
          headers: {
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82.xlsx",
          },
        });
      })
    );

    const origCreateObjectURL = URL.createObjectURL;
    const anchorClicks: { href: string; download: string }[] = [];
    const origCreateElement = document.createElement.bind(document);

    try {
      URL.createObjectURL = () => "blob:fake-xlsx";
      vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
        const el = origCreateElement(tag);
        if (tag === "a") {
          vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {
            anchorClicks.push({
              href: (el as HTMLAnchorElement).href,
              download: (el as HTMLAnchorElement).download,
            });
          });
        }
        return el;
      });

      const user = userEvent.setup();
      renderProject();

      const btn = await screen.findByRole("button", { name: /экспорт/i });
      await user.click(btn);

      await waitFor(() => expect(requests).toHaveLength(1));

      const url = new URL(requests[0].url);
      expect(url.searchParams.get("project_id")).toBe("1");
      expect(anchorClicks).toHaveLength(1);
      expect(anchorClicks[0].download).toMatch(/отчёт.*\.xlsx$/);
    } finally {
      URL.createObjectURL = origCreateObjectURL;
      vi.restoreAllMocks();
    }
  });

  it("Экспорт button shows Формирую... while request is in flight", async () => {
    let resolve!: () => void;
    const pending = new Promise<void>((res) => { resolve = res; });

    server.use(
      http.get("/api/export/excel", async () => {
        await pending;
        return HttpResponse.arrayBuffer(new ArrayBuffer(8), {
          headers: {
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename*=UTF-8''test.xlsx",
          },
        });
      })
    );

    const origCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = () => "blob:fake";
    const origCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag) => {
      const el = origCreateElement(tag);
      if (tag === "a") vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {});
      return el;
    });

    try {
      const user = userEvent.setup();
      renderProject();

      const btn = await screen.findByRole("button", { name: /экспорт/i });
      await user.click(btn);

      // While request is in-flight
      expect(await screen.findByRole("button", { name: /формирую/i })).toBeDisabled();

      resolve();

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /экспорт/i })).not.toBeDisabled()
      );
    } finally {
      URL.createObjectURL = origCreateObjectURL;
      vi.restoreAllMocks();
    }
  });

  it("clicking Экспорт passes period_start and period_end when inputs are filled", async () => {
    const requests: Request[] = [];
    server.use(
      http.get("/api/export/excel", ({ request }) => {
        requests.push(request);
        return HttpResponse.arrayBuffer(new ArrayBuffer(8), {
          headers: {
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename*=UTF-8''test.xlsx",
          },
        });
      })
    );

    const origCreateObjectURL = URL.createObjectURL;
    const origCreateElement = document.createElement.bind(document);
    try {
      URL.createObjectURL = () => "blob:fake";
      vi.spyOn(document, "createElement").mockImplementation((tag) => {
        const el = origCreateElement(tag);
        if (tag === "a") vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {});
        return el;
      });

      const user = userEvent.setup();
      renderProject();

      const startInput = await screen.findByTestId("period-start-input");
      const endInput = screen.getByTestId("period-end-input");
      fireEvent.change(startInput, { target: { value: "2026-03-01" } });
      fireEvent.change(endInput, { target: { value: "2026-03-31" } });

      const btn = screen.getByRole("button", { name: /экспорт/i });
      await user.click(btn);

      await waitFor(() => expect(requests).toHaveLength(1));
      const params = new URL(requests[0].url).searchParams;
      expect(params.get("period_start")).toBe("2026-03-01");
      expect(params.get("period_end")).toBe("2026-03-31");
    } finally {
      URL.createObjectURL = origCreateObjectURL;
      vi.restoreAllMocks();
    }
  });

  // ── Key-based remount ───────────────────────────────────────────────────

  it("active tab resets to overview when navigating to a different project (ProjectPageWrapper remount)", async () => {
    const user = userEvent.setup();

    const project2 = { ...sampleProject, id: 2, name: "ЖК Тестовый-2" };
    server.use(
      http.get("/api/projects", () => HttpResponse.json([sampleProject, project2])),
    );

    // Replicates App.tsx ProjectPageWrapper: key={id} causes full remount on route change.
    function ProjectPageKeyed() {
      const { id } = useParams<{ id: string }>();
      return <ProjectPage key={id} />;
    }

    renderWithProviders(
      <>
        <Link to="/projects/2" data-testid="nav-p2">P2</Link>
        <Routes>
          <Route path="/projects/:id" element={<ProjectPageKeyed />} />
        </Routes>
      </>,
      { initialRoute: "/projects/1" },
    );

    // Switch to invoices tab
    const invoicesTab = await screen.findByTestId("project-tab-invoices");
    await user.click(invoicesTab);
    await waitFor(() => {
      expect(invoicesTab).toHaveAttribute("aria-selected", "true");
    });

    // Navigate to a different project — ProjectPageKeyed remounts ProjectPage with fresh state
    await user.click(screen.getByTestId("nav-p2"));

    // Overview tab must be active — useState("overview") re-initialised on remount
    await waitFor(() => {
      expect(screen.getByTestId("project-tab-overview")).toHaveAttribute("aria-selected", "true");
    });
  });

  // ── Supplier exclusions ─────────────────────────────────────────────────

  describe("Поставщики tab — supplier exclusions", () => {
    const sampleSupplierRow = { id: 10, name: "ООО Бетон-Строй", inn: "7700000001", invoice_count: 3 };
    const sampleSupplierRow2 = { id: 11, name: "ИП Иванов", inn: null, invoice_count: 1 };

    it("renders supplier rows from /projects/:id/suppliers", async () => {
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow, sampleSupplierRow2])
        )
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));
      await waitFor(() => {
        expect(screen.getByText("ООО Бетон-Строй")).toBeInTheDocument();
        expect(screen.getByText("ИП Иванов")).toBeInTheDocument();
      });
    });

    it("shows empty state when no suppliers", async () => {
      // Default handler already returns [] — no override needed
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));
      await waitFor(() => {
        expect(screen.getByText("Нет поставщиков")).toBeInTheDocument();
      });
    });

    it("excluded supplier checkbox is unchecked and name is struck through", async () => {
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        ),
        http.get("/api/projects/:projectId/supplier-exclusions", () =>
          HttpResponse.json([sampleSupplierRow.id])
        )
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));
      await waitFor(() => {
        const nameEl = screen.getByText(sampleSupplierRow.name);
        expect(nameEl).toHaveClass("line-through");
        const checkbox = screen.getByRole("checkbox", { name: /включить/i });
        expect(checkbox).not.toBeChecked();
      });
    });

    it("unchecking an included supplier opens the reason popover", async () => {
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        )
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));

      const checkbox = await screen.findByRole("checkbox", { name: /исключить/i });
      await user.click(checkbox);

      await waitFor(() => {
        expect(screen.getByLabelText(/Причина исключения/i)).toBeInTheDocument();
      });
    });

    it("confirming exclusion via button triggers POST and closes popover", async () => {
      let postCalled = false;
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        ),
        http.post("/api/projects/:projectId/supplier-exclusions/:supplierId", () => {
          postCalled = true;
          return new HttpResponse(null, { status: 204 });
        })
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));

      const checkbox = await screen.findByRole("checkbox", { name: /исключить/i });
      await user.click(checkbox);

      const reasonInput = await screen.findByLabelText(/Причина исключения/i);
      await user.type(reasonInput, "Нерепрезентативная цена");

      const confirmBtn = screen.getByRole("button", { name: /^Исключить$/i });
      await user.click(confirmBtn);

      await waitFor(() => {
        expect(postCalled).toBe(true);
        expect(screen.queryByLabelText(/Причина исключения/i)).not.toBeInTheDocument();
      });
    });

    it("pressing Escape closes the reason popover without POST", async () => {
      let postCalled = false;
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        ),
        http.post("/api/projects/:projectId/supplier-exclusions/:supplierId", () => {
          postCalled = true;
          return new HttpResponse(null, { status: 204 });
        })
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));

      const checkbox = await screen.findByRole("checkbox", { name: /исключить/i });
      await user.click(checkbox);

      await screen.findByLabelText(/Причина исключения/i);
      await user.keyboard("{Escape}");

      await waitFor(() => {
        expect(screen.queryByLabelText(/Причина исключения/i)).not.toBeInTheDocument();
      });
      expect(postCalled).toBe(false);
    });

    it("re-including an excluded supplier triggers DELETE without popover", async () => {
      let deleteCalled = false;
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        ),
        http.get("/api/projects/:projectId/supplier-exclusions", () =>
          HttpResponse.json([sampleSupplierRow.id])
        ),
        http.delete("/api/projects/:projectId/supplier-exclusions/:supplierId", () => {
          deleteCalled = true;
          return new HttpResponse(null, { status: 204 });
        })
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("project-tab-suppliers"));

      const checkbox = await screen.findByRole("checkbox", { name: /включить/i });
      await user.click(checkbox);

      await waitFor(() => expect(deleteCalled).toBe(true));
      // Reason popover must NOT appear (DELETE is immediate)
      expect(screen.queryByLabelText(/Причина исключения/i)).not.toBeInTheDocument();
    });

    it("overview banner appears when exclusions are present", async () => {
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        ),
        http.get("/api/projects/:projectId/supplier-exclusions", () =>
          HttpResponse.json([sampleSupplierRow.id])
        )
      );
      renderProject();
      await waitFor(() => {
        expect(
          screen.getByText(/исключ.*поставщик.*из расчётов/i)
        ).toBeInTheDocument();
      });
    });

    it("overview banner Управление link switches to suppliers tab", async () => {
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow])
        ),
        http.get("/api/projects/:projectId/supplier-exclusions", () =>
          HttpResponse.json([sampleSupplierRow.id])
        )
      );
      const user = userEvent.setup();
      renderProject();

      const mgmtLink = await screen.findByRole("button", { name: /управление/i });
      await user.click(mgmtLink);

      await waitFor(() => {
        expect(screen.getByTestId("project-tab-suppliers")).toHaveAttribute("aria-selected", "true");
      });
    });

    it("tab counter increments with supplier count", async () => {
      server.use(
        http.get("/api/projects/:projectId/suppliers", () =>
          HttpResponse.json([sampleSupplierRow, sampleSupplierRow2])
        )
      );
      renderProject();
      await waitFor(() => {
        expect(screen.getByTestId("project-tab-suppliers")).toHaveTextContent("Поставщики · 2");
      });
    });
  });

  // ── Направления (спека §3, §7.2–7.3) ─────────────────────────────────────

  describe("ProjectPage directions", () => {
    it("mono-object: defaults to its direction with tabs visible (ADR #10)", async () => {
      renderProject(); // дефолтная фикстура: directions=[concrete]
      expect(await screen.findByTestId("project-page-tabs-list")).toBeInTheDocument();
      expect(screen.getByTestId("direction-concrete")).toHaveAttribute("aria-pressed", "true");
    });

    it("multi-object: defaults to «Все» — no tabs, summary KPIs with breakdown", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      renderProject();
      await screen.findByTestId("direction-switcher");
      expect(screen.queryByTestId("project-page-tabs-list")).not.toBeInTheDocument();
      expect(screen.getByText("Объёмы")).toBeInTheDocument();
      expect(screen.getByText(/12,4/)).toBeInTheDocument(); // т арматуры
      expect(screen.getByText(/Компенсация (подрядчик|за весь период)/)).toBeInTheDocument();
    });

    it("empty object: legacy tabs, no switcher (ADR #11)", async () => {
      mockSummary(sampleDashboardSummaryEmpty);
      renderProject();
      expect(await screen.findByTestId("project-page-tabs-list")).toBeInTheDocument();
      expect(screen.queryByTestId("direction-switcher")).not.toBeInTheDocument();
    });

    it("summary loading: цельный skeleton, без табов/switcher (§4.2)", async () => {
      server.use(
        http.get("/api/dashboard/summary", async () => {
          await delay("infinite");                 // держим загрузку summary
          return HttpResponse.json(sampleDashboardSummaryMulti);
        }),
      );
      renderProject();
      expect(await screen.findByTestId("project-page-skeleton")).toBeInTheDocument();
      expect(screen.queryByTestId("project-page-tabs-list")).not.toBeInTheDocument();
      expect(screen.queryByTestId("direction-switcher")).not.toBeInTheDocument();
    });

    it("invoices loading: обзор показывает лоадер, а не мигающий «Нет счетов»", async () => {
      // Сводка приходит сразу (обзор «Все» рендерится), но список счетов ещё грузится:
      // в секции «Счета» должен быть лоадер, а не пустой стейт «Нет счетов.».
      mockSummary(sampleDashboardSummaryMulti);
      let release!: () => void;
      const pending = new Promise<void>((r) => { release = r; });
      server.use(
        http.get("/api/dashboard/invoices", async () => {
          await pending;
          return HttpResponse.json([]);
        }),
      );
      renderProject();

      // Обзор отрисован (сводка загрузилась), но счета ещё в полёте → лоадер, не пусто.
      await screen.findByTestId("direction-switcher");
      expect(await screen.findByTestId("invoices-overview-loading")).toBeInTheDocument();
      expect(screen.queryByText("Нет счетов.")).not.toBeInTheDocument();

      // Счета догрузились пустыми — честный пустой стейт, лоадер исчез.
      release();
      expect(await screen.findByText("Нет счетов.")).toBeInTheDocument();
      expect(screen.queryByTestId("invoices-overview-loading")).not.toBeInTheDocument();
    });

    it("summary error: состояние ошибки с «Повторить», НЕ legacy-табы", async () => {
      server.use(
        http.get("/api/dashboard/summary", () => new HttpResponse(null, { status: 500 })),
      );
      renderProject();
      expect(await screen.findByText("Не удалось загрузить сводку")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
      expect(screen.queryByTestId("project-page-tabs-list")).not.toBeInTheDocument();
    });

    it("summary 500 → «Повторить» → 200: нормальный экран", async () => {
      let calls = 0;
      server.use(
        http.get("/api/dashboard/summary", () => {
          calls += 1;
          return calls === 1
            ? new HttpResponse(null, { status: 500 })
            : HttpResponse.json(sampleDashboardSummaryMulti);
        }),
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByRole("button", { name: "Повторить" }));
      await screen.findByTestId("direction-switcher");   // сводка догрузилась
    });

    it("projects error: ошибка списка, НЕ «Объект не найден»", async () => {
      server.use(
        http.get("/api/projects", () => new HttpResponse(null, { status: 500 })),
      );
      renderProject();
      expect(await screen.findByText("Не удалось загрузить объекты")).toBeInTheDocument();
      expect(screen.queryByText("Объект не найден")).not.toBeInTheDocument();
    });

    it("?direction=rebar opens rebar mode directly (criterion #3)", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      renderProject("1", "?direction=rebar");
      expect(await screen.findByTestId("project-page-tabs-list")).toBeInTheDocument();
      expect(screen.getByTestId("direction-rebar")).toHaveAttribute("aria-pressed", "true");
    });

    it("garbage ?direction= falls back to auto-default and never hits API with it", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      const seen: string[] = [];
      server.use(
        http.get("/api/dashboard/invoices", ({ request }) => {
          seen.push(new URL(request.url).searchParams.get("direction") ?? "");
          return HttpResponse.json([]);
        }),
      );
      renderProject("1", "?direction=trash");
      await screen.findByTestId("direction-switcher");
      expect(screen.getByTestId("direction-all")).toHaveAttribute("aria-pressed", "true");
      await waitFor(() => expect(seen.length).toBeGreaterThan(0));
      expect(seen).not.toContain("trash"); // гейт §7.2: запрос не ушёл с мусором
    });

    it("switching direction resets active tab to overview and updates URL", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("direction-concrete"));
      // в режиме направления видим табы, активен «Обзор»
      expect(await screen.findByTestId("project-tab-overview")).toHaveAttribute("aria-selected", "true");
      await user.click(screen.getByTestId("project-tab-invoices"));
      await waitFor(() => {
        expect(screen.getByTestId("project-tab-invoices")).toHaveAttribute("aria-selected", "true");
      });
      await user.click(screen.getByTestId("direction-rebar"));
      await waitFor(() => {
        expect(screen.getByTestId("project-tab-overview")).toHaveAttribute("aria-selected", "true");
      });
    });

    it("«Все»: alert opens errors view, back link returns", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      server.use(
        http.get("/api/invoices/documents", () =>
          HttpResponse.json([
            {
              id: 1,
              project_id: 1,
              filename: "x.pdf",
              doc_type: "invoice",
              status: "error",
              uploaded_at: "2026-05-01T10:00:00",
              invoice_count: 0,
              has_issues: true,
              ai_confidence: null,
              invoices: [],
            },
          ])
        ),
      );
      const user = userEvent.setup();
      renderProject();
      await user.click(await screen.findByTestId("unrecognized-alert"));
      expect(await screen.findByTestId("project-errors-view")).toBeInTheDocument();
      // направление не сменилось
      expect(screen.getByTestId("direction-all")).toHaveAttribute("aria-pressed", "true");
      await user.click(screen.getByRole("button", { name: /к сводке/ }));
      await waitFor(() => {
        expect(screen.queryByTestId("project-errors-view")).not.toBeInTheDocument();
      });
    });

    it("«Все»: no period table and no configure-prices button", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      renderProject();
      await screen.findByTestId("direction-switcher");
      expect(screen.queryByRole("button", { name: /настроить базовые/i })).not.toBeInTheDocument();
    });

    it("export in direction mode sends ?direction and adds direction suffix to filename", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      const requests: Request[] = [];
      server.use(
        http.get("/api/export/excel", ({ request }) => {
          requests.push(request);
          return HttpResponse.arrayBuffer(new ArrayBuffer(8), {
            headers: {
              "Content-Type":
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "Content-Disposition": "attachment; filename*=UTF-8''test.xlsx",
            },
          });
        })
      );

      const origCreateObjectURL = URL.createObjectURL;
      const origCreateElement = document.createElement.bind(document);
      const anchorClicks: { download: string }[] = [];
      try {
        URL.createObjectURL = () => "blob:fake";
        vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
          const el = origCreateElement(tag);
          if (tag === "a") {
            vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {
              anchorClicks.push({ download: (el as HTMLAnchorElement).download });
            });
          }
          return el;
        });

        const user = userEvent.setup();
        renderProject("1", "?direction=rebar");
        await screen.findByTestId("project-page-tabs-list");

        await user.click(screen.getByRole("button", { name: /экспорт/i }));

        await waitFor(() => expect(requests).toHaveLength(1));
        expect(new URL(requests[0].url).searchParams.get("direction")).toBe("rebar");
        expect(anchorClicks).toHaveLength(1);
        expect(anchorClicks[0].download).toBe("отчёт-ЖК Радуга-Арматура.xlsx");
      } finally {
        URL.createObjectURL = origCreateObjectURL;
        vi.restoreAllMocks();
      }
    });

    it("direction mode passes direction to scoped hooks (MSW)", async () => {
      mockSummary(sampleDashboardSummaryMulti);
      const seen: string[] = [];
      server.use(
        http.get("/api/dashboard/invoices", ({ request }) => {
          seen.push(new URL(request.url).searchParams.get("direction") ?? "none");
          return HttpResponse.json([]);
        }),
      );
      renderProject("1", "?direction=rebar");
      await screen.findByTestId("project-page-tabs-list");
      await waitFor(() => expect(seen).toContain("rebar"));
    });
  });

  it("shows all invoices (including orphan) in «Все направления» summary", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    renderProject("1", "?direction=all");
    // СФ-OTHER (directions=[]) виден в общем списке сводки
    expect(await screen.findByText("СФ-OTHER")).toBeInTheDocument();
    // и несёт бейдж «прочее»
    expect(screen.getByText("прочее")).toBeInTheDocument();
  });

  it("«Прочие · N» KPI filters the invoice list to orphans only", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    const user = userEvent.setup();
    renderProject("1", "?direction=all");
    // дождаться полного списка (есть бетонный СФ-CONFIRMED)
    expect(await screen.findByText("СФ-CONFIRMED")).toBeInTheDocument();
    // клик по кликабельной строке «Прочие» (label содержит число прочих = 1)
    await user.click(screen.getByRole("button", { name: /Прочие/i }));
    await waitFor(() => {
      // после фильтра остаются только сироты
      expect(screen.getByText("СФ-OTHER")).toBeInTheDocument();
      expect(screen.queryByText("СФ-CONFIRMED")).not.toBeInTheDocument();
    });
  });
});
