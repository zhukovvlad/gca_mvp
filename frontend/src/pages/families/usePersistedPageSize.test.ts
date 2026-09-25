import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePersistedPageSize } from "./usePersistedPageSize";

/**
 * `usePersistedPageSize` (план Task 6, «Решения плана» п. 5): размер
 * страницы хранится в `localStorage` ключом `gca.families.<список>.pageSize`;
 * недопустимое значение или ошибка доступа к `localStorage` — умолчание.
 *
 * Плана-файл (Task 6 «Files») не заводит отдельного `*.test.ts` под этот
 * хук, но раздел «Утверждения» прямо требует три отдельных проверки
 * (сохранённое допустимое / отсутствующее-недопустимое / бросающий
 * `localStorage`) — без своего файла они утонули бы в `Pager.test.tsx`,
 * который проверяет компонент, а не хранение. Отклонение от буквального
 * списка файлов задачи.
 */
const KEY = "gca.families.contexts.pageSize";

describe("usePersistedPageSize", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("возвращает умолчание, если ключ отсутствует", () => {
    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));
    expect(result.current[0]).toBe(20);
  });

  it("возвращает сохранённое значение, если оно допустимо", () => {
    localStorage.setItem(KEY, "50");
    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));
    expect(result.current[0]).toBe(50);
  });

  it.each(["37", "abc", "", "0", "-10"])(
    "возвращает умолчание на недопустимом значении %j",
    (raw) => {
      localStorage.setItem(KEY, raw);
      const { result } = renderHook(() => usePersistedPageSize(KEY, 20));
      expect(result.current[0]).toBe(20);
    }
  );

  it("сеттер сохраняет допустимое значение в localStorage", () => {
    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));

    act(() => {
      result.current[1](100);
    });

    expect(result.current[0]).toBe(100);
    expect(localStorage.getItem(KEY)).toBe("100");
  });

  it("бросающий localStorage.getItem — умолчание, без падения хука", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("недоступен");
    });

    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));
    expect(result.current[0]).toBe(20);
  });

  it("бросающий localStorage.setItem — сеттер не падает", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("недоступен");
    });

    const { result } = renderHook(() => usePersistedPageSize(KEY, 20));

    expect(() => {
      act(() => {
        result.current[1](50);
      });
    }).not.toThrow();
    expect(result.current[0]).toBe(50);
  });
});
