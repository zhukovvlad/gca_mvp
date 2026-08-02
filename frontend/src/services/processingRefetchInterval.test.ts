import { describe, expect, it } from "vitest";

import { processingRefetchInterval } from "./processingRefetchInterval";

/** Обёртка: собирает минимальный query-объект вокруг data. */
function q(data: unknown) {
  return { state: { data } };
}

describe("processingRefetchInterval (AC-S1-4)", () => {
  it("массив с processing-документом → 2500", () => {
    expect(processingRefetchInterval(q([{ id: 1, status: "parsed" }, { id: 2, status: "processing" }]))).toBe(2500);
  });
  it("массив с pending-документом → 2500 (pending нетерминален)", () => {
    expect(processingRefetchInterval(q([{ id: 1, status: "pending" }]))).toBe(2500);
  });
  it("все терминальные → false (polling останавливается)", () => {
    expect(processingRefetchInterval(q([{ id: 1, status: "parsed" }, { id: 2, status: "error" }]))).toBe(false);
  });
  it("одиночный документ (detail-квери) → по его статусу", () => {
    expect(processingRefetchInterval(q({ id: 1, status: "processing" }))).toBe(2500);
    expect(processingRefetchInterval(q({ id: 1, status: "error" }))).toBe(false);
  });
  it("нет данных → false", () => {
    expect(processingRefetchInterval(q(undefined))).toBe(false);
  });
});
