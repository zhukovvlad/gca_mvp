import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import AdminOrgCreate from "./AdminOrgCreate";
import type { User } from "@/types/auth";

const SUPERUSER: User = {
  id: 99,
  email: "root@platform.ru",
  org_id: null,
  org_role: null,
  is_superuser: true,
  organization: null,
};

describe("AdminOrgCreate", () => {
  it("кнопка «Сгенерировать» заполняет поле пароля", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminOrgCreate />, { initialUser: SUPERUSER });

    const passwordInput = screen.getByLabelText("Пароль") as HTMLInputElement;
    // По умолчанию пароль уже сгенерирован при монтировании
    const initial = passwordInput.value;
    expect(initial).toMatch(/^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$/);

    await user.click(screen.getByRole("button", { name: /Сгенерировать/ }));
    // Значение остаётся валидным паролем (может совпасть, но формат сохраняется)
    expect(passwordInput.value).toMatch(/^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$/);
  });

  it("сабмит создаёт организацию, затем первого пользователя", async () => {
    const user = userEvent.setup();
    const onCreateOrg = vi.fn();
    const onCreateUser = vi.fn();

    server.use(
      http.post("/api/admin/organizations", async ({ request }) => {
        onCreateOrg(await request.json());
        return HttpResponse.json({ id: 7, name: "ООО Тест", inn: null, kind: "contractor" }, { status: 201 });
      }),
      http.post("/api/admin/organizations/:id/users", async ({ request, params }) => {
        onCreateUser({ orgId: params.id, body: await request.json() });
        return HttpResponse.json(
          { id: 2, email: "a@b.ru", org_id: Number(params.id), org_role: "superadmin", is_active: true },
          { status: 201 }
        );
      })
    );

    renderWithProviders(<AdminOrgCreate />, { initialUser: SUPERUSER });

    await user.type(screen.getByLabelText("Название"), "ООО Тест");
    // выбрать роль «Подрядчик»
    await user.click(screen.getByRole("button", { name: /Подрядчик/ }));
    await user.type(screen.getByLabelText("Email"), "a@b.ru");

    await user.click(screen.getByRole("button", { name: "Создать организацию" }));

    await waitFor(() => {
      expect(onCreateOrg).toHaveBeenCalledTimes(1);
      expect(onCreateUser).toHaveBeenCalledTimes(1);
    });

    // организация создана с выбранным kind
    expect(onCreateOrg.mock.calls[0][0]).toMatchObject({ name: "ООО Тест", kind: "contractor" });
    // пользователь создан в новой организации с ролью superadmin
    expect(onCreateUser.mock.calls[0][0].orgId).toBe("7");
    expect(onCreateUser.mock.calls[0][0].body).toMatchObject({ email: "a@b.ru", org_role: "superadmin" });
  });
});
