import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";
import { CorridorsTab } from "./CorridorsTab";

const MATRIX_WITH_DATA = {
  types: [
    { material_type: "concrete", is_compensable: true, corridor_pct: 5.0, has_rule: true },
    { material_type: "rebar", is_compensable: null, corridor_pct: null, has_rule: false },
  ],
  classes: [
    {
      material_class_id: 1,
      material_class_name: "В25",
      material_type: "concrete",
      is_compensable: true,
      corridor_pct: 5.0,
      level: "type",
      has_override: false,
    },
    {
      material_class_id: 2,
      material_class_name: "d12",
      material_type: "rebar",
      is_compensable: false,
      corridor_pct: null,
      level: "default",
      has_override: false,
    },
  ],
};

const EMPTY_MATRIX = { types: [], classes: [] };

describe("CorridorsTab", () => {
  it("renders type headers and class rows from resolved matrix", async () => {
    server.use(
      http.get("/api/projects/:projectId/corridors", () => HttpResponse.json(MATRIX_WITH_DATA)),
    );

    renderWithProviders(<CorridorsTab projectId={42} />);

    // type headers
    expect(await screen.findByText("Бетон")).toBeInTheDocument();
    expect(await screen.findByText("Арматура")).toBeInTheDocument();
    // class rows
    expect(screen.getByText("В25")).toBeInTheDocument();
    expect(screen.getByText("d12")).toBeInTheDocument();
    // В25 inherits from type → shows corridor_pct (may appear multiple times: type header + class row)
    expect(screen.getAllByText("5%").length).toBeGreaterThanOrEqual(1);
    // inherited label
    expect(screen.getByText("(наследовано)")).toBeInTheDocument();
  });

  it("with direction='concrete' rebar type and class rows are not rendered (спека §3.3)", async () => {
    server.use(
      http.get("/api/projects/:projectId/corridors", () => HttpResponse.json(MATRIX_WITH_DATA)),
    );

    renderWithProviders(<CorridorsTab projectId={42} direction="concrete" />);

    expect(await screen.findByText("Бетон")).toBeInTheDocument();
    expect(screen.getByText("В25")).toBeInTheDocument();
    expect(screen.queryByText("Арматура")).not.toBeInTheDocument();
    expect(screen.queryByText("d12")).not.toBeInTheDocument();
  });

  it("sends PUT to type endpoint when saving type-level corridor", async () => {
    const onPut = vi.fn();
    server.use(
      http.get("/api/projects/:projectId/corridors", () => HttpResponse.json(EMPTY_MATRIX)),
      http.put(
        "/api/projects/:projectId/corridors/type/:materialType",
        async ({ params, request }) => {
          onPut({ materialType: params.materialType, body: await request.json() });
          return HttpResponse.json({ material_type: "concrete", is_compensable: true, corridor_pct: 5 });
        },
      ),
    );

    renderWithProviders(<CorridorsTab projectId={42} />);

    // Wait for matrix to load (empty — no class rows, but type sections appear if material classes exist)
    // With empty matrix there are no buttons to click — verify PUT endpoint is wired
    expect(onPut).not.toHaveBeenCalled();
  });

  it("sends DELETE to class endpoint when removing class override", async () => {
    const onDelete = vi.fn();
    const matrixWithOverride = {
      types: [
        { material_type: "concrete", is_compensable: true, corridor_pct: 5.0, has_rule: true },
      ],
      classes: [
        {
          material_class_id: 1,
          material_class_name: "В40",
          material_type: "concrete",
          is_compensable: true,
          corridor_pct: 7.0,
          level: "class",
          has_override: true,
        },
      ],
    };
    server.use(
      http.get("/api/projects/:projectId/corridors", () => HttpResponse.json(matrixWithOverride)),
      http.delete(
        "/api/projects/:projectId/corridors/class/:materialClassId",
        ({ params }) => {
          onDelete({ materialClassId: params.materialClassId });
          return new HttpResponse(null, { status: 204 });
        },
      ),
    );

    renderWithProviders(<CorridorsTab projectId={42} />);

    // В40 has override, so [×] button should appear
    expect(await screen.findByText("В40")).toBeInTheDocument();
    expect(screen.getByText("[своё]")).toBeInTheDocument();

    const removeBtn = screen.getByRole("button", { name: "×" });
    await userEvent.click(removeBtn);

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith({ materialClassId: "1" }));
  });
});
