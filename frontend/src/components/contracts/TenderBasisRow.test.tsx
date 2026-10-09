import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { TenderBasisRow } from "./TenderBasisRow";
import { sampleTenderBasis } from "@/test/fixtures";
import type { EstimateOrigin } from "@/types/domain";

function renderRow(origin: EstimateOrigin | null, basis = sampleTenderBasis) {
  return render(
    <MemoryRouter>
      <dl>
        <TenderBasisRow basis={basis} origin={origin} />
      </dl>
    </MemoryRouter>
  );
}

describe("Строка «Основание» карточки договора (дизайн Б2 §2.4)", () => {
  it("ссылка на тендер и «этап N · финал»", () => {
    renderRow("from_offer");

    expect(screen.getByText("Основание")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Т-2026-001/ });
    expect(link).toHaveAttribute("href", "/tenders/300");
    expect(screen.getByText(/этап 2 · финал/)).toBeInTheDocument();
  });

  it("смета, полученная копией КП, помечена «получена из КП»", () => {
    renderRow("from_offer");

    expect(screen.getByText("Смета договора")).toBeInTheDocument();
    expect(screen.getByText("получена из КП")).toBeInTheDocument();
    expect(screen.queryByText("загружена отдельно")).not.toBeInTheDocument();
    expect(screen.queryByText("сметы пока нет")).not.toBeInTheDocument();
  });

  it("смета, загруженная отдельно, помечена «загружена отдельно»", () => {
    renderRow("uploaded_separately");

    expect(screen.getByText("загружена отдельно")).toBeInTheDocument();
    expect(screen.queryByText("получена из КП")).not.toBeInTheDocument();
    expect(screen.queryByText("сметы пока нет")).not.toBeInTheDocument();
  });

  it("договор без сметы помечен «сметы пока нет»", () => {
    renderRow("no_estimate");

    expect(screen.getByText("сметы пока нет")).toBeInTheDocument();
    expect(screen.queryByText("получена из КП")).not.toBeInTheDocument();
    expect(screen.queryByText("загружена отдельно")).not.toBeInTheDocument();
  });

  it("пометка объясняет себя подсказкой макета", () => {
    renderRow("uploaded_separately");

    expect(screen.getByText("загружена отдельно")).toHaveAttribute(
      "title",
      "Смета есть, но копией КП не является; отличается ли она от КП, пометка не утверждает"
    );
  });

  it("без основания строки нет", () => {
    const { container } = render(
      <MemoryRouter>
        <dl>
          <TenderBasisRow basis={null} origin={null} />
        </dl>
      </MemoryRouter>
    );

    expect(container.querySelector("dl")).toBeEmptyDOMElement();
    expect(screen.queryByText("Основание")).not.toBeInTheDocument();
  });
});
