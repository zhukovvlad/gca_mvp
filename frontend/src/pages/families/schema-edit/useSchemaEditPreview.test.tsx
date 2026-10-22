import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { semanticApi } from "@/services/api/domain";
import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import type { SchemaEditDraft, SchemaEditSummary } from "@/types/domain";

import { SCHEMA_EDIT_PREVIEW_DELAY_MS, useSchemaEditPreview } from "./useSchemaEditPreview";

/**
 * Предпросмотр правки схемы (спека 3в §2.11, решение плана 6): запрос через 400 мс после
 * изменения тела; ответ на устаревшее тело отбрасывается по счётчику запроса; сводка относится
 * только к нынешнему телу. Паузы — фейковые таймеры, гонка ответов — управляемые промисы
 * обработчика, а не `sleep`.
 */

const LATER = { timeout: 8000 };

/** Тело из одного нового параметра: проходит проверки сервера при любом имени, кроме занятого. */
function draftsOf(name: string): SchemaEditDraft[] {
  return [{ ordinal: null, name, role: "main", value_kind: "exact", closed: false, values: ["да"], range: null }];
}

function hashOf(name: string): string {
  return `edit-${SCHEMA_FAMILY_ID}-2-r1-${JSON.stringify(draftsOf(name))}`;
}

