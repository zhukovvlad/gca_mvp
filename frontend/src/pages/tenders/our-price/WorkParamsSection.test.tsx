import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ourPriceParamFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";
import type { LineParam, ParamsBasis } from "@/types/domain";

import { WorkParamsSection } from "./WorkParamsSection";

const MATCH_HINT =
  "Не учитывается — значение не сравнивается; сборные строки договоров при этом не берутся. Нужен такой же набор — строка договора должна быть сборной из тех же частей.";
const PATH_NOTE =
  "Значение «из разделов смет» общее для строк каталога с этим наименованием; они бывают из разных разделов и смет, какой раздел дал значение — не хранится.";

function renderSection(
  params: LineParam[],
  options: { basis?: ParamsBasis | null; kind?: "work" | "system" } = {}
) {
  return renderWithProviders(
    <WorkParamsSection params={params} basis={options.basis === undefined ? "rung" : options.basis} kind={options.kind ?? "work"} />
  );
}

const rows = () => screen.getAllByTestId("work-param-row");

describe("WorkParamsSection: состав раздела", () => {
  it("параметров нет — раздела нет", () => {
    renderSection([]);
    expect(screen.queryByTestId("sheet-work-params")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Работа" })).not.toBeInTheDocument();
  });

  it("заголовок «Работа» и колонки «Параметр | В строке КП | Откуда | При подборе»", () => {
    renderSection([ourPriceParamFixture()]);
    const section = screen.getByTestId("sheet-work-params");
    expect(within(section).getByRole("heading", { name: "Работа" })).toBeInTheDocument();
    const headers = within(section)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual(["Параметр", "В строке КП", "Откуда", "При подборе"]);
  });

  it("строка: название, значение, откуда и правило подбора — по порядку params", () => {
    renderSection([
      ourPriceParamFixture({ name: "Параметр А", value: "значение А", source: "name", match: "required" }),
      ourPriceParamFixture({ name: "Параметр Б", value: "значение Б", source: "manual", match: "same_set" }),
    ]);
    const [a, b] = rows();
    expect(within(a).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Параметр А",
      "значение А",
      "из наименования",
      "должен совпасть",
    ]);
    expect(within(b).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Параметр Б",
      "значение Б",
      "задано вручную",
      "нужен такой же набор",
    ]);
  });

  it("у второстепенного под названием «второстепенный», у главного — нет", () => {
    renderSection([
      ourPriceParamFixture({ name: "Главный", role: "main" }),
      ourPriceParamFixture({ name: "Побочный", role: "secondary", match: "ignored" }),
    ]);
    const [main, secondary] = rows();
    expect(within(main).queryByText("второстепенный")).not.toBeInTheDocument();
    expect(within(secondary).getByText("второстепенный")).toBeInTheDocument();
  });

  it("колонка «При подборе»: «ближайшее: B40» и «совпал»", () => {
    renderSection([
      ourPriceParamFixture({ name: "Класс", match: "nearest", nearest: { values: ["B40"], without_value: false } }),
      ourPriceParamFixture({ name: "Марка", match: "matched" }),
    ]);
    const [near, matched] = rows();
    expect(within(near).getAllByRole("cell")[3]).toHaveTextContent("ближайшее: B40");
    expect(within(matched).getAllByRole("cell")[3]).toHaveTextContent("совпал");
  });

  it("значение — по общим текстам: не уточнено, расходится по разделам, прежнее", () => {
    renderSection([
      ourPriceParamFixture({ name: "П1", value: null, source: "none", match: null }),
      ourPriceParamFixture({ name: "П2", value: null, source: "path_conflict", match: "required" }),
      ourPriceParamFixture({ name: "П3", value: "W6", source: "stale", catalog_value: "W8", match: "required" }),
    ]);
    const [none, conflict, stale] = rows();
    expect(within(none).getAllByRole("cell")[1]).toHaveTextContent("не уточнено");
    expect(within(none).getAllByRole("cell")[3]).toHaveTextContent("—");
    expect(within(conflict).getAllByRole("cell")[1]).toHaveTextContent("расходится по разделам");
    expect(within(stale).getAllByRole("cell")[1]).toHaveTextContent("W6 — прежнее");
    expect(within(stale).getAllByRole("cell")[2]).toHaveTextContent("в каталоге уже: «W8»");
  });

  it("предупреждающий цвет — только у значения, которому он положен", () => {
    renderSection([
      ourPriceParamFixture({ name: "П1", value: "норма", source: "name" }),
      ourPriceParamFixture({ name: "П2", value: null, source: "none", match: null }),
    ]);
    const [ok, warn] = screen.getAllByTestId("work-param-value");
    expect(ok.classList.contains("text-warning")).toBe(false);
    expect(warn.classList.contains("text-warning")).toBe(true);
  });
});

