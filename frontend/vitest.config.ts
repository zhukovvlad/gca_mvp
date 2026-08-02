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
