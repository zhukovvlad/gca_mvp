import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { articleRefFixture, chapterPathFixture } from "@/test/fixtures";

import { LineWhere } from "./LineWhere";

describe("«где строка»", () => {
  it("цепочка статей от корня: код и название каждой, в порядке цепочки", () => {
    render(<LineWhere article={articleRefFixture()} chapterPath={[]} />);
    const steps = within(screen.getByTestId("where-article")).getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual(["4 Несущие конструкции", "4.2.4 Вертикальные конструкции"]);
  });

  it("статья без кода: только название, без «null» и лишнего пробела", () => {
    const article = articleRefFixture({
      code: null,
      title: "Статья без кода",
      chain: [{ id: 31, code: null, title: "Статья без кода" }],
    });
    render(<LineWhere article={article} chapterPath={[]} />);
    expect(within(screen.getByTestId("where-article")).getByRole("listitem").textContent).toBe("Статья без кода");
  });

  it("полный путь по смете с номерами разделов", () => {
    render(<LineWhere article={null} chapterPath={chapterPathFixture("Здание", "1 Этап", "Конструкции")} />);
    const path = within(screen.getByTestId("where-path")).getAllByRole("listitem");
    expect(path.map((p) => p.textContent)).toEqual(["п. 4 — Здание", "п. 4.1 — 1 Этап", "п. 4.1.2 — Конструкции"]);
  });

  it("раздел без номера: только название, без «п.»", () => {
    render(
      <LineWhere
        article={null}
        chapterPath={[{ chapter_item_id: 1, title: "Раздел без номера", number: null }]}
      />
    );
    expect(within(screen.getByTestId("where-path")).getByRole("listitem").textContent).toBe("Раздел без номера");
  });

  it("подпись пути: смета КП по умолчанию, смета договора — у аналога", () => {
    const { unmount } = render(<LineWhere article={null} chapterPath={chapterPathFixture("Здание")} />);
    expect(screen.getByTestId("line-where")).toHaveTextContent("в смете КП");
    unmount();
    render(<LineWhere article={null} chapterPath={chapterPathFixture("Здание")} scope="contract" />);
    expect(screen.getByTestId("line-where")).toHaveTextContent("в смете договора");
    expect(screen.getByTestId("line-where")).not.toHaveTextContent("в смете КП");
  });

  it("пустой путь: блока пути нет, статья на месте, без падения", () => {
    render(<LineWhere article={articleRefFixture()} chapterPath={[]} />);
    expect(screen.getByTestId("where-article")).toBeInTheDocument();
    expect(screen.queryByTestId("where-path")).not.toBeInTheDocument();
  });

  it("статьи нет («Нераспределённое»): блока статьи нет, путь на месте", () => {
    render(<LineWhere article={null} chapterPath={chapterPathFixture("Здание")} />);
    expect(screen.queryByTestId("where-article")).not.toBeInTheDocument();
    expect(screen.getByTestId("where-path")).toBeInTheDocument();
  });

  it("статьи нет и пути нет — не рисуется вовсе", () => {
    const { container } = render(<LineWhere article={null} chapterPath={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("статья не пришла (поле отсутствует у строки разбора): как «нет статьи»", () => {
    const { container } = render(<LineWhere article={undefined} chapterPath={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
