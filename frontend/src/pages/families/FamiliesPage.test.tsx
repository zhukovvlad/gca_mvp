import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { RequireAdmin } from "@/App";
import { TopNav } from "@/components/layout/TopNav";
import FamiliesPage from "@/pages/families/FamiliesPage";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { User } from "@/types/auth";

/**
 * Экран `/families` целиком (спека 2026-09-22-catalog-families-design.md
 * §2.10). Право проверяется ТЕМ ЖЕ входом, что и `RequireAdmin.test.tsx`
 * (план задачи 13, «Утверждения»: «тем же входом, которым он проверен у
 * /standards» — /standards не заводит собственного теста guard'а, только
 * общий `RequireAdmin.test.tsx`, поэтому здесь — тот же приём для `/families`).
 */

const ADMIN: User = { id: 1, email: "admin@example.com", role: "admin" };
const MEMBER: User = { id: 2, email: "member@example.com", role: "member" };

function FamiliesRoutes() {
  return (
    <Routes>
      <Route path="/" element={<div>Главная страница</div>} />
      <Route element={<RequireAdmin />}>
        <Route path="/families" element={<FamiliesPage />} />
      </Route>
    </Routes>
  );
}

describe("FamiliesPage", () => {
  it("admin видит экран «Семьи и контексты»", async () => {
    renderWithProviders(<FamiliesRoutes />, { initialRoute: "/families", initialUser: ADMIN });
    await waitFor(() => {
      expect(screen.getByText("Семьи и контексты")).toBeInTheDocument();
    });
  });

  it("member редиректится на главную", async () => {
    renderWithProviders(<FamiliesRoutes />, { initialRoute: "/families", initialUser: MEMBER });
    await waitFor(() => {
      expect(screen.getByText("Главная страница")).toBeInTheDocument();
    });
    expect(screen.queryByText("Семьи и контексты")).not.toBeInTheDocument();
  });

  /*
   * Два теста выше собирают СОБСТВЕННОЕ дерево маршрутов и потому не видят,
   * где `/families` стоит в настоящем `App.tsx`: маршрут, вынесенный из-под
   * `RequireAdmin`, оставил бы их зелёными. Здесь — настоящий `App` (свежий
   * модуль на тест: его `QueryClient` модульный и держал бы пользователя
   * между тестами), пользователь приходит от `/api/auth/me`.
   */
  describe("настоящее дерево маршрутов App", () => {
    afterEach(() => {
      window.history.pushState({}, "", "/");
    });

    async function renderApp(user: User) {
      server.use(http.get("/api/auth/me", () => HttpResponse.json(user)));
      window.history.pushState({}, "", "/families");
      vi.resetModules();
      const { default: App } = await import("@/App");
      render(<App />);
    }

    it("admin открывает /families", async () => {
      await renderApp(ADMIN);
      expect(await screen.findByText("Семьи и контексты")).toBeInTheDocument();
      expect(window.location.pathname).toBe("/families");
    });

    it("member на /families уходит на главную", async () => {
      await renderApp(MEMBER);
      await waitFor(() => expect(window.location.pathname).toBe("/"));
      expect(screen.queryByText("Семьи и контексты")).not.toBeInTheDocument();
    });
  });

  it("пункт меню «Семьи» виден admin и скрыт от member", () => {
    const { unmount } = renderWithProviders(<TopNav />, { initialUser: ADMIN });
    expect(screen.getByRole("link", { name: /Семьи/ })).toBeInTheDocument();
    unmount();
    renderWithProviders(<TopNav />, { initialUser: MEMBER });
    expect(screen.queryByRole("link", { name: /Семьи/ })).not.toBeInTheDocument();
  });

  it("три области экрана переключаются вкладками", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesPage />);

    expect(await screen.findByRole("tab", { name: "Семьи" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Контексты" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Операции" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Контексты" }));
    expect(await screen.findByLabelText("Поиск по написанию каталога")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Операции" }));
    expect(await screen.findByText("Контекст не выбран")).toBeInTheDocument();
  });

  it("выбор контекста в очереди открывает его карточку во вкладке «Операции»", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesPage />);

    await user.click(await screen.findByRole("tab", { name: "Контексты" }));
    await user.click(
      await screen.findByText("Штукатурка стен цементно-песчаным раствором")
    );

    // Клик по строке переключает на «Операции» и открывает карточку ЭТОГО контекста.
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Операции" })).toHaveAttribute("aria-selected", "true");
    });
    expect(
      await screen.findByText("Членств: 3")
    ).toBeInTheDocument();
  });
});
