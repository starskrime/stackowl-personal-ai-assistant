/**
 * StackOwl — Interactive Onboarding Wizard
 *
 * Two render modes:
 *   standalone  (inAltScreen=false) — enters its own alt screen, full-screen layout
 *   framed      (inAltScreen=true)  — reuses the existing alt screen + draws the
 *                                     same yellow outer border as TerminalUI so the
 *                                     user never sees a "different window"
 *
 * Sections
 *   A  You        — name, work type, communication style
 *   B  Provider   — AI backend + API key + model
 *   C  Channels   — CLI (always), Web UI, Telegram, Slack
 *   D  Features   — Memory, Proactive, Voice, Web Face, …
 *   E  Review     — confirm & write stackowl.config.json
 */

import { writeFile, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import chalk         from "chalk";
import { ModelLoader } from "../models/loader.js";

// ─── ANSI ────────────────────────────────────────────────────────

const ESC = "\x1B";
const A = {
  altIn:  `${ESC}[?1049h`,
  altOut: `${ESC}[?1049l`,
  hide:   `${ESC}[?25l`,
  show:   `${ESC}[?25h`,
  clear:  `${ESC}[2J${ESC}[H`,
  pos:    (r: number, c = 1) => `${ESC}[${r};${c}H`,
  el:     `${ESC}[2K`,
  bold:   (s: string) => `\x1B[1m${s}\x1B[0m`,
};

function w(s: string) { process.stdout.write(s); }
function cols() { return Math.max(process.stdout.columns ?? 80, 60); }
function rows() { return Math.max(process.stdout.rows    ?? 24, 16); }

// ─── Frame state (set once per run() call) ────────────────────────
// When _frameMode=true the outer yellow chrome occupies rows 1-3 and
// rows-2 .. rows (bottom).  All wizard content is offset by _frameOffset.

let _frameMode   = false;
let _frameOffset = 0;   // 0 standalone, 3 framed

/** Absolute row for a wizard-relative row number. */
function ar(rel: number): number { return rel + _frameOffset; }

/** ANSI position string with frame offset applied. */
function p(rel: number, col = 1): string { return A.pos(ar(rel), col); }

/** Last content row (inclusive) — stops before the bottom chrome. */
function contentEnd(): number {
  return _frameMode ? rows() - 3 : rows();
}

/** Row used by hintLine(). */
function hintRow(): number {
  return _frameMode ? rows() - 1 : rows();
}

/** Clear rows [fromRel, toAbsOrEnd] within the content area. */
function clearRange(fromRel: number, toAbs?: number): void {
  const from = ar(fromRel);
  const to   = toAbs ?? contentEnd();
  const c    = cols();
  let   o    = "";
  for (let r = from; r <= to; r++) {
    o += A.pos(r, 1) + " ".repeat(c);
  }
  w(o);
}

// ─── Raw mode ────────────────────────────────────────────────────

function enterRaw() {
  if (process.stdin.isTTY) process.stdin.setRawMode(true);
  process.stdin.resume();
  process.stdin.setEncoding("utf8");
}

function exitRaw() {
  if (process.stdin.isTTY) {
    try { process.stdin.setRawMode(false); } catch { /* ignore */ }
  }
}

function waitForKey(): Promise<string> {
  return new Promise((resolve) => {
    const handler = (chunk: unknown) => {
      const key = typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
      process.stdin.off("data", handler);
      resolve(key);
    };
    process.stdin.once("data", handler);
  });
}

// ─── Frame chrome ────────────────────────────────────────────────
// Draws the outer yellow border + topbar + shortcuts bar.
// Called once before any wizard content renders.

const Y  = chalk.yellow;
const D  = chalk.dim;

const BOX = { tl:"┌",tr:"┐",bl:"└",br:"┘",h:"─",v:"│",ml:"├",mr:"┤" };

function drawFrame(): void {
  const c = cols();
  const r = rows();
  let   o = "";

  // Row 1 — top border
  o += A.pos(1) + Y(BOX.tl + BOX.h.repeat(c - 2) + BOX.tr);

  // Row 2 — top bar
  const title = "  🦉 StackOwl  ·  /onboarding — Setup Wizard";
  const inner = c - 2;
  const titlePad = title + " ".repeat(Math.max(0, inner - title.length));
  o += A.pos(2) + Y(BOX.v) + chalk.bgYellow.black(titlePad) + Y(BOX.v);

  // Row 3 — divider
  o += A.pos(3) + Y(BOX.ml + BOX.h.repeat(c - 2) + BOX.mr);

  // Rows 4 .. rows-3 — side borders
  for (let i = 4; i <= r - 3; i++) {
    o += A.pos(i, 1) + Y(BOX.v) + A.pos(i, c) + Y(BOX.v);
  }

  // Row rows-2 — bottom divider
  o += A.pos(r - 2) + Y(BOX.ml + BOX.h.repeat(c - 2) + BOX.mr);

  // Row rows-1 — shortcuts
  const shortcuts =
    chalk.cyan("[↑↓]") + D(" move   ") +
    chalk.cyan("[Space]") + D(" toggle   ") +
    chalk.cyan("[Enter]") + D(" confirm   ") +
    chalk.cyan("[^C]") + D(" quit");
  const shortcutsPad = shortcuts + " ".repeat(Math.max(0, inner - 2 - stripAnsi(shortcuts).length));
  o += A.pos(r - 1, 1) + Y(BOX.v) + " " + shortcutsPad + Y(BOX.v);

  // Row rows — bottom border
  o += A.pos(r) + Y(BOX.bl + BOX.h.repeat(c - 2) + BOX.br);

  w(o);
}

function stripAnsi(s: string): string {
  return s.replace(/\x1B\[[0-9;]*[A-Za-z]/g, "");
}

// ─── Drawing helpers ─────────────────────────────────────────────

const SECTION_NAMES = ["You", "Provider", "Channels", "Features", "Review"];
const OWL = "🦉";

function drawHeader(current: number) {
  const c = cols();
  // Row 1 — logo
  w(p(1) + chalk.bold.white("  ◈ STACKOWL") + chalk.dim(" setup wizard"));

  // Row 2 — divider
  w(p(2) + chalk.dim("  " + "─".repeat(c - 4)));

  // Row 3 — progress
  const parts = SECTION_NAMES.map((n, i) => {
    if (i < current)   return chalk.green(`${n} ✓`);
    if (i === current) return chalk.cyan.bold(`${n} ●`);
    return chalk.dim(`${n} ○`);
  });
  w(p(3) + "  " + parts.join(chalk.dim("  ·  ")));

  // Row 4 — divider
  w(p(4) + chalk.dim("  " + "─".repeat(c - 4)));
}

function clearBody(fromRel = 5) {
  clearRange(fromRel);
}

function hintLine(msg: string) {
  if (_frameMode) {
    // In framed mode, update the shortcuts bar with the wizard hint
    const c     = cols();
    const inner = c - 2;
    const text  = "  " + chalk.dim(msg);
    const pad   = text + " ".repeat(Math.max(0, inner - 2 - stripAnsi(text).length));
    w(A.pos(hintRow(), 1) + Y(BOX.v) + pad + Y(BOX.v));
  } else {
    w(A.pos(hintRow(), 1) + chalk.dim("  " + msg));
  }
}

// ─── Input primitives ────────────────────────────────────────────

const KEY = {
  ENTER:   ["\r", "\n"],
  BS:      "\x7f",
  CTRL_C:  "\x03",
  UP:      `${ESC}[A`,
  DOWN:    `${ESC}[B`,
  SPACE:   " ",
  TAB:     "\t",
};

function ctrlCExit() {
  if (_frameMode) {
    process.exit(0);
  } else {
    w(A.altOut + A.show);
    process.exit(0);
  }
}

/** Single-line text input rendered at `row` (wizard-relative). */
async function inputText(opts: {
  row:     number;
  label:   string;
  hint?:   string;
  masked?: boolean;
  prefill?: string;
}): Promise<string> {
  let buf    = opts.prefill ?? "";
  let cursor = buf.length;
  const rowAbs = ar(opts.row);

  function render() {
    const display = opts.masked ? "•".repeat(buf.length) : buf;
    const caret   = display.slice(0, cursor) + chalk.bgWhite(" ") + display.slice(cursor);
    clearRange(opts.row, Math.min(rowAbs + 2, contentEnd()));
    w(A.pos(rowAbs,     1) + `  ${chalk.cyan("╔══")} ${chalk.bold(opts.label)}`);
    w(A.pos(rowAbs + 1, 1) + `  ${chalk.cyan("║")}  ${caret}`);
    w(A.pos(rowAbs + 2, 1) + `  ${chalk.cyan("╚══")} ${chalk.dim(opts.hint ?? "Enter to confirm")}`);
  }

  render();

  while (true) {
    let key = await waitForKey();

    // Strip bracketed-paste wrappers (ESC[200~ ... ESC[201~) sent by modern terminals.
    // Without this, pasted text that arrives wrapped in these markers is dropped because
    // the chunk starts with ESC and no branch below matches the full sequence.
    key = key.replace(/\x1B\[200~/g, "").replace(/\x1B\[201~/g, "");
    if (!key) { render(); continue; }

    if (key === KEY.CTRL_C) ctrlCExit();
    if (KEY.ENTER.includes(key) && buf.trim()) return buf.trim();
    if (key === KEY.BS && cursor > 0) {
      buf = buf.slice(0, cursor - 1) + buf.slice(cursor);
      cursor--;
    } else if (key === `${ESC}[D` && cursor > 0) {
      cursor--;
    } else if (key === `${ESC}[C` && cursor < buf.length) {
      cursor++;
    } else if (!key.startsWith(ESC)) {
      // Handles both single keypresses and multi-character paste events.
      // For single chars: filter keeps only printable chars (>= " ").
      // For paste chunks: strips residual control characters, inserts everything printable.
      const printable = [...key].filter(c => c >= " ").join("");
      if (printable) {
        buf = buf.slice(0, cursor) + printable + buf.slice(cursor);
        cursor += printable.length;
      }
    }
    render();
  }
}

/** Vertical list selector (wizard-relative row). */
async function selectOne(opts: {
  row:     number;
  title:   string;
  items:   string[];
  current?: number;
}): Promise<number> {
  let sel = opts.current ?? 0;
  const rowAbs = ar(opts.row);
  const maxEnd = contentEnd();

  function render() {
    clearRange(opts.row, Math.min(rowAbs + opts.items.length + 1, maxEnd));
    w(A.pos(rowAbs, 1) + `  ${chalk.bold(opts.title)}\n`);
    for (let i = 0; i < opts.items.length; i++) {
      const active = i === sel;
      const prefix = active ? chalk.cyan("  ▶ ") : "    ";
      const label  = active ? chalk.white.bold(opts.items[i]) : chalk.dim(opts.items[i]);
      const rowI   = rowAbs + 1 + i;
      if (rowI <= maxEnd) w(A.pos(rowI, 1) + prefix + label);
    }
    hintLine("↑↓ move  Enter select  Ctrl+C quit");
  }

  render();

  while (true) {
    const key = await waitForKey();
    if (key === KEY.CTRL_C) ctrlCExit();
    if (KEY.ENTER.includes(key)) return sel;
    if (key === KEY.UP)   sel = (sel - 1 + opts.items.length) % opts.items.length;
    if (key === KEY.DOWN) sel = (sel + 1) % opts.items.length;
    render();
  }
}

/** Multi-select checkbox list (wizard-relative row). */
async function selectMany(opts: {
  row:      number;
  title:    string;
  items:    string[];
  checked?: boolean[];
  locked?:  boolean[];
}): Promise<boolean[]> {
  const checked = (opts.checked ?? opts.items.map(() => false)).slice();
  const locked  = opts.locked ?? opts.items.map(() => false);
  let   sel     = 0;
  const rowAbs  = ar(opts.row);
  const maxEnd  = contentEnd();

  function render() {
    clearRange(opts.row, Math.min(rowAbs + opts.items.length + 1, maxEnd));
    w(A.pos(rowAbs, 1) + `  ${chalk.bold(opts.title)}\n`);
    for (let i = 0; i < opts.items.length; i++) {
      const active  = i === sel;
      const tick    = checked[i] ? chalk.green("✓") : chalk.dim("○");
      const lck     = locked[i]  ? chalk.dim(" [always on]") : "";
      const prefix  = active ? chalk.cyan("  ▶ ") : "    ";
      const label   = active
        ? chalk.white.bold(`[${tick}] ${opts.items[i]}`) + lck
        : chalk.dim(`[${tick}] ${opts.items[i]}`) + lck;
      const rowI    = rowAbs + 1 + i;
      if (rowI <= maxEnd) w(A.pos(rowI, 1) + prefix + label);
    }
    hintLine("↑↓ move  Space toggle  Enter confirm  Ctrl+C quit");
  }

  render();

  while (true) {
    const key = await waitForKey();
    if (key === KEY.CTRL_C) ctrlCExit();
    if (KEY.ENTER.includes(key)) return checked;
    if (key === KEY.UP)    sel = (sel - 1 + opts.items.length) % opts.items.length;
    if (key === KEY.DOWN)  sel = (sel + 1) % opts.items.length;
    if (key === KEY.SPACE && !locked[sel]) checked[sel] = !checked[sel];
    render();
  }
}

/** Typed confirm — shows y/n (wizard-relative row). */
async function confirm(opts: { row: number; question: string }): Promise<boolean> {
  const rowAbs = ar(opts.row);
  function render(ans: string) {
    clearRange(opts.row, rowAbs);
    w(A.pos(rowAbs, 1) + `  ${chalk.bold(opts.question)} ${chalk.dim("[y/n]")} ${ans}`);
    hintLine("y yes  n no  Ctrl+C quit");
  }

  render("");

  while (true) {
    const key = await waitForKey();
    if (key === KEY.CTRL_C) ctrlCExit();
    if (key.toLowerCase() === "y") { render(chalk.green("y")); return true; }
    if (key.toLowerCase() === "n") { render(chalk.red("n")); return false; }
  }
}

// ─── Ollama / LM Studio local detection ────────────────────────

async function detectLocal(baseUrl: string): Promise<string[]> {
  try {
    const resp = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(2000) });
    if (!resp.ok) return [];
    const data = await resp.json() as { models?: Array<{ name: string }> };
    return (data.models ?? []).map((m) => m.name).filter(Boolean);
  } catch {
    return [];
  }
}

async function detectLMStudio(baseUrl: string): Promise<string[]> {
  try {
    const resp = await fetch(`${baseUrl}/v1/models`, { signal: AbortSignal.timeout(2000) });
    if (!resp.ok) return [];
    const data = await resp.json() as { data?: Array<{ id: string }> };
    return (data.data ?? []).map((m) => m.id).filter(Boolean);
  } catch {
    return [];
  }
}

// ─── Config shape ────────────────────────────────────────────────

interface ProviderEntry {
  baseUrl:      string;
  apiKey:       string;
  defaultModel: string;
  type?:        string;
}

interface OnboardingResult {
  userName:         string;
  workType:         string;
  commStyle:        string;
  provider:         string;
  providerEntry:    ProviderEntry;
  enableWeb:        boolean;
  webPort:          number;
  enableTelegram:   boolean;
  telegramToken:    string;
  enableSlack:      boolean;
  slackBotToken:    string;
  slackAppToken:    string;
  enableMemory:     boolean;
  enableProactive:  boolean;
  enableSessionDebrief: boolean;
  enableVoice:      boolean;
  enableWebFace:    boolean;
}

// ─── Section A — You ─────────────────────────────────────────────

async function sectionYou(current: Partial<OnboardingResult>): Promise<Pick<OnboardingResult, "userName" | "workType" | "commStyle">> {
  drawHeader(0);
  clearBody();

  w(p(6) + `  ${OWL} ${chalk.bold("What should I call you?")}\n`);
  const userName = await inputText({
    row:     8,
    label:   "Your name",
    hint:    "How I'll address you in conversation",
    prefill: current.userName,
  });

  clearBody();
  w(p(6) + `  ${OWL} ${chalk.bold("What kind of work do you do?")}\n`);
  const workIdx = await selectOne({
    row:   8,
    title: "Work type",
    items: [
      "Software engineer / developer",
      "Product / design",
      "Data science / ML",
      "DevOps / infrastructure",
      "Research / writing",
      "Business / management",
      "Student",
      "Other",
    ],
    current: 0,
  });

  const WORK_LABELS = [
    "Software engineer", "Product/design", "Data scientist",
    "DevOps engineer", "Researcher", "Business", "Student", "Other",
  ];
  const workType = WORK_LABELS[workIdx];

  clearBody();
  w(p(6) + `  ${OWL} ${chalk.bold("How should I communicate with you?")}\n`);
  const styleIdx = await selectOne({
    row:   8,
    title: "Communication style",
    items: [
      "Concise — short answers, no fluff",
      "Balanced — helpful explanations when needed",
      "Detailed — thorough with context and examples",
      "Socratic — challenge me, ask questions back",
    ],
    current: 1,
  });
  const STYLE_LABELS = ["concise", "balanced", "detailed", "socratic"];
  const commStyle = STYLE_LABELS[styleIdx];

  return { userName, workType, commStyle };
}

// ─── Provider display names ───────────────────────────────────────

const PROVIDER_DISPLAY: Record<string, string> = {
  anthropic: "Anthropic (Claude)",
  openai:    "OpenAI",
  ollama:    "Ollama",
  lmstudio:  "LM Studio",
  grok:      "Grok (xAI)",
  gemini:    "Gemini (Google)",
  minimax:   "MiniMax",
};

const LOCAL_PROVIDERS = new Set(["ollama", "lmstudio"]);

// ─── Section B — Provider ────────────────────────────────────────

async function sectionProvider(current: Partial<OnboardingResult>): Promise<Pick<OnboardingResult, "provider" | "providerEntry">> {
  // Load provider + model definitions from src/models/* files
  const defs = new ModelLoader().getAll();
  const isLocal = (name: string) => LOCAL_PROVIDERS.has(name);

  drawHeader(1);
  clearBody();

  w(p(6) + `  ${OWL} ${chalk.bold("Which AI provider do you want to use?")}\n`);

  const provItems = [
    ...defs.map(d => {
      const label   = PROVIDER_DISPLAY[d.name] ?? d.name;
      const suffix  = isLocal(d.name) ? " — local, free" : " — cloud, API key required";
      return label + chalk.dim(suffix);
    }),
    chalk.dim("Other (OpenAI-compatible — custom URL)"),
  ];

  const provIdx = await selectOne({ row: 8, title: "Provider", items: provItems, current: 0 });

  const isOther  = provIdx === defs.length;
  const def      = isOther ? null : defs[provIdx];
  const provider = isOther ? "openai-compatible" : def!.name;

  let entry: ProviderEntry;

  clearBody();
  drawHeader(1);

  if (isOther) {
    // ── Other: custom OpenAI-compatible endpoint ─────────────────
    w(p(6) + `  ${chalk.bold("OpenAI-compatible")} — ${chalk.dim("any OpenAI-format API")}\n`);
    const baseUrl = await inputText({
      row: 8, label: "Base URL", hint: "e.g. http://localhost:8080/v1",
      prefill: current.providerEntry?.baseUrl ?? "",
    });
    clearBody(12);
    const defaultModel = await inputText({
      row: 12, label: "Model name", hint: "e.g. llama3, mistral-7b",
      prefill: current.providerEntry?.defaultModel ?? "",
    });
    clearBody(16);
    const apiKey = await inputText({
      row: 16, label: "API Key", hint: "Leave blank if no auth", masked: true,
      prefill: current.providerEntry?.apiKey ?? "",
    });
    entry = { baseUrl, apiKey, defaultModel, type: "openai-compatible" };

  } else if (provider === "ollama") {
    // ── Ollama: live detection → model → optional URL/key ────────
    w(p(6) + chalk.dim("  Checking localhost:11434…"));
    const detectedModels = await detectLocal("http://localhost:11434");

    clearBody();
    drawHeader(1);
    w(p(6) + `  ${chalk.bold("Ollama")}\n`);

    const useLocal = await selectOne({
      row: 8, title: "Where is Ollama running?",
      items: [
        detectedModels.length > 0
          ? `Local (localhost:11434) — ${detectedModels.length} model(s) found`
          : "Local (localhost:11434) — not detected, will configure",
        "Remote (custom URL)",
      ],
      current: 0,
    });

    let baseUrl = def!.url;
    let apiKey  = "";

    if (useLocal === 1) {
      clearBody(8);
      baseUrl = await inputText({
        row: 8, label: "Ollama URL", hint: "e.g. http://my-server:11434",
        prefill: current.providerEntry?.baseUrl ?? "",
      });
      clearBody(12);
      apiKey = await inputText({
        row: 12, label: "API Key (optional)", hint: "Leave blank if no auth",
        prefill: "",
      });
    }

    const availModels = useLocal === 0 ? detectedModels : await detectLocal(baseUrl);
    clearBody(8);
    let defaultModel = def!.defaultModel;

    if (availModels.length > 0) {
      const mIdx = await selectOne({ row: 8, title: "Model", items: availModels, current: 0 });
      defaultModel = availModels[mIdx];
    } else {
      w(p(8) + chalk.dim("  No models found. You can pull models later with: ollama pull <model>"));
      defaultModel = await inputText({
        row: 10, label: "Model name", hint: `e.g. ${def!.defaultModel}`,
        prefill: def!.defaultModel,
      });
    }
    entry = { baseUrl, apiKey, defaultModel, type: def!.compatible };

  } else if (provider === "lmstudio") {
    // ── LM Studio: live detection → model ────────────────────────
    w(p(6) + chalk.dim("  Checking localhost:1234…"));
    const detectedModels = await detectLMStudio("http://localhost:1234");

    clearBody();
    drawHeader(1);
    w(p(6) + `  ${chalk.bold("LM Studio")} — ${chalk.dim("lmstudio.ai")}\n`);

    let defaultModel = def!.defaultModel;

    if (detectedModels.length > 0) {
      w(p(8) + chalk.green(`  ✓ Detected ${detectedModels.length} model(s) on localhost:1234\n`));
      const mIdx = await selectOne({ row: 10, title: "Model", items: detectedModels, current: 0 });
      defaultModel = detectedModels[mIdx];
    } else {
      w(p(8) + chalk.yellow("  ⚠  LM Studio not detected on localhost:1234") +
        "\n" + p(9) + chalk.dim("  Make sure LM Studio is running with the server enabled."));
      await new Promise<void>(r => setTimeout(r, 1200));
      clearBody(8);
      defaultModel = await inputText({
        row: 8, label: "Model name", hint: `e.g. ${def!.defaultModel}`,
        prefill: "",
      });
    }
    entry = { baseUrl: def!.url, apiKey: "", defaultModel, type: def!.compatible };

  } else {
    // ── Cloud providers: model first, then API key ────────────────
    const label = PROVIDER_DISPLAY[provider] ?? provider;
    w(p(6) + `  ${chalk.bold(label)}\n`);

    const models = def!.availableModels;
    let defaultModel = def!.defaultModel;

    if (models.length > 1) {
      const mIdx = await selectOne({ row: 8, title: "Model", items: models, current: 0 });
      defaultModel = models[mIdx];
      clearBody(8);
    }

    const apiKey = await inputText({
      row: 8, label: "API Key", hint: "Your provider API key", masked: true,
      prefill: current.providerEntry?.apiKey ?? "",
    });
    entry = { baseUrl: def!.url, apiKey, defaultModel, type: def!.compatible };
  }

  return { provider, providerEntry: entry };
}

// ─── Section C — Channels ────────────────────────────────────────

async function sectionChannels(current: Partial<OnboardingResult>): Promise<
  Pick<OnboardingResult, "enableWeb" | "webPort" | "enableTelegram" | "telegramToken" | "enableSlack" | "slackBotToken" | "slackAppToken">
> {
  drawHeader(2);
  clearBody();

  w(p(6) + `  ${OWL} ${chalk.bold("Which channels do you want to enable?")}\n` +
    p(7) + chalk.dim("     CLI is always active."));

  const items   = ["Web UI (browser interface)", "Telegram bot", "Slack bot"];
  const checked = [
    current.enableWeb      ?? false,
    current.enableTelegram ?? false,
    current.enableSlack    ?? false,
  ];

  const selected = await selectMany({
    row: 9, title: "Optional channels", items, checked,
  });

  const [enableWeb, enableTelegram, enableSlack] = selected;

  let webPort       = current.webPort ?? 3000;
  let telegramToken = current.telegramToken  ?? "";
  let slackBotToken = current.slackBotToken  ?? "";
  let slackAppToken = current.slackAppToken  ?? "";

  if (enableWeb) {
    clearBody();
    drawHeader(2);
    w(p(6) + `  ${chalk.bold("Web UI")}\n`);
    const portStr = await inputText({
      row: 8, label: "Port", hint: "Default: 3000",
      prefill: String(webPort),
    });
    webPort = parseInt(portStr, 10) || 3000;
  }

  if (enableTelegram) {
    clearBody();
    drawHeader(2);
    w(p(6) + `  ${chalk.bold("Telegram Bot")}\n` +
      p(7) + chalk.dim("  Create a bot via @BotFather on Telegram to get your token."));
    telegramToken = await inputText({
      row: 9, label: "Bot Token", hint: "123456789:AAF...", masked: true,
      prefill: telegramToken,
    });
  }

  if (enableSlack) {
    clearBody();
    drawHeader(2);
    w(p(6) + `  ${chalk.bold("Slack Bot")}\n` +
      p(7) + chalk.dim("  Create a Slack app at api.slack.com/apps to get your tokens."));
    slackBotToken = await inputText({
      row: 9, label: "Bot Token (xoxb-...)", hint: "xoxb-...", masked: true,
      prefill: slackBotToken,
    });
    clearBody(13);
    slackAppToken = await inputText({
      row: 13, label: "App Token (xapp-...)", hint: "xapp-...", masked: true,
      prefill: slackAppToken,
    });
  }

  return { enableWeb, webPort, enableTelegram, telegramToken, enableSlack, slackBotToken, slackAppToken };
}

// ─── Section D — Features ────────────────────────────────────────

async function sectionFeatures(current: Partial<OnboardingResult>): Promise<
  Pick<OnboardingResult, "enableMemory" | "enableProactive" | "enableSessionDebrief" | "enableVoice" | "enableWebFace">
> {
  drawHeader(3);
  clearBody();

  w(p(6) + `  ${OWL} ${chalk.bold("Which features do you want to enable?")}\n`);

  const items   = [
    "Persistent memory (facts, episodes, preferences)",
    "Proactive messages (owl reaches out to you)",
    "Session debrief (summary after each session)",
    "Voice mode (microphone input via Whisper)",
    "Web face (animated owl in browser)",
  ];
  const checked = [
    current.enableMemory         ?? true,
    current.enableProactive      ?? false,
    current.enableSessionDebrief ?? true,
    current.enableVoice          ?? false,
    current.enableWebFace        ?? false,
  ];

  const selected = await selectMany({
    row: 8, title: "Features", items, checked,
  });

  return {
    enableMemory:         selected[0],
    enableProactive:      selected[1],
    enableSessionDebrief: selected[2],
    enableVoice:          selected[3],
    enableWebFace:        selected[4],
  };
}

// ─── Section E — Review ──────────────────────────────────────────

async function sectionReview(result: OnboardingResult): Promise<boolean> {
  drawHeader(4);
  clearBody();

  let row = 6;
  function line(label: string, value: string) {
    w(p(row++) + `  ${chalk.dim(label.padEnd(24))}${chalk.white(value)}`);
  }
  function sep() { row++; }

  w(p(row++) + `  ${chalk.bold("Review your setup")}`);
  sep();
  line("Name",      result.userName);
  line("Work type", result.workType);
  line("Style",     result.commStyle);
  sep();
  line("Provider",  result.provider);
  line("Model",     result.providerEntry.defaultModel);
  line("API Key",   result.providerEntry.apiKey
    ? "•".repeat(Math.min(result.providerEntry.apiKey.length, 12))
    : chalk.dim("(none)"));
  sep();
  const channels = ["CLI (always)"];
  if (result.enableWeb)      channels.push(`Web :${result.webPort}`);
  if (result.enableTelegram) channels.push("Telegram");
  if (result.enableSlack)    channels.push("Slack");
  line("Channels", channels.join(", "));
  sep();
  const features: string[] = [];
  if (result.enableMemory)         features.push("Memory");
  if (result.enableProactive)      features.push("Proactive");
  if (result.enableSessionDebrief) features.push("Debrief");
  if (result.enableVoice)          features.push("Voice");
  if (result.enableWebFace)        features.push("Web Face");
  line("Features", features.length ? features.join(", ") : chalk.dim("(none)"));

  sep(); sep();
  return confirm({ row, question: "Save this configuration and start?" });
}

// ─── Config builder ──────────────────────────────────────────────

function buildConfig(r: OnboardingResult): Record<string, unknown> {
  const providers: Record<string, unknown> = {
    [r.provider]: {
      baseUrl:      r.providerEntry.baseUrl,
      apiKey:       r.providerEntry.apiKey,
      defaultModel: r.providerEntry.defaultModel,
      type:         r.providerEntry.type,
    },
  };

  const cfg: Record<string, unknown> = {
    defaultProvider: r.provider,
    defaultModel:    r.providerEntry.defaultModel,
    workspace:       "./workspace",
    providers,
    gateway: { port: 3099, host: "localhost" },
    parliament: { maxRounds: 3, maxOwls: 4 },
    heartbeat: { enabled: r.enableProactive, intervalMinutes: 60 },
    owlDna: { enabled: true, evolutionBatchSize: 5, decayRatePerWeek: 0.02 },
    user: { name: r.userName, type: r.workType, style: r.commStyle },
    skills: { enabled: true, directories: ["./workspace/skills"], watch: false },
    memory:    { enabled: r.enableMemory },
    cognition: { sessionDebrief: r.enableSessionDebrief },
  };

  if (r.enableWeb)      (cfg as any).web      = { enabled: true, port: r.webPort };
  if (r.enableTelegram && r.telegramToken)
    (cfg as any).telegram = { botToken: r.telegramToken };
  if (r.enableSlack && r.slackBotToken)
    (cfg as any).slack = { botToken: r.slackBotToken, appToken: r.slackAppToken };
  if (r.enableVoice)    (cfg as any).voice    = { enabled: true };
  if (r.enableWebFace)  (cfg as any).face     = { enabled: true };

  return cfg;
}

// ─── Main export ─────────────────────────────────────────────────

export class OnboardingWizard {
  constructor(private readonly configPath: string) {}

  /**
   * @param prefill     Pre-fill values from current config when re-running.
   * @param inAltScreen When true, we're already inside the TerminalUI alt screen.
   *                    The wizard draws its content WITHIN the same yellow-bordered
   *                    frame so the user never sees a layout change.
   */
  async run(prefill: Partial<OnboardingResult> = {}, inAltScreen = false): Promise<boolean> {
    // Set frame state for all helpers
    _frameMode   = inAltScreen;
    _frameOffset = inAltScreen ? 3 : 0;

    if (!inAltScreen) enterRaw();

    if (inAltScreen) {
      // Clear content area only, then draw the outer chrome
      const c = cols();
      let o = "";
      for (let r = 1; r <= rows(); r++) o += A.pos(r, 1) + " ".repeat(c);
      w(o + A.hide);
      drawFrame();
    } else {
      w(A.altIn + A.hide + A.clear);
    }

    try {
      // Welcome screen
      clearBody();
      w(p(1) + chalk.bold.white("  ◈ STACKOWL — Welcome!"));
      w(p(2) + chalk.dim("  " + "─".repeat(cols() - 4)));
      w(p(4) + `  ${OWL} ${chalk.bold("Let's get you set up in a few steps.")}`);
      w(p(6) + chalk.dim("  This wizard will configure your AI provider, channels,"));
      w(p(7) + chalk.dim("  and features. You can change everything later via /onboarding."));
      w(p(9) + chalk.dim("  ↑↓ navigate  Space toggle  Enter confirm  Ctrl+C quit"));
      hintLine("Press any key to continue…");
      await waitForKey();

      // Run sections
      const you      = await sectionYou(prefill);
      const prov     = await sectionProvider(prefill);
      const channels = await sectionChannels(prefill);
      const features = await sectionFeatures(prefill);

      const result: OnboardingResult = { ...you, ...prov, ...channels, ...features };

      const confirmed = await sectionReview(result);
      if (!confirmed) {
        if (!inAltScreen) { w(A.altOut + A.show); exitRaw(); }
        else              { w(A.show); }
        return false;
      }

      // Write config
      const cfg = buildConfig(result);
      const configDir = dirname(this.configPath);
      await mkdir(configDir, { recursive: true });

      // Wipe workspace so all generated memory, sessions, and caches are reset
      const workspacePath = resolve(configDir, "./workspace");
      await rm(workspacePath, { recursive: true, force: true });

      await writeFile(this.configPath, JSON.stringify(cfg, null, 2), "utf8");

      // Success
      clearBody();
      w(p(4) + chalk.green("  ✓ Configuration saved!"));
      w(p(6) + chalk.dim("  Restart StackOwl to apply the new configuration."));
      await new Promise<void>(r => setTimeout(r, 1500));

    } finally {
      // Reset frame state
      _frameMode   = false;
      _frameOffset = 0;
      if (!inAltScreen) { w(A.altOut + A.show); exitRaw(); }
      else              { w(A.show); }
    }

    return true;
  }
}
