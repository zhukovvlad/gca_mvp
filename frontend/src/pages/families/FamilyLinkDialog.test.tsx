import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { constantLinkFixture, familyLinkFixture } from "@/test/fixtures";
import { handlerState, schemaParameter } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { FamilyLinkBody, FamilyLinkView, WorkFamily } from "@/types/domain";

import { FamilyLinksBlock } from "./FamilyLinksBlock";

/** Карточка семьи `id`: активная, единица как задана. */
function cardFamily(id: number, title: string, unit: string): WorkFamily {
  const base = handlerState.workFamilies.find((f) => f.id === 43)!;
  return { ...base, id, title, unit_symbol: unit, status: "active" };
}

/** Схема семьи: точный параметр «Толщина, мм» и параметр-диапазон, который в связь не годится. */
function giveSchema(family: WorkFamily, paramId: number) {
  handlerState.familySchemas[family.id] = {
    family_id: family.id,
    status: "frozen",
    version: 1,
    ready_to_build: true,
    build_wait: null,
    building: false,
    values_jobs_live: 0,
    revision: 1,
    parameters: [
      schemaParameter(paramId, 1, "Толщина, мм", []),
      schemaParameter(paramId + 1, 2, "Диапазон", [], { value_kind: "range" }),
    ],
  };
}

function open(family: WorkFamily, links: FamilyLinkView[]) {
  // Маршруты схемы отвечают только за известные семьи.
  handlerState.workFamilies.push(family);
  handlerState.familyLinks[family.id] = links;
  giveSchema(family, family.id * 10);
  renderWithProviders(<FamilyLinksBlock family={family} />);
}

const sent = (): FamilyLinkBody => {
  const request = handlerState.familyLinkRequests.at(-1);
  expect(request).toBeDefined();
  return request!.body as FamilyLinkBody;
};

async function edit(id: number) {
  const li = await screen.findByTestId(`family-link-${id}`);
  await userEvent.click(within(li).getByRole("button", { name: "Править" }));
  return screen.findByRole("dialog");
}

async function pick(label: string | RegExp, option: string | RegExp) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

async function openCreate() {
  await screen.findByText("Связей нет.");
  await userEvent.click(screen.getByRole("button", { name: "Добавить связь…" }));
  return screen.findByRole("dialog");
}

