import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { lumpFixture, lumpLineFixture, ourPriceLineFixture } from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import { qk } from "@/services/queryKeys";

import { LumpBlock } from "./LumpBlock";

const PATH = "/api/v1/position-items/:id/lump-sum";
const LINE_ID = 4242;
const AUTHOR = { id: 7, email: "boss@example.test" };
const SET_AT = "2026-11-22T09:30:00+00:00";

interface Asked {
  method: string;
  id: string;
  body: unknown;
}

/** Обработчики поправки: пишут вид запроса; `gate` держит ответ, пока тест его не отпустит. */
function serve(gate?: Promise<void>) {
  const asked: Asked[] = [];
  const handle = async ({ request, params }: { request: Request; params: Record<string, unknown> }) => {
    const text = await request.text();
    asked.push({ method: request.method, id: String(params.id), body: text === "" ? null : JSON.parse(text) });
    if (gate !== undefined) await gate;
    return request.method === "PUT" ? HttpResponse.json({}) : new HttpResponse(null, { status: 204 });
  };
  server.use(http.put(PATH, handle), http.delete(PATH, handle));
  return asked;
}

const rule = () => screen.getByTestId("lump-choice-rule");
const asLump = () => screen.getByTestId("lump-choice-lump");
const asRate = () => screen.getByTestId("lump-choice-rate");

function lineWith(over: Parameters<typeof lumpFixture>[0] = {}) {
  return lumpLineFixture({ position_item_id: LINE_ID }, over);
}

describe("LumpBlock: поправка «комплект или ставка»", () => {
  it("admin: три положения; у «Как решило правило» в скобках ответ правила", () => {
    renderWithProviders(<LumpBlock line={lineWith({ rule: true })} canEdit vat="net" />);
    expect(rule()).toHaveTextContent("Как решило правило (комплект)");
    expect(asLump()).toHaveTextContent("Это комплект");
    expect(asRate()).toHaveTextContent("Это ставка");
    expect(rule()).toHaveAttribute("aria-pressed", "true");
    expect(asLump()).toHaveAttribute("aria-pressed", "false");
  });

  it("правило говорит «ставка» — в скобках «ставка»", () => {
    renderWithProviders(
      <LumpBlock line={lineWith({ rule: false, is_lump: false, override: null })} canEdit vat="net" />
    );
    expect(rule()).toHaveTextContent("Как решило правило (ставка)");
  });

  it("действующая поправка — нажато её положение, а не «как решило правило»", () => {
    renderWithProviders(
      <LumpBlock
        line={lineWith({ rule: true, is_lump: false, override: { verdict: "rate", set_by: AUTHOR, set_at: SET_AT } })}
        canEdit
        vat="net"
      />
    );
    expect(asRate()).toHaveAttribute("aria-pressed", "true");
    expect(rule()).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("lump-override-note")).toHaveTextContent("поправлено вручную: boss@example.test, 22.11.2026");
  });

  it("«Это ставка» уходит сразу: PUT с verdict = rate по id строки", async () => {
    const user = userEvent.setup();
    const asked = serve();
    renderWithProviders(<LumpBlock line={lineWith()} canEdit vat="net" />);
    await user.click(asRate());
    await waitFor(() => expect(asked).toEqual([{ method: "PUT", id: String(LINE_ID), body: { verdict: "rate" } }]));
  });

  it("«Это комплект» — PUT с verdict = lump", async () => {
    const user = userEvent.setup();
    const asked = serve();
    renderWithProviders(
      <LumpBlock line={lineWith({ rule: false, is_lump: false })} canEdit vat="net" />
    );
    await user.click(asLump());
    await waitFor(() => expect(asked).toEqual([{ method: "PUT", id: String(LINE_ID), body: { verdict: "lump" } }]));
  });

  it("«Как решило правило» снимает поправку: DELETE без тела", async () => {
    const user = userEvent.setup();
    const asked = serve();
    renderWithProviders(
      <LumpBlock
        line={lineWith({ override: { verdict: "rate", set_by: AUTHOR, set_at: SET_AT }, is_lump: false })}
        canEdit
        vat="net"
      />
    );
    await user.click(rule());
    await waitFor(() => expect(asked).toEqual([{ method: "DELETE", id: String(LINE_ID), body: null }]));
  });

  it("повторный щелчок по нажатому положению запроса не шлёт", async () => {
    const user = userEvent.setup();
    const asked = serve();
    renderWithProviders(<LumpBlock line={lineWith()} canEdit vat="net" />);
    await user.click(rule());
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(asked).toEqual([]);
  });

  it("группа занята на время запроса и свободна после ответа", async () => {
    const user = userEvent.setup();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const asked = serve(gate);
    renderWithProviders(<LumpBlock line={lineWith()} canEdit vat="net" />);
    await user.click(asLump());
    await waitFor(() => expect(asked).toHaveLength(1));
    await waitFor(() => expect(asLump()).toBeDisabled());
    expect(rule()).toBeDisabled();
    expect(asRate()).toBeDisabled();
    release();
    await waitFor(() => expect(asRate()).not.toBeDisabled());
  });

  it("после ответа сброшены все экраны «Против нашей цены» и предпросмотры файла", async () => {
    const user = userEvent.setup();
    serve();
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    renderWithProviders(<LumpBlock line={lineWith()} canEdit vat="net" />, { queryClient });
    await user.click(asRate());
    await waitFor(() => {
      const keys = spy.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
      expect(keys).toContain(JSON.stringify(qk.tenders.ourPriceAll));
      expect(keys).toContain(JSON.stringify(qk.tenders.kpFilePreviewsAll));
    });
  });

  it("member: переключателя нет; без поправки блока нет вовсе", () => {
    renderWithProviders(<LumpBlock line={lineWith()} canEdit={false} vat="net" />);
    expect(screen.queryByTestId("lump-block")).not.toBeInTheDocument();
    expect(screen.queryByTestId("lump-choice-rule")).not.toBeInTheDocument();
  });

  it("member при поправке: только подпись «поправлено вручную: {email}, {дата}», без кнопок", () => {
    renderWithProviders(
      <LumpBlock
        line={lineWith({ override: { verdict: "lump", set_by: AUTHOR, set_at: SET_AT } })}
        canEdit={false}
        vat="net"
      />
    );
    expect(screen.getByTestId("lump-override-note")).toHaveTextContent("поправлено вручную: boss@example.test, 22.11.2026");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("у строки без lump блока нет — ни у admin, ни у member", () => {
    const plain = ourPriceLineFixture({ lump: null });
    const { unmount } = renderWithProviders(<LumpBlock line={plain} canEdit vat="net" />);
    expect(screen.queryByTestId("lump-block")).not.toBeInTheDocument();
    unmount();
    renderWithProviders(<LumpBlock line={plain} canEdit={false} vat="net" />);
    expect(screen.queryByTestId("lump-block")).not.toBeInTheDocument();
  });
});
