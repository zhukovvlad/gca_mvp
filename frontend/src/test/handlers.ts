import { http, HttpResponse } from "msw";
import { sampleAdminUsers } from "./fixtures";

export function resetHandlerState() {
  // Мутируемого состояния пока нет; функция сохранена для симметрии setup.ts
}

export const handlers = [
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),

  // Auth
  http.get("/api/auth/me", () =>
    HttpResponse.json({ id: 1, email: "test@example.com", role: "admin" })
  ),
  http.post("/api/auth/login", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/logout", () => HttpResponse.json({ status: "ok" })),
  http.post("/api/auth/refresh", () => HttpResponse.json({ status: "ok" })),

  // Units
  http.get("/api/units", () =>
    HttpResponse.json([
      { id: 1, code: "TON", name: "Тонна", symbol: "т", dimension: "mass", base_unit_id: null },
      { id: 3, code: "M3", name: "Куб. метр", symbol: "м³", dimension: "volume", base_unit_id: null },
      { id: 5, code: "M2", name: "Кв. метр", symbol: "м²", dimension: "area", base_unit_id: null },
    ])
  ),

  // Admin: пользователи
  http.get("/api/admin/users", ({ request }) => {
    const url = new URL(request.url);
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const page = Number(url.searchParams.get("page") ?? 1) || 1;
    const page_size = Number(url.searchParams.get("page_size") ?? 20) || 20;
    const filtered = q
      ? sampleAdminUsers.filter((u) => u.email.toLowerCase().includes(q))
      : sampleAdminUsers;
    const start = (page - 1) * page_size;
    return HttpResponse.json({
      items: filtered.slice(start, start + page_size),
      total: filtered.length,
      page,
      page_size,
    });
  }),
  http.post("/api/admin/users", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json(
      {
        id: 100,
        email: body.email ?? "new@example.com",
        role: body.role ?? "member",
        is_active: body.is_active ?? true,
      },
      { status: 201 }
    );
  }),
  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(body, "role") && body.role === null) {
      return HttpResponse.json({ detail: "Поле role не может быть null" }, { status: 422 });
    }
    if (Object.prototype.hasOwnProperty.call(body, "is_active") && body.is_active === null) {
      return HttpResponse.json({ detail: "Поле is_active не может быть null" }, { status: 422 });
    }
    return HttpResponse.json({
      id: Number(params.id),
      email: "a.petrov@example.com",
      role: (body.role as string) ?? "admin",
      is_active: (body.is_active as boolean) ?? true,
    });
  }),
  http.post("/api/admin/users/:id/reset-password", ({ params }) =>
    HttpResponse.json({ id: Number(params.id), email: "a.petrov@example.com", password: "Xk7m-Pq9L-vf2Z" })
  ),
];
