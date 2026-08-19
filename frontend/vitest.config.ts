/// <reference types="vitest" />
import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // css: false — дефолт Vitest; НЕ включать. jsdom не считает layout/каскад,
    // поэтому обработка CSS не даёт реальной проверки стилей, но с Tailwind v4
    // раздувает transform/import/environment (прогон ~87с → ~33с при выключении).
    // Тесты через testing-library проверяют DOM/атрибуты (className остаётся в
    // разметке), стили им не нужны. Визуальные проверки — отдельным слоем (не jsdom).
    css: false,
    // Умолчательные 5 секунд на тест перестали хватать после фичи поправки на
    // инфляцию: набор вырос до 32 файлов, и на полном прогоне vitest держит
    // несколько jsdom-воркеров сразу. Замер 2026-08-19 — падения приходили
    // ПАРАМИ: сначала таймаут ~5050 мс в тяжёлом файле (диалог + MSW +
    // userEvent), сразу за ним быстрое падение соседнего теста, потому что
    // зависший запрос упавшего теста доезжает уже в следующий (счётчик запросов
    // видел 2 PATCH вместо 1). Каждый из этих файлов в ОДИНОЧКУ зелёный 3
    // прогона из 3, и пара файлов вместе — тоже.
    //
    // Поднятие таймаута не прячет логических дефектов: они падают на
    // утверждении, а не по времени. Прятало бы обратное — оставить 5 секунд и
    // списывать красное на «мигает»: красное, не являющееся дефектом, стоит
    // ровно столько же, сколько зелёное, не являющееся проходом.
    testTimeout: 20000,
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/components/ui/**",
        "src/main.tsx",
        "src/App.tsx",
        "**/*.d.ts",
        "**/*.test.*",
        "src/test/**",
      ],
    },
  },
});
