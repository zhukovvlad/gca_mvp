import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Routes, Route, Outlet } from "react-router-dom";

import { renderWithProviders } from "@/test/utils";
import { RequireAdmin } from "@/App";
import type { User } from "@/types/auth";

const ADMIN: User = {
  id: 99,
  email: "root@example.com",
  role: "admin",
};

const MEMBER: User = {
  id: 1,
  email: "member@example.com",
  role: "member",
};

/** Маленькое дерево маршрутов: /admin/users под guard, / как цель редиректа. */
function TestRoutes() {
  return (
    <Routes>
      <Route path="/" element={<div>Главная страница</div>} />
      <Route element={<RequireAdmin />}>
        <Route element={<Outlet />}>
          <Route path="/admin/users" element={<div>Админ страница</div>} />
        </Route>
      </Route>
    </Routes>
  );
}

describe("RequireAdmin", () => {
  it("показывает контент admin", async () => {
    renderWithProviders(<TestRoutes />, { initialRoute: "/admin/users", initialUser: ADMIN });
    await waitFor(() => {
      expect(screen.getByText("Админ страница")).toBeInTheDocument();
    });
  });

  it("редиректит member на главную", async () => {
    renderWithProviders(<TestRoutes />, { initialRoute: "/admin/users", initialUser: MEMBER });
    await waitFor(() => {
      expect(screen.getByText("Главная страница")).toBeInTheDocument();
    });
    expect(screen.queryByText("Админ страница")).not.toBeInTheDocument();
  });
});
