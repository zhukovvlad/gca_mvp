import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { SchemaBlock } from "./SchemaBlock";

/**
 * Метка признака «можно отпустить» у главного параметра в блоке схемы (лестница аналогов §2.20):
 * «можно отпустить» / «обязателен» / «не размечен» и «предложение модели» для неразмеченного.
 * Фикстура: «Материал» (id 101) и «Толщина» (id 102) - оба главные.
 */

function renderBlock() {
  const family = handlerState.workFamilies.find((f) => f.id === SCHEMA_FAMILY_ID)!;
  return renderWithProviders(<SchemaBlock family={family} />);
}

function parameter(name: string) {
  const heading = screen.getByRole("heading", { name, level: 5 });
  return heading.parentElement as HTMLElement;
}

describe("SchemaBlock: признак «можно отпустить»", () => {
  it("размеченный главный параметр: «можно отпустить» и «обязателен»", async () => {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters[0].releasable = true;
    schema.parameters[1].releasable = false;
    renderBlock();

    await screen.findByRole("heading", { name: "Материал", level: 5 });
    expect(within(parameter("Материал")).getByText("можно отпустить")).toBeInTheDocument();
    expect(within(parameter("Толщина")).getByText("обязателен")).toBeInTheDocument();
  });

  it("не размечен: метка «не размечен», без предложения - без подписи модели", async () => {
    renderBlock();

    await screen.findByRole("heading", { name: "Материал", level: 5 });
    expect(within(parameter("Материал")).getByText("не размечен")).toBeInTheDocument();
    expect(screen.queryByText(/предложение модели/)).not.toBeInTheDocument();
  });

  it("не размечен, но есть предложение: «предложение модели» с ответом и уверенностью", async () => {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters[1].releasable_proposal = { releasable: true, confidence: "0.71", reason: "близко" };
    renderBlock();

    await screen.findByRole("heading", { name: "Толщина", level: 5 });
    const block = parameter("Толщина");
    expect(within(block).getByText("не размечен")).toBeInTheDocument();
    expect(within(block).getByText(/предложение модели: можно отпустить · 0,71/)).toBeInTheDocument();
  });

  it("у второстепенного параметра метки нет", async () => {
    const schema = handlerState.familySchemas[SCHEMA_FAMILY_ID];
    schema.parameters[1].role = "secondary";
    renderBlock();

    await screen.findByRole("heading", { name: "Толщина", level: 5 });
    expect(within(parameter("Толщина")).queryByText("не размечен")).not.toBeInTheDocument();
    expect(within(parameter("Материал")).getByText("не размечен")).toBeInTheDocument();
  });
});
