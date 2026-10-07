import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import type { ContextVariantData } from "@/types/domain";

import { ContextVariant } from "./ContextVariant";

/**
 * Строка «Вариант» карточки контекста (спека `2026-10-02-catalog-variants-design.md`
 * §2.12): набор значений с источником каждого, пометка «к делению: разделы расходятся»
 * с переходом к членствам, пометка «вариант пересчитывается».
 */

function variantOf(overrides: Partial<ContextVariantData> = {}): ContextVariantData {
  return {
    variant_id: 7,
    values: [
      { parameter_id: 11, ordinal: 1, name: "Материал", value_id: 101, value: "металлочерепица", source: "name" },
      { parameter_id: 12, ordinal: 2, name: "Толщина", value_id: 104, value: "0,5 мм", source: "path" },
      { parameter_id: 13, ordinal: 3, name: "Цвет", value_id: 109, value: "RAL 3005", source: "manual" },
    ],
    split_hint: false,
    pending: null,
    values_job_status: null,
    ...overrides,
  };
}

function renderVariant(variant: ContextVariantData, extra: { onOpenMemberships?: () => void } = {}) {
  return renderWithProviders(
    <ContextVariant variant={variant} onOpenMemberships={extra.onOpenMemberships ?? (() => {})} />
  );
}

describe("ContextVariant: значения", () => {
  it("печатает значение каждого параметра и его источник словами", () => {
    renderVariant(variantOf());

    expect(screen.getByText("Вариант")).toBeInTheDocument();
    expect(screen.getByText("Материал").closest("li")).toHaveTextContent("металлочерепица");
    expect(screen.getByText("Материал").closest("li")).toHaveTextContent("по наименованию");
    expect(screen.getByText("Толщина").closest("li")).toHaveTextContent("0,5 мм");
    expect(screen.getByText("Толщина").closest("li")).toHaveTextContent("по разделам");
    expect(screen.getByText("Цвет").closest("li")).toHaveTextContent("RAL 3005");
    expect(screen.getByText("Цвет").closest("li")).toHaveTextContent("вручную");
  });

  it("значение, которого нет, читается «не уточнено», а расхождение путей — своим словом", () => {
    renderVariant(
      variantOf({
        values: [
          { parameter_id: 11, ordinal: 1, name: "Материал", value_id: null, value: null, source: "none" },
          { parameter_id: 12, ordinal: 2, name: "Толщина", value_id: null, value: null, source: "path_conflict" },
        ],
      })
    );

    const material = screen.getByText("Материал").closest("li")!;
    expect(material).toHaveTextContent(/^Материал: не уточнено$/);
    const thickness = screen.getByText("Толщина").closest("li")!;
    expect(thickness).toHaveTextContent("не уточнено");
    expect(thickness).toHaveTextContent("разделы расходятся");
  });

  it("коды источников на экран не выходят", () => {
    renderVariant(
      variantOf({
        values: [
          { parameter_id: 11, ordinal: 1, name: "Материал", value_id: 101, value: "металлочерепица", source: "name" },
          { parameter_id: 12, ordinal: 2, name: "Толщина", value_id: null, value: null, source: "path_conflict" },
          { parameter_id: 13, ordinal: 3, name: "Цвет", value_id: null, value: null, source: "none" },
        ],
      })
    );

    expect(document.body.textContent).not.toMatch(/path_conflict|\bnone\b|\bname\b|\bmanual\b|\bpath\b/);
  });

  it("у контекста без варианта строки «Вариант» нет", () => {
    renderVariant(variantOf({ variant_id: null, values: [] }));

    expect(screen.queryByText("Вариант")).not.toBeInTheDocument();
    expect(screen.queryByText("вариант пересчитывается")).not.toBeInTheDocument();
  });

  it("у контекста без варианта, чьи значения считаются, строки «Вариант» тоже нет — только пометка", () => {
    renderVariant(variantOf({ variant_id: null, values: [], values_job_status: "running" }));

    expect(screen.getByText("вариант пересчитывается")).toBeInTheDocument();
    expect(screen.queryByText("Вариант")).not.toBeInTheDocument();
    expect(screen.queryByText("не уточнено")).not.toBeInTheDocument();
  });

  it("вариант без значений читается «не уточнено», а не пустой строкой", () => {
    renderVariant(variantOf({ values: [] }));

    expect(screen.getByText("Вариант").nextElementSibling).toHaveTextContent(/^не уточнено$/);
  });
});

describe("ContextVariant: пометки", () => {
  it("«к делению: разделы расходятся» ведёт к членствам", async () => {
    const user = userEvent.setup();
    const open = vi.fn();
    renderVariant(variantOf({ split_hint: true }), { onOpenMemberships: open });

    expect(screen.getByText("к делению: разделы расходятся")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Показать членства" }));

    expect(open).toHaveBeenCalledTimes(1);
  });

  it("без пометки «к делению» ни надписи, ни перехода нет", () => {
    renderVariant(variantOf({ split_hint: false }));

    expect(screen.queryByText("к делению: разделы расходятся")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Показать членства" })).not.toBeInTheDocument();
  });

  it.each(["pending", "running"] as const)(
    "задание значений в статусе %s: «вариант пересчитывается», и у контекста без варианта, и с ним",
    (status) => {
      const { unmount } = renderVariant(variantOf({ variant_id: null, values: [], values_job_status: status }));
      expect(screen.getByText("вариант пересчитывается")).toBeInTheDocument();
      unmount();

      renderVariant(variantOf({ values_job_status: status }));
      expect(screen.getByText("вариант пересчитывается")).toBeInTheDocument();
    }
  );

  it.each(["privacy_hold", "error", null] as const)(
    "задание значений в статусе %s пометки «вариант пересчитывается» не даёт: экран не знает, что оно идёт",
    (status) => {
      renderVariant(variantOf({ variant_id: null, values: [], values_job_status: status }));

      expect(screen.queryByText("вариант пересчитывается")).not.toBeInTheDocument();
    }
  );

  it("код статуса задания на экран не выходит", () => {
    renderVariant(variantOf({ values_job_status: "privacy_hold" }));

    expect(document.body.textContent).not.toMatch(/privacy_hold|running|pending/);
  });
});
