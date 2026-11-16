import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { constantLinkFixture, familyLinkFixture } from "@/test/fixtures";
import { handlerState, schemaParameter } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { FamilyLinkView, WorkFamily } from "@/types/domain";

import { FamilyLinksBlock } from "./FamilyLinksBlock";

const MEMBER = { id: 2, email: "member@example.test", role: "member" } as const;

/** Карточка семьи 71 (м3): активная, схема с точным параметром «Толщина, мм». */
function thisFamily(over: Partial<WorkFamily> = {}): WorkFamily {
  const base = handlerState.workFamilies.find((f) => f.id === 43)!;
  return { ...base, id: 71, title: "Бетонная подготовка в кубах", unit_symbol: "м3", status: "active", ...over };
}

function serve(links: FamilyLinkView[], family: WorkFamily = thisFamily()) {
  handlerState.familyLinks[family.id] = links;
  handlerState.familySchemas[family.id] = {
    family_id: family.id,
    status: "frozen",
    version: 1,
    ready_to_build: true,
    build_wait: null,
    building: false,
    values_jobs_live: 0,
    revision: 1,
    parameters: [schemaParameter(801, 1, "Толщина, мм", [])],
  };
  return renderWithProviders(<FamilyLinksBlock family={family} />);
}

const item = (id: number) => screen.findByTestId(`family-link-${id}`);

describe("раздел «Связи» карточки семьи", () => {
  it("печатает связь: другая семья, вид и параметр этой стороны", async () => {
    serve([familyLinkFixture()]);
    const li = await item(41);
    expect(screen.getByRole("heading", { name: "Связи" })).toBeInTheDocument();
    expect(li).toHaveTextContent("Бетонная подготовка в кубах");
    expect(li).toHaveTextContent("м² ↔ м³ через толщину");
    expect(li).toHaveTextContent("эта семья: «Толщина, мм»");
  });

  it("постоянная связь: равенство — `shown` сервера от этой семьи, с «≈» и основанием", async () => {
    serve([constantLinkFixture()]);
    const shown = await screen.findByTestId("family-link-42-shown");
    expect(shown).toHaveTextContent("1 м3 = ≈1,4999 т");
    expect(shown).toHaveTextContent("насыпная плотность 1,5 т/м3 — по паспорту поставщика");
  });

  it("постоянная связь, у которой левая часть — другая семья: единицы в порядке равенства", async () => {
    serve([constantLinkFixture({ shown: { from_family_id: 72, factor: "0.6667" } })]);
    expect(await screen.findByTestId("family-link-42-shown")).toHaveTextContent("1 т = 0,6667 м3");
  });

  it("не действует: единица семьи изменилась — подтвердите связь заново", async () => {
    serve([constantLinkFixture({ state: "inactive", inactive_reason: "units_changed" })]);
    expect(await screen.findByTestId("family-link-42-inactive")).toHaveTextContent(
      "не действует: единица семьи изменилась — подтвердите связь заново"
    );
  });

  it("действующая связь с устаревшей стороной: «параметр устарел — укажите заново» у этой стороны", async () => {
    serve([familyLinkFixture({ stale_sides: ["other"], other_param: { id: 55, name: "Толщина" } })]);
    const stale = await screen.findByTestId("family-link-41-stale");
    expect(stale).toHaveTextContent("другая семья: параметр устарел — укажите заново");
    expect(screen.queryByTestId("family-link-41-inactive")).not.toBeInTheDocument();
  });

  it("без живой стороны причина названа один раз, без дубля по сторонам", async () => {
    serve([
      familyLinkFixture({ state: "inactive", inactive_reason: "no_live_side", stale_sides: ["this"] }),
    ]);
    expect(await screen.findByTestId("family-link-41-inactive")).toHaveTextContent("параметр устарел — укажите заново");
    expect(screen.queryByTestId("family-link-41-stale")).not.toBeInTheDocument();
  });

  it("подпись автора: «Завёл», пока связь не правилась (updated_at = created_at), иначе «Правил»", async () => {
    const created = "2026-11-14T10:00:00+00:00";
    const editor = { id: 4, email: "editor@example.test" };
    serve([
      familyLinkFixture({ id: 41, created_at: created, updated_by: familyLinkFixture().created_by, updated_at: created }),
      familyLinkFixture({ id: 43, created_at: created, updated_by: editor, updated_at: "2026-11-15T12:00:00+00:00" }),
    ]);
    const untouched = await screen.findByTestId("family-link-41-author");
    expect(untouched).toHaveTextContent("Завёл admin@example.test");
    expect(untouched).not.toHaveTextContent("Правил");
    const edited = await screen.findByTestId("family-link-43-author");
    expect(edited).toHaveTextContent("Правил editor@example.test");
    expect(edited).not.toHaveTextContent("Завёл");
  });

  it("у семьи без связей — «Связей нет.»", async () => {
    serve([]);
    expect(await screen.findByText("Связей нет.")).toBeInTheDocument();
  });
});

describe("раздел «Связи»: права", () => {
  it("admin: «Добавить связь…», «Править», «Удалить»", async () => {
    serve([familyLinkFixture()]);
    const li = await item(41);
    expect(screen.getByRole("button", { name: "Добавить связь…" })).toBeEnabled();
    expect(within(li).getByRole("button", { name: "Править" })).toBeInTheDocument();
    expect(within(li).getByRole("button", { name: "Удалить" })).toBeInTheDocument();
  });

  it("member видит связи, но кнопок записи нет", async () => {
    handlerState.familyLinks[71] = [familyLinkFixture()];
    renderWithProviders(<FamilyLinksBlock family={thisFamily()} />, { initialUser: MEMBER });
    const li = await item(41);
    expect(li).toHaveTextContent("м² ↔ м³ через толщину");
    expect(screen.queryByRole("button", { name: "Добавить связь…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Править" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить" })).not.toBeInTheDocument();
  });

  it("у неактивной семьи создание недоступно", async () => {
    serve([], thisFamily({ status: "archived" }));
    await screen.findByText("Связей нет.");
    expect(screen.getByRole("button", { name: "Добавить связь…" })).toBeDisabled();
  });

  it("удаление просит подтверждения и шлёт DELETE", async () => {
    serve([familyLinkFixture()]);
    const li = await item(41);
    await userEvent.click(within(li).getByRole("button", { name: "Удалить" }));
    expect(handlerState.familyLinkRequests).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Удалить связь" }));
    await waitFor(() => expect(handlerState.familyLinkRequests).toEqual([{ method: "DELETE", id: 41, body: null }]));
  });

  it("отмена удаления ничего не шлёт", async () => {
    serve([familyLinkFixture()]);
    await userEvent.click(within(await item(41)).getByRole("button", { name: "Удалить" }));
    await userEvent.click(await screen.findByRole("button", { name: "Отмена" }));
    expect(handlerState.familyLinkRequests).toHaveLength(0);
  });
});