describe("WorkParamsSection: основание подбора", () => {
  it("rung: заголовок без пояснения каталога, «не учитывается»", () => {
    renderSection([ourPriceParamFixture({ match: "ignored" })], { basis: "rung" });
    expect(screen.getByRole("columnheader", { name: /При подборе/ })).not.toHaveTextContent("настройка каталога");
    expect(within(rows()[0]).getAllByRole("cell")[3]).toHaveTextContent(/^не учитывается$/);
  });

  it("catalog: в заголовке «(аналогов нет — настройка каталога)», «можно не учитывать»", () => {
    renderSection([ourPriceParamFixture({ match: "ignored" })], { basis: "catalog" });
    expect(screen.getByRole("columnheader", { name: /При подборе/ })).toHaveTextContent(
      "(аналогов нет — настройка каталога)"
    );
    expect(within(rows()[0]).getAllByRole("cell")[3]).toHaveTextContent(/^можно не учитывать$/);
  });

  it("catalog: заголовок «При подборе» переносится, подсказка — отдельным блоком под ним", () => {
    renderSection([ourPriceParamFixture({ match: "ignored" })], { basis: "catalog" });
    const head = screen.getByRole("columnheader", { name: /При подборе/ });
    expect(head.classList.contains("whitespace-normal")).toBe(true);
    const hint = screen.getByText("(аналогов нет — настройка каталога)");
    expect(hint).not.toBe(head);
    expect(hint.classList.contains("block")).toBe(true);
  });

  it("basis = null (система без ступени): правил нет, «—», пояснения каталога нет", () => {
    renderSection([ourPriceParamFixture({ match: null })], { basis: null, kind: "system" });
    expect(within(rows()[0]).getAllByRole("cell")[3]).toHaveTextContent(/^—$/);
    expect(screen.getByRole("columnheader", { name: /При подборе/ })).not.toHaveTextContent("настройка каталога");
  });
});

describe("WorkParamsSection: подсказка и примечание", () => {
  it("подсказка заголовка «При подборе» — у работы, дословно §2.3", () => {
    renderSection([ourPriceParamFixture()], { kind: "work" });
    const header = screen.getByRole("columnheader", { name: /При подборе/ });
    expect(within(header).getByTitle(MATCH_HINT)).toBeInTheDocument();
  });

  it("у системы подсказки нет", () => {
    renderSection([ourPriceParamFixture()], { kind: "system" });
    const header = screen.getByRole("columnheader", { name: /При подборе/ });
    expect(within(header).queryByTitle(MATCH_HINT)).not.toBeInTheDocument();
  });

  it("есть параметр из разделов смет — под таблицей примечание, одно", () => {
    renderSection([
      ourPriceParamFixture({ name: "П1", source: "name" }),
      ourPriceParamFixture({ name: "П2", source: "path" }),
      ourPriceParamFixture({ name: "П3", source: "path" }),
    ]);
    expect(screen.getAllByText(PATH_NOTE)).toHaveLength(1);
  });

  it("параметров из разделов нет (path_conflict — не path) — примечания нет", () => {
    renderSection([
      ourPriceParamFixture({ name: "П1", source: "name" }),
      ourPriceParamFixture({ name: "П2", value: null, source: "path_conflict" }),
    ]);
    expect(screen.queryByText(PATH_NOTE)).not.toBeInTheDocument();
  });
});
