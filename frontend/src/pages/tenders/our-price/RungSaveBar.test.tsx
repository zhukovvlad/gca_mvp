import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import {
  KP_FILE_AUTHOR,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineFixture,
} from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { ManualRung, OurPriceLine, ResetReason, RungLevel, RungScope } from "@/types/domain";

import { RungSaveBar } from "./RungSaveBar";

const MEMBER = { id: 2, email: "member@example.test", role: "member" } as const;
const RUNG_URL = `/our-price/offers/${OUR_PRICE_OFFER_ID}/lines/${OUR_PRICE_LINE_ID}/rung`;

function manual(over: Partial<ManualRung> = {}): ManualRung {
  return {
    level: "v",
    scope: "all",
    active: true,
    reset_reason: null,
    target: "line",
    set_by: KP_FILE_AUTHOR,
    set_at: "2026-03-14T09:30:00+00:00",
    ...over,
  };
}

function renderBar(
  opts: {
    line?: Partial<OurPriceLine>;
    previewRung?: { level: RungLevel; scope: RungScope } | null;
    canEdit?: boolean;
    onSaved?: () => void;
  } = {}
) {
  const canEdit = opts.canEdit ?? true;
  return renderWithProviders(
    <RungSaveBar
      tenderId={OUR_PRICE_TENDER_ID}
      roundId={OUR_PRICE_ROUND_ID}
      offerId={OUR_PRICE_OFFER_ID}
      line={ourPriceLineFixture(opts.line)}
      previewRung={opts.previewRung === undefined ? null : opts.previewRung}
      canEdit={canEdit}
      onSaved={opts.onSaved ?? (() => undefined)}
    />,
    canEdit ? {} : { initialUser: MEMBER }
  );
}

const decisions = () => handlerState.kpFileRequests.filter((r) => r.kind === "decision");
const saveButton = () => screen.queryByRole("button", { name: "Сохранить эту ступень" });
const autoButton = () => screen.queryByRole("button", { name: "Вернуть авто" });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("сохранение ступени из предпросмотра", () => {
  it("ступень предпросмотра отличается от действующей и выбираема: PUT с уровнем и охватом, затем onSaved", async () => {
    const onSaved = vi.fn();
    renderBar({ previewRung: { level: "v", scope: "all" }, onSaved });
    expect(onSaved).not.toHaveBeenCalled();
    await userEvent.click(saveButton()!);
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(decisions()).toHaveLength(1);
    expect(decisions()[0].method).toBe("PUT");
    expect(decisions()[0].url).toContain(RUNG_URL);
    expect(decisions()[0].body).toEqual({ level: "v", scope: "all" });
  });

  it("ступень предпросмотра равна действующей — кнопки нет", () => {
    renderBar({ previewRung: { level: "v", scope: "cls" } });
    expect(saveButton()).not.toBeInTheDocument();
  });

  it("выбора ступени нет — кнопки нет", () => {
    renderBar({ previewRung: null });
    expect(saveButton()).not.toBeInTheDocument();
  });

  it("ступень не выбираема (selectable = false) — кнопки нет", () => {
    renderBar({ previewRung: { level: "f", scope: "all" } });
    expect(saveButton()).not.toBeInTheDocument();
  });

  it("у member кнопки нет даже при предпросмотре другой ступени", () => {
    renderBar({ canEdit: false, previewRung: { level: "v", scope: "all" } });
    expect(saveButton()).not.toBeInTheDocument();
  });

  it("отказ сервера — тост, onSaved не зовётся", async () => {
    handlerState.kpFileDecisionOutcome = "refused";
    const error = vi.spyOn(toast, "error").mockImplementation(() => "");
    const onSaved = vi.fn();
    renderBar({ previewRung: { level: "v", scope: "all" }, onSaved });
    await userEvent.click(saveButton()!);
    await waitFor(() => expect(error).toHaveBeenCalledTimes(1));
    expect(onSaved).not.toHaveBeenCalled();
  });
});

describe("действующая ручная ступень", () => {
  it("«Вернуть авто» — DELETE, затем onSaved; метка «ручная»", async () => {
    const onSaved = vi.fn();
    renderBar({ line: { manual_rung: manual(), rung: { level: "v", scope: "all", auto: false, threshold_met: true } }, onSaved });
    expect(screen.getByTestId("rung-manual-tag")).toHaveTextContent(/^ручная$/);
    await userEvent.click(autoButton()!);
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(decisions()).toHaveLength(1);
    expect(decisions()[0].method).toBe("DELETE");
    expect(decisions()[0].url).toContain(RUNG_URL);
  });

  it("ступень семьи у системы: «ручная · для всей семьи в этом КП»", () => {
    renderBar({ line: { kind: "system", manual_rung: manual({ target: "family" }) } });
    expect(screen.getByTestId("rung-manual-tag")).toHaveTextContent("ручная · для всей семьи в этом КП");
  });

  it("у member метка видна, кнопки «Вернуть авто» нет", () => {
    renderBar({ canEdit: false, line: { manual_rung: manual() } });
    expect(screen.getByTestId("rung-manual-tag")).toBeInTheDocument();
    expect(autoButton()).not.toBeInTheDocument();
  });

  it("ручной ступени нет и предпросмотра нет — панель пуста", () => {
    renderBar();
    expect(screen.queryByTestId("rung-manual-tag")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rung-inactive")).not.toBeInTheDocument();
    expect(autoButton()).not.toBeInTheDocument();
  });
});

describe("недействующая ручная ступень", () => {
  const cases: Array<[ResetReason, string]> = [
    ["kind_changed", "строка стала системой, а ступень выбрана для работы"],
    ["key_changed", "у строки сменился вариант или семья"],
    ["rung_unavailable", "такой ступени у строки больше нет"],
    ["rung_empty", "на этой ступени больше нет договоров"],
  ];

  it.each(cases)("причина %s: плашка с подписью ступени и текстом причины", (code, reason) => {
    renderBar({ line: { manual_rung: manual({ active: false, reset_reason: code }) } });
    expect(screen.getByTestId("rung-inactive")).toHaveTextContent(
      `Ручная ступень вариант · все классы не действует: ${reason} — считается автоматическая`
    );
    expect(screen.queryByTestId("rung-manual-tag")).not.toBeInTheDocument();
  });

  it("у admin в плашке есть «Вернуть авто», у member — нет", () => {
    const line = { manual_rung: manual({ active: false, reset_reason: "rung_empty" as const }) };
    const admin = renderBar({ line });
    expect(autoButton()).toBeInTheDocument();
    admin.unmount();
    renderBar({ line, canEdit: false });
    expect(screen.getByTestId("rung-inactive")).toBeInTheDocument();
    expect(autoButton()).not.toBeInTheDocument();
  });
});
