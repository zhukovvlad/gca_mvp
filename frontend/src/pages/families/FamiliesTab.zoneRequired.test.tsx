/**
 * Флажок семьи «зона обязательна» на странице «Семьи» и его след в окне слияния
 * (спека `2026-11-20-zone-as-preference-design.md` §4, §5 п. 7–9).
 */
import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { mergeZoneRequiredWarning, zoneRequiredAuthor } from "@/pages/families/labels";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { WorkFamily } from "@/types/domain";

const ACTIVE_ID = 43; // «Кровельные работы», активна
const ARCHIVED_ID = 44; // «Демонтажные работы (снята)»

const ON_TEXT =
  "Аналоги строкам этой семьи берутся только из договоров с той же зоной раздела, как раньше: у этой семьи зона подменяет тип изделия (противопожарная дверь в подвале, квартирная — на этаже). Без флажка аналоги ищутся сначала в своей зоне, нет — в других. Флажок снимают, когда схема семьи различит такие изделия параметрами.";
const OFF_TEXT =
  "Аналоги ищутся сначала в договорах с той же зоной раздела; если там нет ни одного — в договорах других зон, с пометкой «другая зона».";

function family(id: number): WorkFamily {
  return handlerState.workFamilies.find((f) => f.id === id)!;
}

function invalidatedKeys(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
}

async function openFamily(id: number, status: "active" | "archived" = "active", qc?: QueryClient) {
  const user = userEvent.setup();
  renderWithProviders(<FamiliesTab />, qc ? { queryClient: qc } : {});
  await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
  await user.click(screen.getByRole("combobox", { name: "Статус" }));
  await user.click(await screen.findByRole("option", { name: status === "active" ? "активна" : "в архиве" }));
  await user.click(await screen.findByText(family(id).title, {}, { timeout: 8000 }));
  await screen.findByLabelText("Определение");
  return user;
}

