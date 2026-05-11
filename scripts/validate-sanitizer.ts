#!/usr/bin/env npx tsx
/**
 * validate-sanitizer.ts
 *
 * Standalone validation script for the SkillSanitizer.
 * Run: npx tsx scripts/validate-sanitizer.ts
 *
 * Tests that a simulated OpenClaw-vendor skill gets its references
 * correctly replaced or flagged.
 */

import chalk from "chalk";
import { sanitize } from "../src/skills/sanitizer.js";

// ─── Helpers ──────────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function pass(label: string) {
  console.log(chalk.green("  ✓") + " " + label);
  passed++;
}

function fail(label: string, detail?: string) {
  console.log(chalk.red("  ✗") + " " + label);
  if (detail) console.log(chalk.gray("    " + detail));
  failed++;
}

function section(title: string) {
  console.log("\n" + chalk.bold.blue("▶ " + title));
}

// ─── Fixture: a realistic community skill with vendor references ──────────────

const VENDOR_SKILL = `---
name: pdf_reader
description: Read and summarize PDF files using OpenClaw tools
openclaw:
  emoji: "📄"
  requires:
    bins: [pdftotext]
---

# PDF Reader

This skill uses OpenClaw's built-in PDF parsing via the \`openclaw pdf_extract\` command.

## Configuration

Settings are stored in \`~/.openclaw/config/pdf.json\`. You can override defaults in your
\`~/.claude/settings.json\` as well.

## Steps

1. Run \`openclaw pdf_extract --file <path>\` to extract text.
2. For bulk processing: \`claw install pdf_batch && openclaw pdf_batch --dir <dir>\`
3. Results are stored in \`~/.openclaw/cache/pdf/\`.
4. View the output at https://openclaw.ai/skills/pdf_reader

## Notes

- Requires \`pdftotext\` binary (install via homebrew: \`brew install poppler\`)
- Compatible with Claude Code's CLAUDE.md configuration format
- See \`AGENTS.md\` for multi-agent configuration
- Contact support at https://clawhub.ai/support
`;

// ─── Tests ────────────────────────────────────────────────────────────────────

section("Phase 1: Deterministic substitutions");

const result = sanitize(VENDOR_SKILL);

// Path replacements
if (result.content.includes("~/.stackowl/config/pdf.json")) {
  pass("~/.openclaw/ → ~/.stackowl/");
} else {
  fail("~/.openclaw/ should become ~/.stackowl/", "still contains ~/.openclaw/");
}

if (result.content.includes("~/.stackowl/cache/pdf/")) {
  pass("~/.openclaw/cache/ → ~/.stackowl/cache/");
} else {
  fail("~/.openclaw/cache/ not replaced");
}

// Claude home dir
if (!result.content.includes("~/.claude/settings.json") && result.content.includes("~/.stackowl/settings.json")) {
  pass("~/.claude/ → ~/.stackowl/");
} else {
  fail("~/.claude/ should become ~/.stackowl/");
}

// CLI command replacements
if (!result.content.includes("`openclaw pdf_extract") && result.content.includes("`stackowl pdf_extract")) {
  pass("openclaw CLI → stackowl CLI");
} else {
  fail("openclaw CLI not replaced", "expected `stackowl pdf_extract`");
}

// CLAUDE.md → SKILL.md
if (!result.content.includes("CLAUDE.md") && result.content.includes("SKILL.md")) {
  pass("CLAUDE.md → SKILL.md");
} else {
  fail("CLAUDE.md should become SKILL.md");
}

// AGENTS.md → SKILL.md
if (!result.content.includes("AGENTS.md") && result.content.includes("SKILL.md")) {
  pass("AGENTS.md → SKILL.md");
} else {
  fail("AGENTS.md should become SKILL.md");
}

// Frontmatter should be untouched
if (result.content.includes("openclaw:")) {
  pass("frontmatter openclaw: key preserved (structured metadata)");
} else {
  fail("frontmatter openclaw: key was wrongly removed");
}

section("Phase 1: Replacement report");

if (result.replacements.length > 0) {
  pass(`${result.replacements.length} replacement(s) recorded`);
  for (const r of result.replacements) {
    console.log(
      chalk.gray(`    line ${r.line}: `) +
        chalk.yellow(r.from) +
        chalk.gray(" → ") +
        chalk.green(r.to) +
        (r.count > 1 ? chalk.gray(` (×${r.count})`) : ""),
    );
  }
} else {
  fail("Expected replacements to be recorded");
}

section("Phase 2: Flagged vendor tokens");

if (result.needsReview) {
  pass(`needsReview=true — ${result.flagged.length} token(s) flagged for manual review`);
  for (const f of result.flagged) {
    console.log(
      chalk.gray(`    line ${f.line}: `) +
        chalk.magenta(`[${f.token}]`) +
        chalk.gray(" — ") +
        chalk.yellow(f.context),
    );
  }
} else {
  fail("Expected flagged tokens (openclaw.ai and clawhub.ai URLs should be flagged)");
}

// Specifically: openclaw.ai URL should be flagged (not auto-replaced)
const flaggedUrls = result.flagged.filter((f) =>
  f.context.includes("openclaw.ai"),
);
if (flaggedUrls.length > 0) {
  pass("openclaw.ai URL correctly flagged (not auto-replaced)");
} else {
  fail("openclaw.ai URL should be flagged");
}

section("Edge cases");

// Empty content
const empty = sanitize("");
if (empty.content === "" && empty.replacements.length === 0 && !empty.needsReview) {
  pass("Empty content → clean result");
} else {
  fail("Empty content should produce empty clean result");
}

// Content with no vendor refs
const clean = sanitize("# My Skill\n\nDoes a great thing with no vendor ties.\n");
if (clean.replacements.length === 0 && !clean.needsReview) {
  pass("Clean skill → no replacements, no flags");
} else {
  fail("Clean skill should produce zero changes");
}

// Frontmatter-only (no body)
const frontOnly = sanitize("---\nname: test\ndescription: test\nopenclaw:\n  emoji: x\n---\n");
if (frontOnly.replacements.length === 0) {
  pass("Frontmatter-only skill → no replacements (openclaw: key untouched)");
} else {
  fail("Frontmatter keys should never be replaced");
}

// ─── Summary ─────────────────────────────────────────────────────────────────

console.log("\n" + "─".repeat(50));
if (failed === 0) {
  console.log(chalk.bold.green(`✓ All ${passed} checks passed`));
  process.exit(0);
} else {
  console.log(
    chalk.bold.red(`✗ ${failed} check(s) failed`) +
      chalk.gray(` (${passed} passed)`),
  );
  process.exit(1);
}
