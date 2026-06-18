import globals from "globals";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsparser from "@typescript-eslint/parser";
import observabilityPlugin from "./src/infra/observability/eslint-plugin/index.js";

export default [
  {
    ignores: ["dist/**", "node_modules/**", "workspace/**"],
  },
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parser: tsparser,
      parserOptions: {
        ecmaVersion: 2023,
        sourceType: "module",
      },
      globals: {
        ...globals.node,
      },
    },
    plugins: {
      "@typescript-eslint": tseslint,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/explicit-function-return-type": "off",
      "@typescript-eslint/no-explicit-any": "warn",
      "no-console": "off",
      // ─── TUI v2 single-writer contract ────────────────────────────────
      // Only src/cli/v2/io/output.ts may write directly to process.stdout.
      // All other code must import from that module.
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[object.object.name='process'][object.property.name='stdout'][property.name='write']",
          message:
            "Direct process.stdout.write is forbidden outside src/cli/v2/io/output.ts. Import { write, writeln } from that module instead.",
        },
      ],
    },
  },
  // ─── Platform abstraction: warn on direct OS calls outside src/platform/ ──
  {
    files: ["**/*.ts", "**/*.tsx"],
    ignores: ["src/platform/**", "__tests__/**"],
    languageOptions: {
      parser: tsparser,
      parserOptions: {
        ecmaVersion: 2023,
        sourceType: "module",
      },
      globals: {
        ...globals.node,
      },
    },
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.object.name='os'][callee.property.name='tmpdir']",
          message: "Use platform.paths.tempdir() instead. Direct os.tmpdir() is restricted to src/platform/.",
        },
        {
          selector: "CallExpression[callee.object.name='os'][callee.property.name='homedir']",
          message: "Use platform.paths.home() instead. Direct os.homedir() is restricted to src/platform/.",
        },
        {
          selector: "MemberExpression[object.name='process'][property.name='platform']",
          message: "Use platform.systemInfo.current().platform instead.",
        },
      ],
    },
  },
  // ─── Exempt the one module that IS allowed to write stdout ──────────
  {
    files: ["src/cli/v2/io/output.ts"],
    rules: {
      "no-restricted-syntax": "off",
    },
  },
  // ─── Exempt legacy v1 code (will be deleted at cutover) ─────────────
  {
    files: ["src/cli/renderer.ts", "src/gateway/adapters/cli.ts", "src/cli/**/*.ts"],
    rules: {
      "no-restricted-syntax": "off",
    },
  },
  // ─── Exempt src/platform/ and tests from OS call restrictions ──────────
  {
    files: ["src/platform/**/*.ts", "__tests__/**/*.ts"],
    rules: {
      "no-restricted-syntax": "off",
    },
  },
  // ─── Observability: catch-block hygiene rules ─────────────────────────
  {
    plugins: {
      "observability": observabilityPlugin,
    },
    rules: {
      "observability/no-empty-catch": "warn",
      "observability/catch-must-log": "warn",
    },
  },
];