describe("FamiliesTab: блок «Зона при подборе»", () => {
  it("утв. 1: блок стоит между «Схемой и вариантами» и «Связями»; Switch и пояснение следуют значению", async () => {
    family(ACTIVE_ID).zone_required = true;
    await openFamily(ACTIVE_ID);

    const heading = await screen.findByRole("heading", { name: "Зона при подборе" });
    const schema = screen.getByRole("heading", { name: "Схема и варианты" });
    const links = screen.getByRole("heading", { name: "Связи" });
    expect(schema.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heading.compareDocumentPosition(links) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const block = heading.closest("section")!;
    expect(within(block).getByRole("switch", { name: "Зона обязательна" })).toBeChecked();
    expect(block).toHaveTextContent(ON_TEXT);
    expect(block).not.toHaveTextContent(OFF_TEXT);
  });

  it("утв. 1: без флажка Switch выключен и пояснение — другое", async () => {
    await openFamily(ACTIVE_ID);
    const block = (await screen.findByRole("heading", { name: "Зона при подборе" })).closest("section")!;
    expect(within(block).getByRole("switch", { name: "Зона обязательна" })).not.toBeChecked();
    expect(block).toHaveTextContent(OFF_TEXT);
    expect(block).not.toHaveTextContent(ON_TEXT);
  });

  it("утв. 2: переключение уходит сразу с новым значением, Switch занят до ответа, затем сброшены оба ключа", async () => {
    server.use(
      http.put("/api/v1/semantic/families/:id/zone-required", async ({ params, request }) => {
        const body = (await request.json()) as { zone_required: boolean };
        handlerState.zoneRequiredRequests.push({ id: Number(params.id), body });
        await delay(150);
        const f = family(Number(params.id));
        f.zone_required = body.zone_required;
        return HttpResponse.json(f);
      })
    );
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const user = await openFamily(ACTIVE_ID, "active", qc);
    const block = (await screen.findByRole("heading", { name: "Зона при подборе" })).closest("section")!;

    await user.click(within(block).getByRole("switch", { name: "Зона обязательна" }));

    await waitFor(() =>
      expect(handlerState.zoneRequiredRequests).toEqual([{ id: ACTIVE_ID, body: { zone_required: true } }])
    );
    await waitFor(() => expect(within(block).getByRole("switch", { name: "Зона обязательна" })).toHaveAttribute("aria-disabled", "true"));
    await waitFor(() => expect(within(block).getByRole("switch", { name: "Зона обязательна" })).not.toHaveAttribute("aria-disabled", "true"));
    const keys = invalidatedKeys(spy);
    expect(keys).toContain(JSON.stringify(["tenders", "our-price"]));
    expect(keys.some((k) => k.includes("famil"))).toBe(true);
    expect(within(block).getByRole("switch", { name: "Зона обязательна" })).toBeChecked();
  });

  it("утв. 3: подпись «Поставил {email}, {дата}» / «Снял …» / «Поставлен при обновлении, {дата}» / без записи — нет", () => {
    const base = family(ACTIVE_ID);
    const at = "2026-11-20T10:00:00Z";
    const date = new Date(at).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
    const who = { id: 1, email: "boss@example.com" };
    const f = (zone_required: boolean, changedAt: string | null, by: typeof who | null): WorkFamily => ({
      ...base,
      zone_required,
      zone_required_changed_at: changedAt,
      zone_required_changed_by: by,
    });
    expect(zoneRequiredAuthor(f(true, at, who))).toBe(`Поставил boss@example.com, ${date}`);
    expect(zoneRequiredAuthor(f(false, at, who))).toBe(`Снял boss@example.com, ${date}`);
    expect(zoneRequiredAuthor(f(true, at, null))).toBe(`Поставлен при обновлении, ${date}`);
    expect(zoneRequiredAuthor(f(false, null, null))).toBeNull();
  });

  it("утв. 3: подпись видна в блоке", async () => {
    const f = family(ACTIVE_ID);
    f.zone_required = true;
    f.zone_required_changed_at = "2026-11-20T10:00:00Z";
    f.zone_required_changed_by = { id: 1, email: "boss@example.com" };
    await openFamily(ACTIVE_ID);
    const block = (await screen.findByRole("heading", { name: "Зона при подборе" })).closest("section")!;
    expect(block).toHaveTextContent(/Поставил boss@example\.com, \d{2}\.\d{2}\.\d{4}/);
  });

  it("утв. 3: без записи автора подписи нет", async () => {
    await openFamily(ACTIVE_ID);
    const block = (await screen.findByRole("heading", { name: "Зона при подборе" })).closest("section")!;
    expect(block).not.toHaveTextContent(/Поставил|Снял|Поставлен при обновлении/);
  });

  it("утв. 4: у архивной семьи Switch недоступен", async () => {
    await openFamily(ARCHIVED_ID, "archived");
    const block = (await screen.findByRole("heading", { name: "Зона при подборе" })).closest("section")!;
    expect(within(block).getByRole("switch", { name: "Зона обязательна" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("FamiliesTab: окно слияния и флажок зоны", () => {
  function pair(sourceFlag: boolean, targetFlag: boolean): [WorkFamily, WorkFamily] {
    const base = family(ACTIVE_ID);
    return [
      { ...base, title: "Источник", zone_required: sourceFlag },
      { ...base, id: 99, title: "Цель", zone_required: targetFlag },
    ];
  }

  it("утв. 5: флажок у источника — строка спеки дословно", () => {
    const [s, t] = pair(true, false);
    expect(mergeZoneRequiredWarning(s, t)).toBe(
      "У семьи «Источник» зона обязательна — у «Цель» после слияния она тоже будет обязательной."
    );
  });

  it("утв. 5: флажок у цели — строка спеки дословно", () => {
    const [s, t] = pair(false, true);
    expect(mergeZoneRequiredWarning(s, t)).toBe(
      "У семьи «Цель» зона обязательна — строки «Источник» после слияния тоже будут подбираться только в своей зоне."
    );
  });

  it("утв. 5: одинаковые флажки — строки нет", () => {
    expect(mergeZoneRequiredWarning(...pair(true, true))).toBeNull();
    expect(mergeZoneRequiredWarning(...pair(false, false))).toBeNull();
  });

  async function openMerge(sourceFlag: boolean, targetFlag: boolean, qc?: QueryClient) {
    // Цель — активная «Кровельные работы» (43); источник — вторая активная.
    const target = family(ACTIVE_ID);
    const source: WorkFamily = { ...target, id: 45, title: "Гидроизоляция кровли" };
    handlerState.workFamilies.push(source);
    source.zone_required = sourceFlag;
    target.zone_required = targetFlag;
    const user = await openFamily(45, "active", qc);
    await user.click(screen.getByRole("button", { name: "Слить" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Кровельные работы" }));
    return { user, dialog };
  }

  it("утв. 5: в окне при флажке у источника строка появляется после выбора цели", async () => {
    const { dialog } = await openMerge(true, false);
    expect(
      await within(dialog).findByText(
        "У семьи «Гидроизоляция кровли» зона обязательна — у «Кровельные работы» после слияния она тоже будет обязательной."
      )
    ).toBeInTheDocument();
  });

  it("утв. 5: в окне при флажке у цели — строка про подбор только в своей зоне", async () => {
    const { dialog } = await openMerge(false, true);
    expect(
      await within(dialog).findByText(
        "У семьи «Кровельные работы» зона обязательна — строки «Гидроизоляция кровли» после слияния тоже будут подбираться только в своей зоне."
      )
    ).toBeInTheDocument();
  });

  it("утв. 5: одинаковые флажки — в окне строки нет", async () => {
    const { dialog } = await openMerge(true, true);
    await within(dialog).findByRole("combobox");
    expect(within(dialog).queryByText(/зона обязательна/)).not.toBeInTheDocument();
  });

  it("утв. 5: слияние сбрасывает и экраны «Против нашей цены»", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { user, dialog } = await openMerge(false, false, qc);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Слить" })).toBeEnabled());
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));
    await waitFor(() => expect(handlerState.lastMergeFamiliesRequest).not.toBeNull());
    await waitFor(() => expect(invalidatedKeys(spy)).toContain(JSON.stringify(["tenders", "our-price"])));
  });
});
