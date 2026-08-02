import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Routes, Route, Outlet } from "react-router-dom";

import { renderWithProviders } from "@/test/utils";
import { RequireSuperuser } from "@/App";
import type { User } from "@/types/auth";

const SUPERUSER: User = {
  id: 99,
  email: "root@platform.ru",
  org_id: null,
  org_role: null,
  is_superuser: true,
  organization: null,
};

const ORG_ADMIN: User = {
  id: 1,
  email: "admin@org.ru",
  org_id: 1,
  org_role: "admin",
  is_superuser: false,
  organization: { id: 1, name: "Орг", inn: null, kind: "customer" },
};

/** Маленькое дерево маршрутов: /admin под guard, /dashboard как цель редиректа. */
function TestRoutes() {
  return (
    <Routes>
      <Route path="/dashboard" element={<div>Дашборд страница</div>} />
      <Route element={<RequireSuperuser />}>
        <Route element={<Outlet />}>
          <Route path="/admin" element={<div>Админ страница</div>} />
        </Route>
      </Route>
    </Routes>
  );
}

describe("RequireSuperuser", () => {
  it("показывает контент суперюзеру", async () => {
    renderWithProviders(<TestRoutes />, { initialRoute: "/admin", initialUser: SUPERUSER });
    await waitFor(() => {
      expect(screen.getByText("Админ страница")).toBeInTheDocument();
    });
  });

  it("редиректит не-суперюзера на /dashboard", async () => {
    renderWithProviders(<TestRoutes />, { initialRoute: "/admin", initialUser: ORG_ADMIN });
    await waitFor(() => {
      expect(screen.getByText("Дашборд страница")).toBeInTheDocument();
    });
    expect(screen.queryByText("Админ страница")).not.toBeInTheDocument();
  });
});
