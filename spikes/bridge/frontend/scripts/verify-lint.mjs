#!/usr/bin/env node
// Story 1.5 AC: "Given spikes/bridge/frontend/src/, when npm run lint runs,
// then it fails on a fixture containing {@html}/innerHTML/insertAdjacentHTML
// and passes on the real source." This one script (the whole of `npm run
// lint`) makes both halves of that claim true in one command:
//
//   1. Lints the REAL source under src/ and requires zero errors -- if this
//      kit's own code ever regresses to a banned pattern, `npm run lint`
//      fails.
//   2. Lints three fixtures (never under src/, so they never affect (1)) --
//      each one contains exactly one banned pattern and MUST fail lint, or
//      this script fails. This is what proves the ban itself still fires
//      rather than having silently rotted into a no-op.
//
// Never weakens either half to get a green run (mirrors AD-36's own "never
// relax the policy to make a page work").
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ESLint } from "eslint";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

async function lintRealSource() {
  const eslint = new ESLint({ cwd: root });
  const results = await eslint.lintFiles(["src/**/*.{ts,svelte}"]);
  const errorCount = results.reduce((sum, result) => sum + result.errorCount, 0);
  if (errorCount > 0) {
    const formatter = await eslint.loadFormatter("stylish");
    console.error(await formatter.format(results));
    throw new Error(`lint: ${errorCount} error(s) in real frontend source under src/ -- expected zero`);
  }
  console.log("lint: src/ is clean (0 errors)");
}

const FORBIDDEN_FIXTURES = [
  { label: ".innerHTML = assignment", file: "fixtures/forbidden-inner-html.ts" },
  { label: ".insertAdjacentHTML( call", file: "fixtures/forbidden-insert-adjacent-html.ts" },
  { label: "{@html} tag", file: "fixtures/forbidden-at-html.svelte" },
];

async function lintForbiddenFixtures() {
  const eslint = new ESLint({ cwd: root });
  for (const fixture of FORBIDDEN_FIXTURES) {
    const filePath = path.join(root, fixture.file);
    const source = await readFile(filePath, "utf8");
    const [result] = await eslint.lintText(source, { filePath });
    if (!result) {
      throw new Error(
        `lint: fixture '${fixture.label}' (${fixture.file}) got no result from eslint.lintText() -- ` +
          "expected exactly one LintResult back for this one file",
      );
    }
    if (result.errorCount === 0) {
      throw new Error(
        `lint: fixture '${fixture.label}' (${fixture.file}) was expected to FAIL lint but passed -- ` +
          "the ban on this pattern is not actually enforced",
      );
    }
    console.log(`lint: fixture '${fixture.label}' correctly failed lint (${result.errorCount} error(s))`);
  }
}

await lintRealSource();
await lintForbiddenFixtures();
console.log("lint: OK");
