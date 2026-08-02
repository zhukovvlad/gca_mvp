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

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => resetHandlerState());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
