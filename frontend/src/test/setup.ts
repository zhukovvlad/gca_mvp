import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import { server } from "./server";
import { resetHandlerState } from "./handlers";

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

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => resetHandlerState());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
