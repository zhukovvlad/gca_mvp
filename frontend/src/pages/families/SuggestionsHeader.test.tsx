import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { HeldBatchInfo, QueueStatus } from "@/types/domain";

import { SuggestionsHeader } from "./SuggestionsHeader";

/**
 * Шапка вкладки «Предложения» (спека semantic-suggestions §2.12): расход
 * против бюджета и четыре вида плашек. Каждая плашка — при СВОЁМ условии и
 * только при нём: вход на каждое условие и на его отсутствие.
 */

function status(overrides: Partial<QueueStatus> = {}): QueueStatus {
  return {
    spent_24h_usd: "4.2",
    daily_budget_usd: "30",
    claim_paused: null,
    held_batches: [],
    stale_units: [],
    config_stale: null,
    catalog_to_review: 0,
    catalog_position: 0,
    contexts_with_variant: 0,
    contexts_pending: 0,
    families_without_schema: 0,
    ...overrides,
  };
}

function heldBatch(overrides: Partial<HeldBatchInfo> = {}): HeldBatchInfo {
  return {
    batch_id: 7,
    source: "import",
    import_job_id: 21,
    unit_id: null,
    contexts_count: 4210,
    reserve_usd: "17.6",
    expected_cached_usd: "6.05",
    created_at: "2026-09-28T10:00:00+00:00",
    ...overrides,
  };
}

const BANNER_IDS = ["banner-paused", "banner-held", "banner-config", "banner-stale-unit"] as const;

function shownBanners(): string[] {
  return BANNER_IDS.filter((id) => screen.queryAllByTestId(id).length > 0);
}

describe("SuggestionsHeader — расход", () => {
  it("расход и суточный бюджет: «$4,20 из $30», полоса — доля расхода", () => {
    renderWithProviders(<SuggestionsHeader status={status()} onPreview={vi.fn()} />);

    expect(screen.getByText("Расход за 24 ч")).toBeInTheDocument();
    expect(screen.getByText("$4,20")).toBeInTheDocument();
    expect(screen.getByText("из $30")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "14");
  });

  it("сумма, которую float исказил бы, печатается точно, без округления через Number", () => {
    renderWithProviders(
      <SuggestionsHeader
        status={status({ spent_24h_usd: "12345678901234567.89", daily_budget_usd: "99999999999999999.99" })}
        onPreview={vi.fn()}
      />
    );

    // Number("12345678901234567.89") = 12345678901234568: последняя цифра и сотые потеряны.
    // getByText сворачивает неразрывные пробелы в обычные — сравниваем с обычными.
    expect(screen.getByText("$12 345 678 901 234 567,89")).toBeInTheDocument();
  });

  it("перерасход бюджета: полоса зажата на 100", () => {
    renderWithProviders(
      <SuggestionsHeader status={status({ spent_24h_usd: "45.5" })} onPreview={vi.fn()} />
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("SuggestionsHeader — плашки, каждая при своём условии", () => {
  it("без условий ни одной плашки", () => {
    renderWithProviders(<SuggestionsHeader status={status()} onPreview={vi.fn()} />);
    expect(shownBanners()).toEqual([]);
  });

  it.each([
    ["остановка захвата", status({ claim_paused: { reason: "reserve_below_actual", attempt_id: 5, paused_at: "2026-09-28T09:00:00+00:00" } }), ["banner-paused"]],
    ["удержанная пачка", status({ held_batches: [heldBatch()] }), ["banner-held"]],
    ["конфигурация изменена", status({ config_stale: { stale_count: 12, prompt_version_current: 4 } }), ["banner-config"]],
    ["список семей единицы изменён", status({ stale_units: [{ unit_id: 5, unit_code: "M2", stale_count: 82 }] }), ["banner-stale-unit"]],
  ])("%s: одно условие — ровно своя плашка", (_name, input, expected) => {
    renderWithProviders(<SuggestionsHeader status={input} onPreview={vi.fn()} />);
    expect(shownBanners()).toEqual(expected);
  });

  it("остановка захвата: текст и кнопка «Снять остановку» шлёт снятие", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <SuggestionsHeader
        status={status({ claim_paused: { reason: "reserve_below_actual", attempt_id: 5, paused_at: "2026-09-28T09:00:00+00:00" } })}
        onPreview={vi.fn()}
      />
    );

    const banner = screen.getByTestId("banner-paused");
    expect(banner).toHaveTextContent("Захват остановлен: резерв оказался ниже факта");
    await user.click(within(banner).getByRole("button", { name: "Снять остановку" }));

    await waitFor(() => expect(handlerState.resumeWorkerCalls).toBe(1));
  });

  it("удержанная пачка: источник, число контекстов, резерв и ожидаемая цена", () => {
    renderWithProviders(
      <SuggestionsHeader status={status({ held_batches: [heldBatch()] })} onPreview={vi.fn()} />
    );

    const banner = screen.getByTestId("banner-held");
    expect(banner).toHaveTextContent("Удержано: Импорт сметы (job 21)");
    expect(banner).toHaveTextContent(`4 210 контекстов`);
    expect(banner).toHaveTextContent("резерв $17,60");
    expect(banner).toHaveTextContent("ожидаемо ≈ $6,05");
  });

  it("две удержанные пачки — две плашки; пачка без задания импорта не печатает «job»", () => {
    renderWithProviders(
      <SuggestionsHeader
        status={status({
          held_batches: [heldBatch(), heldBatch({ batch_id: 8, source: "mass", import_job_id: null, contexts_count: 1 })],
        })}
        onPreview={vi.fn()}
      />
    );

    const banners = screen.getAllByTestId("banner-held");
    expect(banners).toHaveLength(2);
    expect(banners[1]).toHaveTextContent("Массовая постановка — 1 контекст,");
    expect(banners[1]).not.toHaveTextContent("job");
  });

  it("«Поставить…» открывает preview именно этой пачки", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithProviders(
      <SuggestionsHeader
        status={status({ held_batches: [heldBatch(), heldBatch({ batch_id: 8, source: "mass" })] })}
        onPreview={onPreview}
      />
    );

    await user.click(within(screen.getAllByTestId("banner-held")[1]).getByRole("button", { name: "Поставить…" }));
    expect(onPreview).toHaveBeenCalledWith({ kind: "batch", batchId: 8, source: "mass" });
  });

  it("«Отбросить» шлёт отбрасывание пачки с её id", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <SuggestionsHeader status={status({ held_batches: [heldBatch()] })} onPreview={vi.fn()} />
    );

    await user.click(screen.getByRole("button", { name: "Отбросить" }));
    await waitFor(() => expect(handlerState.discardBatchRequests).toEqual([7]));
  });

  it("«Конфигурация изменена»: версия промпта, число контекстов и «Перезапросить всё…»", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithProviders(
      <SuggestionsHeader
        status={status({ config_stale: { stale_count: 4380, prompt_version_current: 4 } })}
        onPreview={onPreview}
      />
    );

    const banner = screen.getByTestId("banner-config");
    expect(banner).toHaveTextContent("Конфигурация изменена (версия промпта 4)");
    expect(banner).toHaveTextContent("4 380 контекстов");
    await user.click(within(banner).getByRole("button", { name: "Перезапросить всё…" }));
    expect(onPreview).toHaveBeenCalledWith({ kind: "config" });
  });

  it("«Список семей единицы изменён»: подпись единицы символом, кнопка открывает preview этой единицы", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithProviders(
      <SuggestionsHeader
        status={status({ stale_units: [{ unit_id: 5, unit_code: "M2", stale_count: 82 }] })}
        onPreview={onPreview}
      />
    );

    // Символ приходит из справочника единиц, поэтому плашка появляется с кнопкой сразу,
    // а подпись дозагружается: ждём символ.
    const button = await screen.findByRole("button", { name: "Перезапросить м²…" });
    const banner = screen.getByTestId("banner-stale-unit");
    expect(banner).toHaveTextContent("м²: список семей изменён, к перезапросу 82 контекста");
    await user.click(button);
    expect(onPreview).toHaveBeenCalledWith({ kind: "unit", unitId: 5, unitCode: "M2" }, "м²");
  });

  it("устаревшие «без единицы» и единица — две плашки; «без единицы» шлёт unitId null", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithProviders(
      <SuggestionsHeader
        status={status({
          stale_units: [
            { unit_id: null, unit_code: null, stale_count: 3 },
            { unit_id: 3, unit_code: "M3", stale_count: 1 },
          ],
        })}
        onPreview={onPreview}
      />
    );

    expect(screen.getAllByTestId("banner-stale-unit")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Перезапросить без единицы…" }));
    expect(onPreview).toHaveBeenCalledWith({ kind: "unit", unitId: null, unitCode: null }, "без единицы");
  });

  it("все четыре условия сразу — четыре вида плашек", () => {
    renderWithProviders(
      <SuggestionsHeader
        status={status({
          claim_paused: { reason: "reserve_below_actual", attempt_id: 5, paused_at: "2026-09-28T09:00:00+00:00" },
          held_batches: [heldBatch()],
          config_stale: { stale_count: 12, prompt_version_current: 4 },
          stale_units: [{ unit_id: 5, unit_code: "M2", stale_count: 82 }],
        })}
        onPreview={vi.fn()}
      />
    );
    expect(shownBanners()).toEqual([...BANNER_IDS]);
  });
});

