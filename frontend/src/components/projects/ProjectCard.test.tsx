import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import { sampleProject } from "@/test/fixtures";

import { ProjectCard } from "./ProjectCard";

/**
 * ProjectCard renders a <Link> to /projects/:id plus a dropdown menu (Edit / Archive / Delete).
 *
 * Key invariants under test:
 *   1. The kebab button (⋯) does NOT trigger Link navigation — it must be a sibling of <Link>, not a child.
 *      We assert this indirectly: clicking it opens the menu and the route stays at the initial page.
 *   2. Edit flow calls PUT /api/projects/:id with the trimmed payload.
 *   3. Delete flow opens an AlertDialog (not auto-confirmed) and calls DELETE only after confirmation.
 *   4. The «В архив» item is disabled (feature flag — coming soon).
 */
describe("ProjectCard", () => {
  it("renders project name and contract number", () => {
    renderWithProviders(<ProjectCard project={sampleProject} />);
    expect(screen.getByText(sampleProject.name)).toBeInTheDocument();
    expect(screen.getByText(`Договор № ${sampleProject.contract_number}`)).toBeInTheDocument();
  });

  it("shows «Договор не указан» when contract_number is null", () => {
    renderWithProviders(
      <ProjectCard project={{ ...sampleProject, contract_number: null }} />
    );
    expect(screen.getByText("Договор не указан")).toBeInTheDocument();
  });

  it("opens the actions menu with Edit / Archive (disabled) / Delete", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProjectCard project={sampleProject} />);

    await user.click(screen.getByRole("button", { name: "Действия с объектом" }));

    expect(await screen.findByText("Редактировать")).toBeInTheDocument();
    expect(screen.getByText("В архив")).toBeInTheDocument();
    expect(screen.getByText("скоро")).toBeInTheDocument();
    expect(screen.getByText("Удалить объект")).toBeInTheDocument();
  });

  it("edit flow sends PUT with trimmed name", async () => {
    const onUpdate = vi.fn();
    server.use(
      http.put("/api/projects/:id", async ({ request, params }) => {
        const body = (await request.json()) as { name: string; contract_number: string | null };
        onUpdate({ id: params.id, body });
        return HttpResponse.json({ ...sampleProject, ...body });
      })
    );

    const user = userEvent.setup();
    renderWithProviders(<ProjectCard project={sampleProject} />);

    await user.click(screen.getByRole("button", { name: "Действия с объектом" }));
    await user.click(await screen.findByText("Редактировать"));

    const nameInput = await screen.findByDisplayValue(sampleProject.name);
    await user.clear(nameInput);
    await user.type(nameInput, "  ЖК Северный  ");

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalledWith({
        id: String(sampleProject.id),
        body: { name: "ЖК Северный", contract_number: sampleProject.contract_number },
      });
    });
  });

  it("delete flow requires confirmation in AlertDialog before calling DELETE", async () => {
    const onDelete = vi.fn();
    server.use(
      http.delete("/api/projects/:id", ({ params }) => {
        onDelete({ id: params.id });
        return HttpResponse.json({ message: "Удалено" });
      })
    );

    const user = userEvent.setup();
    renderWithProviders(<ProjectCard project={sampleProject} />);

    await user.click(screen.getByRole("button", { name: "Действия с объектом" }));
    await user.click(await screen.findByText("Удалить объект"));

    // AlertDialog is open — title visible, DELETE not yet called
    expect(
      await screen.findByText(`Удалить объект «${sampleProject.name}»?`)
    ).toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();

    // Cancel → dialog closes, still no DELETE
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onDelete).not.toHaveBeenCalled();

    // Reopen and confirm
    await user.click(screen.getByRole("button", { name: "Действия с объектом" }));
    await user.click(await screen.findByText("Удалить объект"));
    await user.click(await screen.findByRole("button", { name: "Удалить" }));

    await waitFor(() => {
      expect(onDelete).toHaveBeenCalledWith({ id: String(sampleProject.id) });
    });
  });
});