describe("форма связи: правка берёт хранимые значения", () => {
  it("с карточки левой семьи: сохранение без изменений шлёт хранимые factor и factor_from_family_id", async () => {
    const family = cardFamily(71, "Щебень в кубах", "м3");
    open(family, [constantLinkFixture({ other_family: { id: 72, title: "Щебень в тоннах", unit: "т" } })]);
    const dialog = await edit(42);
    expect(within(dialog).getByLabelText("Множитель")).toHaveValue("0.6667");
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(handlerState.familyLinkRequests).toHaveLength(1));
    expect(handlerState.familyLinkRequests[0]).toMatchObject({ method: "PUT", id: 42 });
    expect(sent()).toEqual({
      family_a_id: 71,
      family_b_id: 72,
      kind: "constant",
      param_a_id: null,
      param_b_id: null,
      factor_from_family_id: 72,
      factor: "0.6667",
      basis: "насыпная плотность 1,5 т/м3 — по паспорту поставщика",
    });
  });

  it("с карточки второй семьи: те же factor и factor_from_family_id, а не обращённое `shown`", async () => {
    const family = cardFamily(72, "Щебень в тоннах", "т");
    open(family, [
      constantLinkFixture({
        other_family: { id: 71, title: "Щебень в кубах", unit: "м3" },
        shown: { from_family_id: 72, factor: "0.6667" },
      }),
    ]);
    await edit(42);
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(handlerState.familyLinkRequests).toHaveLength(1));
    expect(sent()).toMatchObject({ family_a_id: 72, family_b_id: 71, factor_from_family_id: 72, factor: "0.6667" });
  });

  it("форма не подставляет `shown`: с «≈» в показе поле множителя всё равно хранимое", async () => {
    const family = cardFamily(71, "Щебень в кубах", "м3");
    open(family, [constantLinkFixture({ other_family: { id: 72, title: "Щебень в тоннах", unit: "т" } })]);
    const dialog = await edit(42);
    expect(within(dialog).getByLabelText("Множитель")).not.toHaveValue("≈1.4999");
    expect(within(dialog).getByLabelText("Множитель")).toHaveValue("0.6667");
  });

  it("вид с параметром: factor, basis и factor_from_family_id — null, а не пустые строки", async () => {
    const family = cardFamily(71, "Бетон в кубах", "м3");
    open(family, [familyLinkFixture({ this_param: { id: 710, name: "Толщина, мм" } })]);
    await edit(41);
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(handlerState.familyLinkRequests).toHaveLength(1));
    expect(sent()).toEqual({
      family_a_id: 71,
      family_b_id: 72,
      kind: "thickness",
      param_a_id: 710,
      param_b_id: null,
      factor_from_family_id: null,
      factor: null,
      basis: null,
    });
  });

  it("устаревший параметр не предзаполняется: «Сохранить» ждёт нового выбора", async () => {
    const family = cardFamily(71, "Бетон в кубах", "м3");
    open(family, [familyLinkFixture({ stale_sides: ["this"], this_param: { id: 55, name: "Толщина (старая)" } })]);
    const dialog = await edit(41);
    expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("в списке параметров только точные: диапазон не предлагается", async () => {
    const family = cardFamily(71, "Бетон в кубах", "м3");
    open(family, [familyLinkFixture({ this_param: { id: 710, name: "Толщина, мм" } })]);
    await edit(41);
    await userEvent.click(screen.getByRole("combobox", { name: "Параметр этой семьи" }));
    expect(await screen.findByRole("option", { name: "Толщина, мм" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Диапазон" })).not.toBeInTheDocument();
  });
});

describe("форма связи: создание", () => {
  it("вид с параметром: выбор другой семьи и параметра, тело с null у множителя", async () => {
    open(cardFamily(71, "Бетон в кубах", "м3"), []);
    await openCreate();
    await pick("Другая семья", /Кровельные работы/);
    await pick("Параметр этой семьи", "Толщина, мм");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(handlerState.familyLinkRequests).toHaveLength(1));
    expect(handlerState.familyLinkRequests[0]).toMatchObject({ method: "POST", id: null });
    expect(sent()).toEqual({
      family_a_id: 71,
      family_b_id: 43,
      kind: "thickness",
      param_a_id: 710,
      param_b_id: null,
      factor_from_family_id: null,
      factor: null,
      basis: null,
    });
  });

  it("постоянный множитель: запятая в числе становится точкой, направление — выбранная левая часть", async () => {
    open(cardFamily(71, "Щебень в кубах", "м3"), []);
    const dialog = await openCreate();
    await pick("Другая семья", /Кровельные работы/);
    await pick("Вид связи", "постоянный множитель");
    await pick("Левая часть равенства", /Кровельные работы/);
    await userEvent.type(within(dialog).getByLabelText("Множитель"), "0,667");
    await userEvent.type(within(dialog).getByLabelText("Основание"), "по паспорту");
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(handlerState.familyLinkRequests).toHaveLength(1));
    expect(sent()).toEqual({
      family_a_id: 71,
      family_b_id: 43,
      kind: "constant",
      param_a_id: null,
      param_b_id: null,
      factor_from_family_id: 43,
      factor: "0.667",
      basis: "по паспорту",
    });
  });

  it("постоянная связь без основания не сохраняется", async () => {
    open(cardFamily(71, "Щебень в кубах", "м3"), []);
    const dialog = await openCreate();
    await pick("Другая семья", /Кровельные работы/);
    await pick("Вид связи", "постоянный множитель");
    await userEvent.type(within(dialog).getByLabelText("Множитель"), "0.667");
    expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("вид с параметром без единого параметра не сохраняется", async () => {
    open(cardFamily(71, "Бетон в кубах", "м3"), []);
    const dialog = await openCreate();
    await pick("Другая семья", /Кровельные работы/);
    expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });
});

describe("форма связи: отказы", () => {
  async function submitWithRefusal(refusal: NonNullable<typeof handlerState.familyLinkRefusal>) {
    const family = cardFamily(71, "Бетон в кубах", "м3");
    open(family, [familyLinkFixture({ this_param: { id: 710, name: "Толщина, мм" } })]);
    handlerState.familyLinkRefusal = refusal;
    await edit(41);
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    return screen.findByTestId("link-refusal");
  }

  it("link_exists — «связь этой пары уже есть»", async () => {
    expect(await submitWithRefusal({ status: 409, code: "link_exists" })).toHaveTextContent(
      "связь этой пары уже есть"
    );
  });

  it.each([
    ["same_family", "Связь держит две разные семьи."],
    ["family_inactive", "Связь заводится между активными семьями."],
    ["units_mismatch", "Единицы семей не подходят к виду связи."],
    ["param_invalid", "Параметр не годится"],
    ["params_missing", "Укажите параметр хотя бы одной стороны."],
    ["constant_invalid", "Постоянная связь: множитель — число больше нуля"],
    ["pair_changed", "Пара семей связи не меняется"],
  ])("link_invalid с причиной %s — текст по причине, а не текст сервера", async (reason, text) => {
    const refusal = await submitWithRefusal({ status: 422, code: "link_invalid", reason });
    expect(refusal).toHaveTextContent(text);
    expect(refusal).not.toHaveTextContent("server text");
  });

  it("после отказа диалог остаётся открытым, после успеха — закрывается", async () => {
    await submitWithRefusal({ status: 409, code: "link_exists" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
