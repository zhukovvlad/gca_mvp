import { describe, expect, it } from "vitest";

import { compareNullable } from "./sorting";

describe("compareNullable", () => {
  it.each(["asc", "desc"] as const)("две пустые равны (%s): компаратор не должен ставить их по-разному", (dir) => {
    expect(compareNullable(null, null, dir)).toBe(0);
  });

  it.each(["asc", "desc"] as const)("пустое всегда в конце (%s) — с обеих сторон", (dir) => {
    expect(compareNullable(null, "1", dir)).toBe(1);
    expect(compareNullable("1", null, dir)).toBe(-1);
  });

  it("числа сравниваются по направлению: меньшее раньше при asc, позже при desc", () => {
    expect(compareNullable("2", "10", "asc")).toBe(-1);
    expect(compareNullable("2", "10", "desc")).toBe(1);
    expect(compareNullable("10", "2", "asc")).toBe(1);
  });
});
