import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import AdminOrgDetail from "./AdminOrgDetail";
import type { User } from "@/types/auth";

const SUPERUSER: User = {
  id: 99,
  email: "root@platform.ru",
  org_id: null,
  org_role: null,
  is_superuser: true,
  organization: null,
};

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/admin/organizations/:id" element={<AdminOrgDetail />} />
    </Routes>,
    { initialRoute: "/admin/organizations/1", initialUser: SUPERUSER },
  );
}

describe("AdminOrgDetail — редактирование организации", () => {
  it("кнопка «Редактировать» открывает диалог с предзаполненными полями", async () => {
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(screen.getByText("ООО «СтройГрад»")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Редактировать/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("ООО «СтройГрад»");
    expect(within(dialog).getByLabelText(/ИНН/)).toHaveValue("7705123456");
  });

  it("сохранение отправляет PATCH с изменёнными полями", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();

    server.use(
      http.patch("/api/admin/organizations/:id", async ({ request, params }) => {
        const body = await request.json();
        onPatch({ id: params.id, body });
        return HttpResponse.json({ id: Number(params.id), name: "ООО Новое", inn: "7705123456", kind: "contractor" });
      }),
    );

    renderDetail();
    await waitFor(() => expect(screen.getByText("ООО «СтройГрад»")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Редактировать/ }));
    const dialog = await screen.findByRole("dialog");

    const nameInput = within(dialog).getByLabelText("Название");
    await user.clear(nameInput);
    await user.type(nameInput, "ООО Новое");
    await user.click(within(dialog).getByRole("button", { name: /Подрядчик/ }));
    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1));
    expect(onPatch.mock.calls[0][0].id).toBe("1");
    expect(onPatch.mock.calls[0][0].body).toMatchObject({ name: "ООО Новое", kind: "contractor" });
  });
});

/** Подменяет GET org-detail: добавляет member (деактивируемый) и привязанный проект. */
function useDetailWithMemberAndProject() {
  server.use(
    http.get("/api/admin/organizations/:id", ({ params }) =>
      HttpResponse.json({
        id: Number(params.id),
        name: "ООО «СтройГрад»",
        inn: "7705123456",
        kind: "customer",
        created_at: "2026-05-01T10:00:00Z",
        users: [
          { id: 1, email: "boss@stroygrad.ru", org_id: 1, org_role: "superadmin", is_superuser: false, is_active: true },
          { id: 2, email: "ivan@stroygrad.ru", org_id: 1, org_role: "member", is_superuser: false, is_active: true },
        ],
        projects: [{ project_id: 5, project_name: "ЖК Радуга", project_role: "customer" }],
      }),
    ),
  );
}

describe("AdminOrgDetail — подтверждение деструктивных действий", () => {
  it("деактивация: API не вызывается до подтверждения в AlertDialog", async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    useDetailWithMemberAndProject();
    server.use(
      http.patch("/api/admin/users/:id", async ({ request, params }) => {
        onPatch({ id: params.id, body: await request.json() });
        return HttpResponse.json({
          id: Number(params.id), email: "ivan@stroygrad.ru", org_id: 1,
          org_role: "member", is_superuser: false, is_active: false,
        });
      }),
    );

    renderDetail();
    await waitFor(() => expect(screen.getByText("ivan@stroygrad.ru")).toBeInTheDocument());

    // Кликаем «Деактивировать» в строке member-пользователя (ivan), а не superadmin
    const ivanRow = screen.getByText("ivan@stroygrad.ru").closest("tr") as HTMLElement;
    await user.click(within(ivanRow).getByRole("button", { name: "Деактивировать" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(onPatch).not.toHaveBeenCalled();

    // Подтверждение в диалоге → запрос уходит
    await user.click(within(dialog).getByRole("button", { name: "Деактивировать" }));
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1));
    expect(onPatch.mock.calls[0][0]).toMatchObject({ id: "2", body: { is_active: false } });
  });

  it("снятие доступа к проекту: API не вызывается до подтверждения", async () => {
    const user = userEvent.setup();
    const onUnlink = vi.fn();
    useDetailWithMemberAndProject();
    server.use(
      http.delete("/api/admin/organizations/:id/projects/:projectId", ({ params }) => {
        onUnlink({ orgId: params.id, projectId: params.projectId });
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderDetail();
    await waitFor(() => expect(screen.getByText("ООО «СтройГрад»")).toBeInTheDocument());

    await user.click(screen.getByRole("tab", { name: /Доступ к проектам/ }));
    await waitFor(() => expect(screen.getByText("ЖК Радуга")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Снять доступ" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(onUnlink).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Снять доступ" }));
    await waitFor(() => expect(onUnlink).toHaveBeenCalledTimes(1));
    expect(onUnlink.mock.calls[0][0]).toMatchObject({ orgId: "1", projectId: "5" });
  });
});

describe("AdminOrgDetail — вкладки (shadcn Tabs)", () => {
  it("переключение на «Доступ к проектам» показывает форму выдачи доступа", async () => {
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(screen.getByText("ООО «СтройГрад»")).toBeInTheDocument());

    // По умолчанию активна вкладка «Пользователи»
    expect(screen.getByText("Добавить пользователя")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Доступ к проектам/ }));

    await waitFor(() => {
      expect(screen.getByText("Дать доступ к проекту")).toBeInTheDocument();
    });
  });
});
