/**
 * StackOwl — Boot Splash Screen
 *
 * Layout (each boot):
 *   ┌─ header ────────────────────────────────┐
 *   │  text column (32 chars)   owl column    │
 *   └─────────────────────────────────────────┘
 *   ────────────────────────── divider
 *     > Step label ............. [OK] 42ms
 *     > Step label ............. [OK] 81ms
 *   ────────────────────────── divider
 *     [OK] 🦉 OwlName  . provider . model
 */

import chalk from "chalk";
import { BOOT_OWL } from "./owl-art.js";
import { padR } from "./shared/text.js";

// ─── Constants ───────────────────────────────────────────────────

const TEXT_COL_W = 32;   // visible width of left (text) column
const STEP_LBL_W = 36;   // visible width of step label field
const DOTS_W     = 12;   // dots between label and [OK]
const INDENT     = "  "; // 2-space left margin

// ─── Header ──────────────────────────────────────────────────────

const BANNER: string[] = [
  chalk.bold.white("STACKOWL"),
  chalk.dim("personal ai assistant"),
  "",
  chalk.dim("─".repeat(22)),
];

function renderHeader(owlKey: keyof typeof BOOT_OWL): void {
  const owlLines  = BOOT_OWL[owlKey];
  const textLines = [...BANNER];
  const height    = Math.max(textLines.length, owlLines.length);
  const lines: string[] = [];
  for (let i = 0; i < height; i++) {
    const text = padR(INDENT + (textLines[i] ?? ""), TEXT_COL_W);
    const owl  = owlLines[i] ?? "";
    lines.push(text + owl);
  }
  process.stdout.write("\n" + lines.join("\n") + "\n");
}

// ─── Helpers ─────────────────────────────────────────────────────

function divider(cols: number): string {
  return INDENT + chalk.dim("─".repeat(Math.max(0, cols - INDENT.length)));
}

const sleep = (ms: number): Promise<void> =>
  new Promise(r => setTimeout(r, ms));

// ─── Step runner ─────────────────────────────────────────────────

async function runStep(label: string, fn: () => Promise<void>): Promise<void> {
  const lbl  = padR(label, STEP_LBL_W);
  const dots = chalk.dim(".".repeat(DOTS_W));
  process.stdout.write(INDENT + chalk.cyan(">") + " " + chalk.dim(lbl) + " " + dots + " ");
  const t0 = Date.now();
  await fn();
  const ms = Date.now() - t0;
  process.stdout.write(chalk.green("[OK]") + chalk.dim(` ${ms}ms\n`));
}

// ─── BootSplash ──────────────────────────────────────────────────

export interface BootStep {
  label: string;
  fn: () => Promise<void>;
}

export interface BootSplashResult {
  owlName:  string;
  owlEmoji: string;
  provider: string;
  model:    string;
}

export class BootSplash {
  async run(steps: BootStep[], getMeta: () => BootSplashResult): Promise<void> {
    const cols = Math.min(process.stdout.columns ?? 80, 80);

    // 1. Clear + sleeping owl
    process.stdout.write("\x1Bc");
    renderHeader("asleep");
    await sleep(250);

    // 2. Owl wakes — rewind and redraw header in-place
    const headerHeight = Math.max(BANNER.length, BOOT_OWL.asleep.length) + 1;
    const rewind = `\x1B[${headerHeight}A\x1B[0J`;

    process.stdout.write(rewind);
    renderHeader("waking");
    await sleep(300);

    process.stdout.write(rewind);
    renderHeader("awake");
    await sleep(150);

    // 3. Divider + steps
    process.stdout.write("\n" + divider(cols) + "\n");

    // Silence ALL console output while boot steps run
    const origLog  = console.log;
    const origWarn = console.warn;
    const origErr  = console.error;
    console.log   = () => { /* suppressed during boot */ };
    console.warn  = () => { /* suppressed during boot */ };
    console.error = () => { /* suppressed during boot */ };

    for (const step of steps) {
      await runStep(step.label, step.fn);
    }

    console.log   = origLog;
    console.warn  = origWarn;
    console.error = origErr;

    // 4. Ready line
    const meta = getMeta();
    process.stdout.write(
      divider(cols) + "\n\n" +
      INDENT +
        chalk.green("[OK]") + " " +
        chalk.bold(meta.owlEmoji + " " + meta.owlName) + "  " +
        chalk.dim(meta.provider + " · " + meta.model) +
      "\n\n",
    );
  }
}