describe("SuggestionsHeader — счётчики промоушена", () => {
  const COUNTERS = {
    catalog_to_review: 1384,
    catalog_position: 210,
    contexts_with_variant: 37,
    contexts_pending: 5,
    families_without_schema: 12,
  };

  it("печатает строки каталога на разборе и признанные работами, контексты с вариантом, ожидания и семьи без схемы", () => {
    renderWithProviders(<SuggestionsHeader status={status(COUNTERS)} onPreview={vi.fn()} />);

    expect(screen.getByTestId("counter-to-review")).toHaveTextContent(/Строк на разборе.*1\s?384/);
    expect(screen.getByTestId("counter-position")).toHaveTextContent(/Строк-работ.*210/);
    expect(screen.getByTestId("counter-with-variant")).toHaveTextContent(/Контекстов с вариантом.*37/);
    expect(screen.getByTestId("counter-pending")).toHaveTextContent(/Ожидают семьи.*5/);
    expect(screen.getByTestId("counter-without-schema")).toHaveTextContent(/Семей без схемы.*12/);
  });

  it("нуль — тоже факт: счётчики с нулём печатаются, а не пропадают", () => {
    renderWithProviders(
      <SuggestionsHeader
        status={status({
          catalog_to_review: 0,
          catalog_position: 0,
          contexts_with_variant: 0,
          contexts_pending: 0,
          families_without_schema: 0,
        })}
        onPreview={vi.fn()}
      />
    );

    for (const id of [
      "counter-to-review",
      "counter-position",
      "counter-with-variant",
      "counter-pending",
      "counter-without-schema",
    ]) {
      expect(screen.getByTestId(id)).toHaveTextContent(/0$/);
    }
  });

  it("имена полей ответа на экран не выходят", () => {
    renderWithProviders(<SuggestionsHeader status={status(COUNTERS)} onPreview={vi.fn()} />);

    expect(document.body.textContent).not.toMatch(
      /catalog_to_review|catalog_position|contexts_with_variant|contexts_pending|families_without_schema/
    );
  });
});
