import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SchemaEditSummary } from "@/types/domain";

import { EditSummaryPanel } from "./EditSummaryPanel";

/** Сводка правки схемы: тексты по ответу предпросмотра (спека 3в §2.5, §2.11). */

function summaryOf(overrides: Partial<SchemaEditSummary> = {}): SchemaEditSummary {
  return {
    variants_before: 72,
    variants_after_now: 72,
    variants_pending_answers: false,
    asked_by_parameter: [],
    reserve_usd: "0",
    expected_cached_usd: "0",
    value_moves: [],
    retired: [],
    refreshing_contexts: 0,
    repriced_pending_jobs: 0,
    preview_hash: "h",
    ...overrides,
  };
}

function show(summary: SchemaEditSummary | null, pending = false, refusal: string | null = null) {
  return render(<EditSummaryPanel summary={summary} pending={pending} refusal={refusal} />);
}

describe("EditSummaryPanel: варианты", () => {
  it("варианты не меняются: число называется, оттенок не тревожный", () => {
    show(summaryOf());

    expect(screen.getByText("Варианты семьи не меняются: их по-прежнему 72.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Что изменит правка" }).className).not.toContain("warning");
  });

  it("варианты изменились сразу: было и станет, тревожный оттенок", () => {
    show(summaryOf({ variants_before: 18, variants_after_now: 17 }));

    expect(screen.getByText("Варианты семьи изменятся сразу: было 18, станет 17.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Что изменит правка" }).className).toContain("warning");
  });

  it("итог ждёт ответов модели: числа «станет» нет, оттенок тревожный", () => {
    show(summaryOf({ variants_before: 18, variants_after_now: 18, variants_pending_answers: true }));

    expect(
      screen.getByText("Варианты семьи соберутся заново: сейчас 18, сколько станет — будет известно после ответов модели.")
    ).toBeInTheDocument();
    expect(screen.queryByText(/станет \d/)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Что изменит правка" }).className).toContain("warning");
  });
});

describe("EditSummaryPanel: запросы к модели и деньги", () => {
  it("ничего не запрашивается: строки с деньгами нет", () => {
    show(summaryOf());

    expect(screen.getByText("У модели ничего не запрашивается.")).toBeInTheDocument();
    expect(screen.queryByText(/Резерв/)).not.toBeInTheDocument();
  });

  it("запрос по параметру называет имя и число контекстов, деньги — из ответа", () => {
    show(
      summaryOf({
        asked_by_parameter: [{ ordinal: 4, name: "Коэффициент армирования", contexts: 218 }],
        reserve_usd: "1.2500",
        expected_cached_usd: "0.3000",
      })
    );

    expect(screen.getByText("У модели запросится «Коэффициент армирования» — у 218 контекстов.")).toBeInTheDocument();
    expect(screen.getByText(/Резерв \$1,25, ожидаемая цена при попадании в кэш ≈ \$0,30\./)).toBeInTheDocument();
    expect(screen.queryByText("У модели ничего не запрашивается.")).not.toBeInTheDocument();
  });
});

describe("EditSummaryPanel: переносы, выведенные, уточняется", () => {
  it("переносы и выведенные значения называют значения и контексты", () => {
    show(
      summaryOf({
        value_moves: [{ parameter: "Толщина", from_value: "5.1", to_value: "4–6", contexts: 1 }],
        retired: [{ parameter: "Толщина", value: "5.1", contexts: 1 }],
      })
    );

    expect(screen.getByText(/«5\.1» → «4–6» \(1 контекст\)/)).toBeInTheDocument();
    expect(screen.getByText(/Уходят из списка в историю, не удаляются: «5\.1» \(Толщина, 1 контекст\)/)).toBeInTheDocument();
  });

  it("длинный список переносов обрезается и называет остаток числом", () => {
    const moves = Array.from({ length: 8 }, (_, i) => ({
      parameter: "Толщина",
      from_value: `v${i}`,
      to_value: "t",
      contexts: 1,
    }));
    show(summaryOf({ value_moves: moves }));

    expect(screen.getByText(/и ещё 2\./)).toBeInTheDocument();
    expect(screen.queryByText(/«v7»/)).not.toBeInTheDocument();
  });

  it("«уточняется» и пересчёт ждущих запросов — только когда числа не нулевые", () => {
    const { unmount } = show(summaryOf());
    expect(screen.queryByText(/значение уточняется/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ждущие запросы/)).not.toBeInTheDocument();
    unmount();

    show(summaryOf({ refreshing_contexts: 30, repriced_pending_jobs: 3 }));
    expect(screen.getByText(/У 30 контекстов значение уточняется/)).toBeInTheDocument();
    expect(screen.getByText("Пересчитаются ждущие запросы значений семьи: 3.")).toBeInTheDocument();
  });
});

describe("EditSummaryPanel: ожидание и отказ", () => {
  it("пока ответа нет — подпись ожидания, сводки нет", () => {
    show(null, true);

    expect(screen.getByRole("status")).toHaveTextContent("Считаю, что изменит правка…");
    expect(screen.queryByRole("region", { name: "Что изменит правка" })).not.toBeInTheDocument();
  });

  it("ответ прежнего тела при pending не показывается", () => {
    show(summaryOf(), true);

    expect(screen.queryByText(/по-прежнему 72/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Считаю");
  });

  it("отказ проверки — подпись отказа вместо сводки", () => {
    show(null, false, "Два параметра называются одинаково: имена должны различаться.");

    expect(screen.getByRole("status")).toHaveTextContent("Правку нельзя сохранить");
    expect(screen.getByRole("status")).toHaveTextContent("Два параметра называются одинаково");
    expect(screen.queryByRole("region", { name: "Что изменит правка" })).not.toBeInTheDocument();
  });
});
