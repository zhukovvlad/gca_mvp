import { http, HttpResponse } from "msw";
import {
  sampleProject,
  sampleMaterialClass,
  sampleDocument,
  sampleDashboardSummary,
  sampleDashboardInvoices,
  sampleMonthlySummary,
  sampleReferencePrice,
  sampleSupplier,
  sampleSupplierProjectRows,
  sampleSupplierInvoices,
  sampleUnits,
  sampleMaterialTypes,
} from "./fixtures";

// Mutable state so that verify/unverify mutations are reflected in subsequent GETs.
// Keyed by invoice id (as string) so each invoice's state is independent.
const invoiceVerifiedById = new Map<string, boolean>();

export function resetHandlerState() {
  invoiceVerifiedById.clear();
}

export function setHandlerVerified(invoiceId: number | string, v: boolean) {
  invoiceVerifiedById.set(String(invoiceId), v);
}

export const handlers = [
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),

  http.get("/api/projects", () => HttpResponse.json([sampleProject])),
  http.post("/api/projects", () => HttpResponse.json(sampleProject)),
  http.put("/api/projects/:id", () => HttpResponse.json(sampleProject)),
  http.delete("/api/projects/:id", () => HttpResponse.json({ message: "Удалено" })),

  http.get("/api/material-classes", () => HttpResponse.json([sampleMaterialClass])),
  http.post("/api/material-classes", () => HttpResponse.json(sampleMaterialClass)),

  http.get("/api/units", () => HttpResponse.json(sampleUnits)),
  http.get("/api/material-types", () => HttpResponse.json(sampleMaterialTypes)),

  http.get("/api/reference-prices", () => HttpResponse.json([])),
  http.post("/api/reference-prices", () => HttpResponse.json({ id: 1 })),
  http.patch("/api/reference-prices/:id", () => HttpResponse.json(sampleReferencePrice)),
  http.delete("/api/reference-prices/:id", () => HttpResponse.json({ message: "Удалено" })),

  http.get("/api/invoices/documents", () => HttpResponse.json([sampleDocument])),
  http.get("/api/invoices/documents/:id", () => {
    const inv0 = sampleDocument.invoices[0];
    const verified = invoiceVerifiedById.get(String(inv0.id)) ?? false;
    return HttpResponse.json({
      ...sampleDocument,
      invoices: [{
        ...inv0,
        verified,
        verified_at: verified ? "2026-05-14T12:00:00" : null,
      }],
    });
  }),
  http.post("/api/invoices/upload", () => HttpResponse.json(sampleDocument)),
  http.post("/api/invoices/documents/:id/reparse", () => HttpResponse.json(sampleDocument)),
  http.post("/api/invoices/documents/:id/deskew-reparse", () => HttpResponse.json(sampleDocument)),
  http.put("/api/invoices/:id", ({ params }) =>
    HttpResponse.json({ message: "Сохранено", invoice_id: Number(params.id), warnings: [] })
  ),
  http.delete("/api/invoices/bulk", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { ids?: number[] };
    const ids = Array.isArray(body.ids) ? body.ids : [];
    return HttpResponse.json({ deleted: ids.length, skipped: [] });
  }),
  http.delete("/api/invoices/:id", () => HttpResponse.json({ message: "СФ удалена" })),
  http.delete("/api/invoices/documents/:id", () =>
    HttpResponse.json({ message: "Удалено" })
  ),
  http.post("/api/invoices/:id/verify", ({ params }) => {
    const id = String(params.id);
    invoiceVerifiedById.set(id, true);
    return HttpResponse.json({ message: "Проверено", invoice_id: Number(id), verified_at: "2026-05-14T12:00:00" });
  }),
  http.post("/api/invoices/:id/unverify", ({ params }) => {
    const id = String(params.id);
    invoiceVerifiedById.set(id, false);
    return HttpResponse.json({ message: "Отметка снята", invoice_id: Number(id) });
  }),

  http.get("/api/dashboard/summary", () => HttpResponse.json(sampleDashboardSummary)),
  http.get("/api/dashboard/invoices", () =>
    HttpResponse.json(
      sampleDashboardInvoices.map((inv) => {
        const verified = invoiceVerifiedById.get(String(inv.id)) ?? inv.verified;
        return {
          ...inv,
          verified,
          verified_at: verified ? (inv.verified_at ?? "2026-05-14T12:00:00") : null,
        };
      })
    )
  ),
  http.get("/api/dashboard/calculations", () => HttpResponse.json([])),
  http.get("/api/dashboard/monthly-summary", () => HttpResponse.json(sampleMonthlySummary)),

  http.get("/api/settings", () =>
    HttpResponse.json({
      provider: "openrouter",
      can_edit_model: true,
      cost_available: true,
      api_key_set: true,
      model: "anthropic/claude-sonnet-4.6",
      confidence_threshold: 0.7,
    })
  ),
  http.put("/api/settings", () => HttpResponse.json({ message: "Настройки сохранены" })),

  // Suppliers
  http.get("/api/suppliers", () => HttpResponse.json([sampleSupplier])),
  http.get("/api/suppliers/:id", () => HttpResponse.json(sampleSupplier)),
  http.put("/api/suppliers/:id", () => HttpResponse.json({ id: 1, name: "ООО «ЭРКОН»", inn: "7723746396" })),
  http.post("/api/suppliers/:id/merge", () => HttpResponse.json({ id: 1, name: "ООО «ЭРКОН»", inn: "7723746396" })),
  http.get("/api/suppliers/:id/projects", () => HttpResponse.json(sampleSupplierProjectRows)),
  http.get("/api/suppliers/:id/invoices-list", () => HttpResponse.json(sampleSupplierInvoices)),

  // Project suppliers & exclusions
  http.get("/api/projects/:projectId/suppliers", () => HttpResponse.json([])),
  http.get("/api/projects/:projectId/supplier-exclusions", () => HttpResponse.json([])),
  http.post("/api/projects/:projectId/supplier-exclusions/:supplierId", () =>
    new HttpResponse(null, { status: 204 })
  ),
  http.delete("/api/projects/:projectId/supplier-exclusions/:supplierId", () =>
    new HttpResponse(null, { status: 204 })
  ),

  // Corridors (fallback hierarchy)
  http.get("/api/projects/:projectId/corridors", () =>
    HttpResponse.json({ types: [], classes: [] }),
  ),
  http.put("/api/projects/:projectId/corridors/type/:materialType", () =>
    HttpResponse.json({ material_type: "concrete", is_compensable: true, corridor_pct: 5 }),
  ),
  http.delete("/api/projects/:projectId/corridors/type/:materialType", () =>
    new HttpResponse(null, { status: 204 }),
  ),
  http.put("/api/projects/:projectId/corridors/class/:materialClassId", () =>
    HttpResponse.json({ material_class_id: 1, is_compensable: true, corridor_pct: 7 }),
  ),
  http.delete("/api/projects/:projectId/corridors/class/:materialClassId", () =>
    new HttpResponse(null, { status: 204 }),
  ),

  // Export
  http.get("/api/export/excel", () =>
    HttpResponse.arrayBuffer(new ArrayBuffer(8), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": 'attachment; filename*=UTF-8\'\'%D0%BE%D1%82%D1%87%D1%91%D1%82.xlsx',
      },
    })
  ),

  // Auth
  http.get("/api/auth/me", () =>
    HttpResponse.json({
      id: 1,
      email: "test@example.com",
      org_id: 1,
      org_role: "admin",
      is_superuser: false,
      organization: { id: 1, name: "Тест Орг", inn: null, kind: "customer" },
    })
  ),
  http.post("/api/auth/login", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/logout", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/refresh", () => HttpResponse.json({ status: "ok" })),

  // Admin (superuser)
  http.get("/api/admin/organizations", () =>
    HttpResponse.json([
      {
        id: 1,
        name: "ООО «СтройГрад»",
        inn: "7705123456",
        kind: "customer",
        created_at: "2026-05-01T10:00:00Z",
        user_count: 3,
        project_count: 2,
      },
    ])
  ),
  http.get("/api/admin/organizations/:id", ({ params }) =>
    HttpResponse.json({
      id: Number(params.id),
      name: "ООО «СтройГрад»",
      inn: "7705123456",
      kind: "customer",
      created_at: "2026-05-01T10:00:00Z",
      users: [
        {
          id: 1,
          email: "a.petrov@stroygrad.ru",
          org_id: Number(params.id),
          org_role: "superadmin",
          is_superuser: false,
          is_active: true,
        },
      ],
      projects: [],
    })
  ),
  http.post("/api/admin/organizations", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json(
      { id: 1, name: body.name ?? "ООО Новая", inn: body.inn ?? null, kind: body.kind ?? "customer" },
      { status: 201 }
    );
  }),
  http.patch("/api/admin/organizations/:id", async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(body, "name") && body.name === null) {
      return HttpResponse.json({ detail: "Поле name не может быть null" }, { status: 422 });
    }
    if (Object.prototype.hasOwnProperty.call(body, "kind") && body.kind === null) {
      return HttpResponse.json({ detail: "Поле kind не может быть null" }, { status: 422 });
    }
    const inn = Object.prototype.hasOwnProperty.call(body, "inn") ? (body.inn as string | null) : "7705123456";
    return HttpResponse.json({
      id: Number(params.id),
      name: (body.name as string) ?? "ООО «СтройГрад»",
      inn,
      kind: (body.kind as string) ?? "customer",
    });
  }),
  http.post("/api/admin/organizations/:id/users", async ({ request, params }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json(
      {
        id: 2,
        email: body.email ?? "new@example.com",
        org_id: Number(params.id),
        org_role: body.org_role ?? "member",
        is_superuser: false,
        is_active: body.is_active ?? true,
      },
      { status: 201 }
    );
  }),
  http.get("/api/admin/users", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const page = Number(url.searchParams.get("page") ?? 1) || 1;
    const page_size = Number(url.searchParams.get("page_size") ?? 20) || 20;
    const all = [
      {
        id: 1,
        email: "a.petrov@stroygrad.ru",
        org_id: 1,
        org_name: "ООО «СтройГрад»",
        org_role: "superadmin",
        is_superuser: false,
        is_active: true,
      },
    ];
    const filtered = q
      ? all.filter((u) => u.email.toLowerCase().includes(q) || (u.org_name ?? "").toLowerCase().includes(q))
      : all;
    const start = (page - 1) * page_size;
    return HttpResponse.json({
      items: filtered.slice(start, start + page_size),
      total: filtered.length,
      page,
      page_size,
    });
  }),
  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(body, "org_role") && body.org_role === null) {
      return HttpResponse.json({ detail: "Поле org_role не может быть null" }, { status: 422 });
    }
    if (Object.prototype.hasOwnProperty.call(body, "is_active") && body.is_active === null) {
      return HttpResponse.json({ detail: "Поле is_active не может быть null" }, { status: 422 });
    }
    return HttpResponse.json({
      id: Number(params.id),
      email: "a.petrov@stroygrad.ru",
      org_id: 1,
      org_role: (body.org_role as string) ?? "admin",
      is_superuser: false,
      is_active: (body.is_active as boolean) ?? true,
    });
  }),
  http.post("/api/admin/users/:id/reset-password", ({ params }) =>
    HttpResponse.json({ id: Number(params.id), email: "a.petrov@stroygrad.ru", password: "Xk7m-Pq9L-vf2Z" })
  ),
  http.post("/api/admin/organizations/:id/projects", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json(
      { project_id: body.project_id ?? 1, project_name: "ЖК Радуга", project_role: body.project_role ?? "customer" },
      { status: 201 }
    );
  }),
  http.delete("/api/admin/organizations/:id/projects/:projectId", () => new HttpResponse(null, { status: 204 })),
];
