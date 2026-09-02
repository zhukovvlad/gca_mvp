import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import { server } from "./server";
import { resetHandlerState } from "./handlers";

// Файл — общий setup ОБОИХ окружений: jsdom (компонентные тесты) и node
// (тесты чистых функций, помеченные `@vitest-environment node`). Заглушки ниже
// нужны только там, где есть DOM, и в node-окружении их постановка упала бы на
// отсутствующем `window`. Поэтому весь DOM-блок — под одним признаком, а не под
// проверкой на каждую заглушку: признак ОДИН (есть ли DOM), и разносить его на
// три независимых условия значило бы делать вид, что бывает `window` без
// `Element`. `jest-dom`-матчеры импортируются здесь же — они обращаются к
// `Element` в момент СРАВНЕНИЯ, а не импорта, но в node-тестах не нужны вовсе.
const HAS_DOM = typeof window !== "undefined";

if (HAS_DOM) {
  await import("@testing-library/jest-dom/vitest");

  // jsdom не реализует window.matchMedia, а next-themes/ThemeProvider его требует.
  // Возвращаем стабильный no-op мок (matches=false → светлая тема по умолчанию).
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  // jsdom не реализует ResizeObserver, а cmdk (основа shadcn `Command`, то есть и
  // комбобокса «выбрать или создать») подписывается на него при монтировании.
  // Заглушка no-op: измерения в тестах не проверяются, важно лишь чтобы компонент
  // смонтировался.
  if (!("ResizeObserver" in globalThis)) {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Object.defineProperty(globalThis, "ResizeObserver", {
      writable: true,
      value: ResizeObserverStub,
    });
  }

  // jsdom не реализует scrollIntoView — cmdk зовёт его, подсвечивая активный пункт
  // списка. Тоже no-op: прокрутка в тестах не наблюдаема.
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function scrollIntoView() {};
  }
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => resetHandlerState());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
