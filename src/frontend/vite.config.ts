import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Proxy /api to the FastAPI backend so the browser talks same-origin in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text"],
      // Everything written for Spec v2.3 is held at 100%. The older panels
      // have no tests yet; they are reported, not gated.
      include: [
        "src/features/scanner/FrozenFormula.tsx",
        "src/features/backtest/FairComparisonFields.tsx",
        "src/features/backtest/TaxSummary.tsx",
        "src/features/scanner/ScannerPanel.tsx",
      ],
      thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
    },
  },
});
