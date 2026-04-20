/**
 * StackOwl — Boot Splash Screen
 *
 * Dark Glass boot animation:
 *   1. Clear screen + logo + sleeping owl
 *   2. Owl wakes up over ~600ms (3 frames)
 *   3. Each boot step runs with clean > label -------- [OK] Xms
 *   4. Final ready line: [OK] OwlName . Provider . Model
 */

import chalk from "chalk";
import { BOOT_OWL } from "./owl-art.js";

// ─── Thin divider ────────────────────────────────────────────────

const DIV = "-";

// ─── Banner ──────────────────────────────────────────────────────

const BANNER_LINES = [
  chalk.bold.white("  STACKOWL"),
  chalk.dim("  personal ai assistant"),
  "",
  chalk.dim("  " + DIV.repeat(22)),
];

const OWL_HEIGHT = 5;

// ─── Header renderer ─────────────────────────────────────────────

function renderHeader(owlKey: keyof typeof BOOT_OWL): void {
  const owlLines = BOOT_OWL[owlKey];
  const textLines = [...BANNER_LINES];
  while (textLines.length < OWL_HEIGHT) textLines.push("");
  const rows = Math.max(textLines.length, OWL_HEIGHT);
  const lines: string[] = [];
  for (let i = 0; i < rows; i++) {
    const text = (textLines[i] ?? "").padEnd(30);
    const owl = owlLines[i] ?? "";
    lines.push(text + owl);
  }
  process.stdout.write("\n" + lines.join("\n") + "\n");
}

// ─── Step renderer ───────────────────────────────────────────────

async function runStep(label: string, fn: () => Promise<void>): Promise<void> {
  const displayLabel =
    label.length > 28 ? label.slice(0, 27) + "." : label.padEnd(28);
  process.stdout.write(
    "\n  " +
      chalk.cyan(">") +
      " " +
      chalk.dim(displayLabel) +
      " " +
      chalk.dim(DIV.repeat(16)) +
      " ",
  );
  const t0 = Date.now();
  await fn();
  const ms = Date.now() - t0;
  process.stdout.write(chalk.green("[OK]") + chalk.dim(" " + ms + "ms"));
}

// ─── Sleep ───────────────────────────────────────────────────────

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
  async run(steps: BootStep[], getMeta: () => BootSplashResult): Promise<void> {
    // 1. Clear + sleeping owl
    process.stdout.write("\x1Bc");
    renderHeader("asleep");

    await sleep(250);

    // 2. Waking owl
    const headerRows = Math.max(BANNER_LINES.length, OWL_HEIGHT) + 1;
    process.stdout.write("\x1B[" + headerRows + "A");
    process.stdout.write("\x1B[0J");
    renderHeader("waking");

    await sleep(300);

    // 3. Awake owl
    process.stdout.write("\x1B[" + headerRows + "A");
    process.stdout.write("\x1B[0J");
    renderHeader("awake");

    await sleep(150);

    // 4. Horizontal rule
    const cols = Math.min(process.stdout.columns ?? 80, 72);
    process.stdout.write("\n\n  " + chalk.dim(DIV.repeat(cols - 4)) + "\n");

    // 5. Run steps (suppress noisy console.log during init)
    const origConsoleLog = console.log;
    const origConsoleWarn = console.warn;
    let suppressing = false;

    console.log = (...args: unknown[]) => {
      const line = args.map(String).join(" ");
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

    console.log = origConsoleLog;
    console.warn = origConsoleWarn;

    // 6. Ready line
    const meta = getMeta();
    process.stdout.write(
      "\n\n  " +
        chalk.dim(DIV.repeat(cols - 4)) +
        "\n\n" +
        "  " +
        chalk.green("[OK]") +
        " " +
        chalk.bold(meta.owlEmoji + " " + meta.owlName) +
        " " +
        chalk.dim(". " + meta.provider + " . " + meta.model) +
        "\n\n",
    );
  }
}
