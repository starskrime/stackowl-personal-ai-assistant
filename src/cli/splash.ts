/**
 * StackOwl — Boot Splash Screen
 *
 * Renders the animated startup sequence:
 *   1. Clear screen + StackOwl banner beside a sleeping owl
 *   2. Owl wakes up over ~600ms
 *   3. Each boot step is printed as it completes (◆ step ··· ✓ Xms)
 *   4. Final "ready" line with provider + model + owl name
 *
 * All output goes to process.stdout — no readline interference.
 * Called BEFORE readline is initialised.
 */

import chalk from "chalk";
import { BOOT_OWL } from "./owl-art.js";

// ─── Layout ──────────────────────────────────────────────────────

const BANNER_LINES = [
  chalk.bold.white("  ◈ STACKOWL"),
  chalk.dim("  personal ai assistant"),
  "",
  chalk.dim("  ─────────────────────"),
];

const OWL_HEIGHT = 5; // must match BOOT_OWL line count

/** Print both the text banner and the owl side-by-side. */
function renderHeader(owlKey: keyof typeof BOOT_OWL): void {
  const owlLines = BOOT_OWL[owlKey];
  const textLines = [...BANNER_LINES];
  // Pad shorter side
  while (textLines.length < OWL_HEIGHT) textLines.push("");
  const rows = Math.max(textLines.length, OWL_HEIGHT);
  const lines: string[] = [];
  for (let i = 0; i < rows; i++) {
    const text = (textLines[i] ?? "").padEnd(30);
    const owl  = owlLines[i] ?? "";
    lines.push(text + owl);
  }
  process.stdout.write("\n" + lines.join("\n") + "\n");
}

// ─── Step renderer ───────────────────────────────────────────────

/** Render a single boot step with a "running" indicator, then resolve. */
async function runStep(
  label: string,
  fn: () => Promise<void>,
): Promise<void> {
  // Print "◆ label ···" without newline
  process.stdout.write(
    `\n  ${chalk.cyan("◆")} ${chalk.dim(label.padEnd(30))} `,
  );
  const t0 = Date.now();
  await fn();
  const ms = Date.now() - t0;
  process.stdout.write(chalk.green("✓") + chalk.dim(` ${ms}ms`));
}

// ─── sleep helper ────────────────────────────────────────────────

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// ─── BootSplash ──────────────────────────────────────────────────

export interface BootStep {
  label: string;
  fn: () => Promise<void>;
}

export interface BootSplashResult {
  owlName: string;
  owlEmoji: string;
  provider: string;
  model: string;
}

export class BootSplash {
  /**
   * Run the full boot animation.
   * @param steps   Ordered list of async initialisation steps
   * @param getMeta Callback invoked AFTER all steps complete — returns
   *                info for the "ready" line (owl, provider, model).
   *                A callback is required because owl info isn't known
   *                until bootstrap has run inside the first step.
   */
  async run(steps: BootStep[], getMeta: () => BootSplashResult): Promise<void> {
    // ── 1. Clear + sleeping owl ──────────────────────────────────
    process.stdout.write("\x1Bc"); // clear screen (ANSI reset)
    renderHeader("asleep");

    await sleep(250);

    // ── 2. Redraw waking owl (overwrite the header block) ────────
    // Move cursor up by (OWL_HEIGHT + BANNER_LINES.length + 1 blank) lines
    const headerRows = Math.max(BANNER_LINES.length, OWL_HEIGHT) + 1; // +1 for leading \n
    process.stdout.write(`\x1B[${headerRows}A`); // cursor up
    process.stdout.write("\x1B[0J");              // erase from cursor down
    renderHeader("waking");

    await sleep(300);

    // ── 3. Redraw awake owl ──────────────────────────────────────
    process.stdout.write(`\x1B[${headerRows}A`);
    process.stdout.write("\x1B[0J");
    renderHeader("awake");

    await sleep(150);

    // ── 4. Horizontal rule + step header ────────────────────────
    const cols = Math.min(process.stdout.columns ?? 80, 72);
    process.stdout.write(
      "\n\n  " + chalk.dim("─".repeat(cols - 4)) + "\n",
    );

    // ── 5. Run steps (suppress noisy console.log during init) ────
    let suppressing = false;

    // Intercept console.log/warn that bootstrap emits (chalk.dim "[...]" lines)
    // so they don't corrupt the step-progress output.
    const origConsoleLog  = console.log;
    const origConsoleWarn = console.warn;
    console.log  = (...args: unknown[]) => {
      const line = args.map(String).join(" ");
      // Only suppress the dim status lines bootstrap emits (wrapped in [ ])
      if (!suppressing || !line.includes("[")) origConsoleLog(...args);
    };
    console.warn = (...args: unknown[]) => {
      if (!suppressing) origConsoleWarn(...args);
    };

    suppressing = true;
    for (const step of steps) {
      await runStep(step.label, step.fn);
    }
    suppressing = false;

    console.log  = origConsoleLog;
    console.warn = origConsoleWarn;

    // ── 6. Ready line (meta known only now) ──────────────────────
    const meta = getMeta();
    process.stdout.write(
      "\n\n  " + chalk.dim("─".repeat(cols - 4)) + "\n\n" +
      `  ${chalk.green("✓")} ${chalk.bold(meta.owlEmoji + " " + meta.owlName)} ` +
      chalk.dim(`· ${meta.provider} · ${meta.model}`) +
      "\n\n",
    );
  }
}