function summaryWith(previewHash: string): SchemaEditSummary {
  return {
    variants_before: 2,
    variants_after_now: 2,
    variants_pending_answers: false,
    asked_by_parameter: [],
    reserve_usd: "0",
    expected_cached_usd: "0",
    value_moves: [],
    retired: [],
    refreshing_contexts: 0,
    repriced_pending_jobs: 0,
    preview_hash: previewHash,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useSchemaEditPreview: задержка (фейковые таймеры, сеть подменена на уровне клиента)", () => {
  function spyOnPreview() {
    return vi
      .spyOn(semanticApi, "schemaEditPreview")
      .mockImplementation((_familyId, parameters) => Promise.resolve(summaryWith(`spy-${parameters[0].name}`)));
  }

  it("до 400 мс запроса нет, по истечении уходит один, сводка относится к телу", async () => {
    const spy = spyOnPreview();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { result } = renderHook(() => useSchemaEditPreview(SCHEMA_FAMILY_ID, draftsOf("Толщина")));

    expect(result.current).toEqual({ summary: null, pending: true, refusal: null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEMA_EDIT_PREVIEW_DELAY_MS - 1);
    });
    expect(spy).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    // Ответ подменённого клиента — микрозадача: сбрасываем её, таймеры не трогая (`waitFor` их не видит).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(result.current.pending).toBe(false);
    expect(result.current.summary?.preview_hash).toBe("spy-Толщина");
    expect(result.current.refusal).toBeNull();
  });

  it("изменения тела внутри паузы сливаются: уходит один запрос с последним телом", async () => {
    const spy = spyOnPreview();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { result, rerender } = renderHook(({ name }) => useSchemaEditPreview(SCHEMA_FAMILY_ID, draftsOf(name)), {
      initialProps: { name: "Т" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    rerender({ name: "То" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    rerender({ name: "Толщина" });
    expect(spy).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEMA_EDIT_PREVIEW_DELAY_MS);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(SCHEMA_FAMILY_ID, draftsOf("Толщина"));
    expect(result.current.pending).toBe(false);
    expect(result.current.summary?.preview_hash).toBe("spy-Толщина");
  });

  it("размонтирование до паузы запрос не отправляет", async () => {
    const spy = spyOnPreview();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { unmount } = renderHook(() => useSchemaEditPreview(SCHEMA_FAMILY_ID, draftsOf("Толщина")));
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEMA_EDIT_PREVIEW_DELAY_MS * 2);
    });

    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useSchemaEditPreview: ответ на устаревшее тело", () => {
  it("поздний ответ на прежнее тело отброшен: сводка нынешнего тела не перетирается", async () => {
    const releases: Array<() => void> = [];
    handlerState.schemaEditPreviewGate = (body) =>
      new Promise<void>((resolve) => {
        const first = (body.parameters as SchemaEditDraft[])[0].name === "Первое";
        if (first) releases.push(resolve);
        else resolve();
      });
    const { result, rerender } = renderHook(({ name }) => useSchemaEditPreview(SCHEMA_FAMILY_ID, draftsOf(name)), {
      initialProps: { name: "Первое" },
    });
    await waitFor(() => expect(handlerState.schemaEditPreviews).toHaveLength(1), LATER);

    rerender({ name: "Второе" });
    await waitFor(() => expect(result.current.summary?.preview_hash).toBe(hashOf("Второе")), LATER);
    expect(result.current.pending).toBe(false);
    // Ответ на «Первое» приходит ПОСЛЕ ответа на «Второе».
    await act(async () => {
      releases.forEach((release) => release());
      await Promise.resolve();
    });
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(releases).toHaveLength(1);
    expect(result.current.summary?.preview_hash).toBe(hashOf("Второе"));
    expect(result.current.pending).toBe(false);
  });

  it("поздний ОТКАЗ на прежнее тело тоже отброшен: сводка нынешнего тела остаётся, отказа нет", async () => {
    // Ревью задачи 8: отбрасывание по счётчику стоит в обеих ветках ответа, а предъявлена была
    // только ветка успеха — поздний отказ перетёр бы сводку и оставил окно в ожидании навсегда.
    const releases: Array<() => void> = [];
    const duplicate: SchemaEditDraft[] = [
      ...draftsOf("Материал").map((draft) => ({ ...draft, ordinal: 1 })),
      ...draftsOf("материал"),
    ];
    handlerState.schemaEditPreviewGate = (body) =>
      new Promise<void>((resolve) => {
        if ((body.parameters as SchemaEditDraft[]).length === 2) releases.push(resolve);
        else resolve();
      });
    const { result, rerender } = renderHook(({ drafts }) => useSchemaEditPreview(SCHEMA_FAMILY_ID, drafts), {
      initialProps: { drafts: duplicate },
    });
    await waitFor(() => expect(handlerState.schemaEditPreviews).toHaveLength(1), LATER);

    rerender({ drafts: draftsOf("Второе") });
    await waitFor(() => expect(result.current.summary?.preview_hash).toBe(hashOf("Второе")), LATER);
    await act(async () => {
      releases.forEach((release) => release());
      await Promise.resolve();
    });
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(releases).toHaveLength(1);
    expect(result.current.summary?.preview_hash).toBe(hashOf("Второе"));
    expect(result.current.refusal).toBeNull();
    expect(result.current.pending).toBe(false);
  });

  it("пока ответа на нынешнее тело нет, сводки нет и pending истинно — прежняя сводка не показывается", async () => {
    const { result, rerender } = renderHook(({ name }) => useSchemaEditPreview(SCHEMA_FAMILY_ID, draftsOf(name)), {
      initialProps: { name: "Первое" },
    });
    await waitFor(() => expect(result.current.pending).toBe(false), LATER);
    expect(result.current.summary).not.toBeNull();

    rerender({ name: "Второе" });

    expect(result.current.summary).toBeNull();
    expect(result.current.pending).toBe(true);
    await waitFor(() => expect(result.current.summary?.preview_hash).toBe(hashOf("Второе")), LATER);
  });
});

describe("useSchemaEditPreview: отказ и перезапрос", () => {
  it("отказ проверки — подпись в refusal, сводки нет, pending снят", async () => {
    const drafts: SchemaEditDraft[] = [
      ...draftsOf("Материал").map((draft) => ({ ...draft, ordinal: 1 })),
      ...draftsOf("материал"),
    ];
    const { result } = renderHook(() => useSchemaEditPreview(SCHEMA_FAMILY_ID, drafts));

    await waitFor(() => expect(result.current.pending).toBe(false), LATER);

    expect(result.current.summary).toBeNull();
    expect(result.current.refusal).toBe("Два параметра называются одинаково: имена должны различаться.");
  });

  it("refreshKey перезапрашивает предпросмотр при том же теле", async () => {
    const drafts = draftsOf("Толщина");
    const { result, rerender } = renderHook(({ key }) => useSchemaEditPreview(SCHEMA_FAMILY_ID, drafts, key), {
      initialProps: { key: 0 },
    });
    await waitFor(() => expect(result.current.pending).toBe(false), LATER);
    expect(handlerState.schemaEditPreviews).toHaveLength(1);

    rerender({ key: 1 });

    expect(result.current.pending).toBe(true);
    await waitFor(() => expect(handlerState.schemaEditPreviews).toHaveLength(2), LATER);
    await waitFor(() => expect(result.current.pending).toBe(false), LATER);
  });

  it("тот же состав тела новой ссылкой на массив запрос не повторяет", async () => {
    const { result, rerender } = renderHook(() => useSchemaEditPreview(SCHEMA_FAMILY_ID, draftsOf("Толщина")));
    await waitFor(() => expect(result.current.pending).toBe(false), LATER);

    rerender();
    await new Promise((resolve) => setTimeout(resolve, SCHEMA_EDIT_PREVIEW_DELAY_MS + 100));

    expect(handlerState.schemaEditPreviews).toHaveLength(1);
    expect(result.current.pending).toBe(false);
  });
});
