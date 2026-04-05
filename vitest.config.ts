import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: [
        "src/**/*.d.ts",
        "src/types/**/*.ts",
        "src/**/index.ts",
        "src/examples/**",
        "src/workspace/**",
        "src/products/**",
        "src/tools/**",
        "src/tournaments/**",
        "src/trust/**",
      ],
      thresholds: {
        lines: 95,
        functions: 95,
        branches: 95,
        statements: 95,
      },
    },
  },
});
