// Story 1.5 (AD-36): this kit's own strict CSP + Trusted Types policy has no
// `unsafe-inline`/`unsafe-eval` and requires `require-trusted-types-for
// 'script'`, so `{@html}`, `.innerHTML =` and `.insertAdjacentHTML(` must
// never appear anywhere under src/ -- they either violate Trusted Types at
// runtime (assigning a raw string to a Trusted-Types-gated sink throws) or,
// worse, silently rely on some other page relaxing the policy. This file is
// the lint tripwire: `svelte/no-at-html-tags` bans `{@html}`, and the two
// custom rules below ban `.innerHTML =` and `.insertAdjacentHTML(` in both
// `.ts` and `.svelte` script blocks.
import js from "@eslint/js";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import tseslint from "typescript-eslint";

const noInnerHtmlAssignment = {
  selector:
    "AssignmentExpression[left.type='MemberExpression'][left.property.name='innerHTML'], " +
    "AssignmentExpression[left.type='MemberExpression'][left.computed=true][left.property.value='innerHTML']",
  message:
    "'.innerHTML =' is banned under this kit's strict CSP (AD-36, require-trusted-types-for 'script'). " +
    "Build DOM nodes and use replaceChildren()/textContent/createElement() instead.",
};

const noInsertAdjacentHtml = {
  selector:
    "CallExpression[callee.type='MemberExpression'][callee.property.name='insertAdjacentHTML']",
  message:
    "'.insertAdjacentHTML(' is banned under this kit's strict CSP (AD-36). " +
    "Build DOM nodes and use insertBefore()/appendChild() instead.",
};

export default tseslint.config(
  {
    // fixtures/ is intentionally NOT ignored here: scripts/verify-lint.mjs
    // lints those files by explicit path via ESLint's lintText(), and an
    // "ignores" match would make ESLint report them as skipped (0 errors)
    // rather than actually linted -- silently defeating the fixture's own
    // "the ban still fires" proof.
    ignores: ["build/**", "node_modules/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...svelte.configs.recommended,
  {
    languageOptions: {
      globals: { ...globals.browser },
    },
  },
  {
    files: ["**/*.{ts,mts,svelte}"],
    rules: {
      "no-restricted-syntax": ["error", noInnerHtmlAssignment, noInsertAdjacentHtml],
      "svelte/no-at-html-tags": "error",
    },
  },
  {
    files: ["**/*.svelte"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },
);
