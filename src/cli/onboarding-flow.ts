/**
 * StackOwl — In-Panel Onboarding Flow
 *
 * Runs entirely inside the existing TerminalRenderer right panel — no screen
 * switching, no raw-mode changes, no direct stdout writes.
 *
 * Each step:
 *   1. Renders its prompt via ui.printLines()
 *   2. Waits for the user to type in the normal input box and press Enter
 *   3. Validates input → advances or retries
 *
 * Multi-select steps (channels, features) accept space-separated numbers
 * or an empty Enter for defaults.
 * Text steps accept any non-empty text (Enter with value).
 * Masked steps (API keys) show • in the input box and panel echo.
 */

import { writeFile, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import chalk          from "chalk";
import type { TerminalRenderer } from "./renderer.js";

// ─── Style helpers (matches commands.ts) ─────────────────────────

const YB = chalk.yellow.bold;
const D  = chalk.dim;
const W  = chalk.white;
const C  = chalk.cyan;
const G  = chalk.green;
const R  = chalk.red;

function sep40() { return D("─".repeat(40)); }

function progressBar(section: number): string {
  const names = ["You", "Provider", "Channels", "Features", "Review"];
  return names.map((n, i) =>
    i < section  ? G(`${n} ✓`) :
    i === section ? C.bold(`${n} ●`) :
    D(`${n} ○`),
  ).join(D("  ·  "));
}

function sectionHeader(title: string, section: number): string[] {
  return [
    "",
    YB(`── ${title} `).padEnd(36) + " " + progressBar(section),
    "",
  ];
}

// ─── Internal data ────────────────────────────────────────────────

interface WizardData {
  // Section A
  userName?: string;
  workIdx?:  number;
  styleIdx?: number;
  // Section B
  provider?:     string;   // "anthropic" | "openai" | "ollama" | "lmstudio" | "minimax" | "openai-compatible"
  provApiKey?:   string;
  provBaseUrl?:  string;
  provModel?:    string;
  provType?:     string;
  provGroupId?:  string;
  provDetectedModels?: string[];  // from Ollama / LM Studio
  // Section C
  channels?: boolean[];  // [web, telegram, slack]
  webPort?:  number;
  telegramToken?:      string;
  telegramAllowedIds?: number[];
  slackBotToken?: string;
  slackAppToken?: string;
  // Section D
  features?: boolean[];  // [memory, proactive, debrief, voice, webface]
}

type StepId =
  | "welcome"
  | "you_name" | "you_work" | "you_style"
  | "prov_choice"
  | "prov_ant_key"  | "prov_ant_model"
  | "prov_oai_key"  | "prov_oai_model"
  | "prov_ollama_url" | "prov_ollama_key" | "prov_ollama_model_sel" | "prov_ollama_model_txt"
  | "prov_lms_model_sel" | "prov_lms_model_txt"
  | "prov_mm_key"   | "prov_mm_model"
  | "prov_compat_url" | "prov_compat_key" | "prov_compat_model"
  | "chan_multi"
  | "chan_web_port" | "chan_tg_token" | "chan_tg_allowed" | "chan_slack_bot" | "chan_slack_app"
  | "feat_multi"
  | "review"
  | "done";

// ─── Ollama / LM Studio detection ────────────────────────────────

async function detectOllama(baseUrl: string): Promise<string[]> {
  try {
    const r = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(2000) });
    if (!r.ok) return [];
    const d = await r.json() as { models?: Array<{ name: string }> };
    return (d.models ?? []).map(m => m.name).filter(Boolean);
  } catch { return []; }
}

async function detectLMStudio(): Promise<string[]> {
  try {
    const r = await fetch("http://localhost:1234/v1/models", { signal: AbortSignal.timeout(2000) });
    if (!r.ok) return [];
    const d = await r.json() as { data?: Array<{ id: string }> };
    return (d.data ?? []).map(m => m.id).filter(Boolean);
  } catch { return []; }
}

// ─── Config builder ───────────────────────────────────────────────

function buildConfig(d: WizardData): Record<string, unknown> {
  const WORK_LABELS = [
    "Software engineer", "Product/design", "Data scientist",
    "DevOps engineer", "Researcher", "Business", "Student", "Other",
  ];
  const STYLE_LABELS = ["concise", "balanced", "detailed", "socratic"];

  const providerKey = d.provider!;
  const providers: Record<string, unknown> = {
    [providerKey]: {
      baseUrl:      d.provBaseUrl,
      apiKey:       d.provApiKey ?? "",
      defaultModel: d.provModel,
      type:         d.provType,
    },
  };

  const cfg: Record<string, unknown> = {
    defaultProvider: providerKey,
    defaultModel:    d.provModel,
    workspace:       "./workspace",
    providers,
    gateway: { port: 3099, host: "localhost" },
    parliament: { maxRounds: 3, maxOwls: 4 },
    heartbeat: { enabled: d.features?.[1] ?? false, intervalMinutes: 60 },
    owlDna: { enabled: true, evolutionBatchSize: 5, decayRatePerWeek: 0.02 },
    user: {
      name:  d.userName,
      type:  WORK_LABELS[d.workIdx ?? 0],
      style: STYLE_LABELS[d.styleIdx ?? 1],
    },
    skills:    { enabled: true, directories: ["./workspace/skills"], watch: false },
    memory:    { enabled: d.features?.[0] ?? true },
    cognition: { sessionDebrief: d.features?.[2] ?? true },
  };

  const ch = d.channels ?? [false, false, false];
  if (ch[0]) (cfg as any).web = { enabled: true, port: d.webPort ?? 3000 };
  if (ch[1] && d.telegramToken) {
    const tg: Record<string, unknown> = { botToken: d.telegramToken };
    if (d.telegramAllowedIds?.length) tg.allowedUserIds = d.telegramAllowedIds;
    (cfg as any).telegram = tg;
  }
  if (ch[2] && d.slackBotToken) (cfg as any).slack = { botToken: d.slackBotToken, appToken: d.slackAppToken ?? "" };
  if (d.features?.[3]) (cfg as any).voice   = { enabled: true };
  if (d.features?.[4]) (cfg as any).face    = { enabled: true };

  return cfg;
}

// ─── OnboardingFlow ──────────────────────────────────────────────

export class OnboardingFlow {
  private _step: StepId = "welcome";
  private _data: WizardData = {};

  constructor(private readonly configPath: string) {}

  // ── Public API ──────────────────────────────────────────────────

  /** Render the welcome prompt. Call once to start the flow. */
  start(ui: TerminalRenderer): void {
    ui.setAllowEmptyInput(true);
    this._step = "welcome";
    this._showStep(ui);
  }

  /**
   * Feed user input into the wizard.
   * Returns true when the wizard is complete (done or cancelled).
   */
  async handleInput(input: string, ui: TerminalRenderer): Promise<boolean> {
    if (input.toLowerCase() === "cancel" || input.toLowerCase() === "abort") {
      ui.setAllowEmptyInput(false);
      ui.printLines(["", R("Onboarding cancelled."), ""]);
      return true;
    }
    const done = await this._handle(input, ui);
    if (done) ui.setAllowEmptyInput(false);
    return done;
  }

  // ── Step renderer ───────────────────────────────────────────────

  private _showStep(ui: TerminalRenderer): void {
    const d = this._data;
    switch (this._step) {

      // ── Welcome ──────────────────────────────────────────────────
      case "welcome":
        ui.printLines([
          "",
          YB("Setup Wizard"),
          sep40(),
          "🦉  Configure your AI provider, channels, and features.",
          D("    Type \"cancel\" at any time to abort."),
          "",
          C("    Press Enter to begin"),
          "",
        ]);
        break;

      // ── Section A: You ───────────────────────────────────────────
      case "you_name":
        ui.printLines([
          ...sectionHeader("Section A — You", 0),
          "🦉  What should I call you?",
          "",
          C("    Type your name and press Enter:"),
          "",
        ]);
        break;

      case "you_work":
        ui.printLines([
          "",
          W("Work type"),
          sep40(),
          C("  1") + "  Software engineer / developer",
          C("  2") + "  Product / design",
          C("  3") + "  Data science / ML",
          C("  4") + "  DevOps / infrastructure",
          C("  5") + "  Research / writing",
          C("  6") + "  Business / management",
          C("  7") + "  Student",
          C("  8") + "  Other",
          "",
          C("    Type 1–8:"),
          "",
        ]);
        break;

      case "you_style":
        ui.printLines([
          "",
          W("Communication style"),
          sep40(),
          C("  1") + "  Concise — short and direct",
          C("  2") + "  Balanced — helpful when needed",
          C("  3") + "  Detailed — thorough with context",
          C("  4") + "  Socratic — challenge me back",
          "",
          C("    Type 1–4:"),
          "",
        ]);
        break;

      // ── Section B: Provider ──────────────────────────────────────
      case "prov_choice":
        ui.printLines([
          ...sectionHeader("Section B — Provider", 1),
          "🦉  Which AI provider do you want to use?",
          "",
          C("  1") + "  Anthropic (Claude)     " + D("cloud · API key required"),
          C("  2") + "  OpenAI (GPT-4)          " + D("cloud · API key required"),
          C("  3") + "  Ollama                  " + D("local or remote · free"),
          C("  4") + "  LM Studio               " + D("local · free"),
          C("  5") + "  MiniMax                 " + D("cloud · API key required"),
          C("  6") + "  OpenAI-compatible        " + D("custom base URL"),
          "",
          C("    Type 1–6:"),
          "",
        ]);
        break;

      case "prov_ant_key":
        ui.printLines([
          "",
          W("Anthropic API key"),
          D("    Stored in stackowl.config.json (gitignored)"),
          "",
          C("    Type your sk-ant-... key and press Enter:"),
          "",
        ]);
        ui.setMasked(true);
        break;

      case "prov_ant_model":
        ui.printLines([
          "",
          W("Model"),
          sep40(),
          C("  1") + "  claude-sonnet-4-6   " + D("(recommended)"),
          C("  2") + "  claude-opus-4-6     " + D("(most capable)"),
          C("  3") + "  claude-haiku-4-5    " + D("(fastest, cheapest)"),
          C("  4") + "  claude-3-5-sonnet-20241022",
          "",
          C("    Type 1–4:"),
          "",
        ]);
        break;

      case "prov_oai_key":
        ui.printLines([
          "",
          W("OpenAI API key"),
          D("    Stored in stackowl.config.json (gitignored)"),
          "",
          C("    Type your sk-... key and press Enter:"),
          "",
        ]);
        ui.setMasked(true);
        break;

      case "prov_oai_model":
        ui.printLines([
          "",
          W("Model"),
          sep40(),
          C("  1") + "  gpt-4o         " + D("(recommended)"),
          C("  2") + "  gpt-4o-mini    " + D("(fast, cheap)"),
          C("  3") + "  gpt-4-turbo",
          C("  4") + "  o1-preview",
          "",
          C("    Type 1–4:"),
          "",
        ]);
        break;

      case "prov_ollama_url":
        ui.printLines([
          "",
          W("Ollama base URL"),
          D("    Default is http://localhost:11434"),
          "",
          C("    Type URL and press Enter (or Enter for default):"),
          "",
        ]);
        break;

      case "prov_ollama_key":
        ui.printLines([
          "",
          W("Ollama API key") + D("  (optional — leave blank if no auth)"),
          "",
          C("    Type key and press Enter, or press Enter to skip:"),
          "",
        ]);
        break;

      case "prov_ollama_model_sel": {
        const models = d.provDetectedModels ?? [];
        const lines: string[] = ["", W("Model") + D(`  (${models.length} detected)`), sep40()];
        models.slice(0, 12).forEach((m, i) => lines.push(C(`  ${i + 1}`) + `  ${m}`));
        lines.push("", C("    Type 1–" + Math.min(models.length, 12) + ":"), "");
        ui.printLines(lines);
        break;
      }

      case "prov_ollama_model_txt":
        ui.printLines([
          "",
          W("Model name"),
          D("    No models detected. Enter a model name to use (e.g. llama3.2):"),
          "",
          C("    Type model name and press Enter:"),
          "",
        ]);
        break;

      case "prov_lms_model_sel": {
        const models = d.provDetectedModels ?? [];
        const lines: string[] = ["", W("LM Studio model") + D(`  (${models.length} detected)`), sep40()];
        models.slice(0, 12).forEach((m, i) => lines.push(C(`  ${i + 1}`) + `  ${m}`));
        lines.push("", C("    Type 1–" + Math.min(models.length, 12) + ":"), "");
        ui.printLines(lines);
        break;
      }

      case "prov_lms_model_txt":
        ui.printLines([
          "",
          W("LM Studio model"),
          D("    LM Studio not detected on localhost:1234. Make sure it's running with the server enabled."),
          "",
          C("    Type model name and press Enter (e.g. meta-llama-3-8b-instruct):"),
          "",
        ]);
        break;

      case "prov_mm_key":
        ui.printLines([
          "",
          W("MiniMax API key"),
          D("    Get yours at platform.minimaxi.com → API Keys"),
          "",
          C("    Type your sk-... key and press Enter:"),
          "",
        ]);
        ui.setMasked(true);
        break;

      case "prov_mm_model":
        ui.printLines([
          "",
          W("Model"),
          sep40(),
          C("  1") + "  MiniMax-M2.7  " + D("(recommended)"),
          C("  2") + "  MiniMax-Text-01",
          C("  3") + "  abab6.5g-chat",
          "",
          C("    Type 1–3, or type a custom model name:"),
          "",
        ]);
        break;

      case "prov_compat_url":
        ui.printLines([
          "",
          W("Base URL"),
          D("    e.g. http://localhost:8080/v1"),
          "",
          C("    Type URL and press Enter:"),
          "",
        ]);
        break;

      case "prov_compat_key":
        ui.printLines([
          "",
          W("API key") + D("  (optional — leave blank if no auth)"),
          "",
          C("    Type key and press Enter, or press Enter to skip:"),
          "",
        ]);
        break;

      case "prov_compat_model":
        ui.printLines([
          "",
          W("Model name"),
          D("    e.g. llama3, mistral-7b"),
          "",
          C("    Type model name and press Enter:"),
          "",
        ]);
        break;

      // ── Section C: Channels ──────────────────────────────────────
      case "chan_multi":
        ui.printLines([
          ...sectionHeader("Section C — Channels", 2),
          "🦉  Which channels to enable?  " + D("(CLI is always active)"),
          "",
          C("  1") + "  Web UI (browser interface)",
          C("  2") + "  Telegram bot",
          C("  3") + "  Slack bot",
          "",
          C("    Type numbers to enable (e.g. \"1 2\"), or press Enter for none:"),
          "",
        ]);
        break;

      case "chan_web_port":
        ui.printLines([
          "",
          W("Web UI port") + D("  (default: 3000)"),
          "",
          C("    Type port number and press Enter (or Enter for 3000):"),
          "",
        ]);
        break;

      case "chan_tg_token":
        ui.printLines([
          "",
          W("Telegram bot token"),
          D("    Create a bot via @BotFather on Telegram to get your token."),
          "",
          C("    Type your 123456789:AAF... token and press Enter:"),
          "",
        ]);
        ui.setMasked(true);
        break;

      case "chan_tg_allowed":
        ui.printLines([
          "",
          W("Telegram — allowed user IDs"),
          D("    Restrict who can talk to your bot."),
          D("    Find your ID by messaging @userinfobot on Telegram."),
          D("    Leave blank to allow everyone (not recommended for personal bots)."),
          "",
          C("    Type ID(s) separated by spaces (e.g. 123456789 987654321), or Enter to skip:"),
          "",
        ]);
        break;

      case "chan_slack_bot":
        ui.printLines([
          "",
          W("Slack bot token  ") + D("(xoxb-...)"),
          D("    Create a Slack app at api.slack.com/apps"),
          "",
          C("    Type your xoxb-... token and press Enter:"),
          "",
        ]);
        ui.setMasked(true);
        break;

      case "chan_slack_app":
        ui.printLines([
          "",
          W("Slack app token  ") + D("(xapp-...)"),
          "",
          C("    Type your xapp-... token and press Enter:"),
          "",
        ]);
        ui.setMasked(true);
        break;

      // ── Section D: Features ──────────────────────────────────────
      case "feat_multi":
        ui.printLines([
          ...sectionHeader("Section D — Features", 3),
          "🦉  Which features to enable?",
          "",
          C("  1") + "  Persistent memory          " + D("(recommended)"),
          C("  2") + "  Proactive messages",
          C("  3") + "  Session debrief             " + D("(recommended)"),
          C("  4") + "  Voice mode (Whisper STT)",
          C("  5") + "  Web face (browser)",
          "",
          C("    Type numbers to enable (e.g. \"1 3\"), or press Enter for defaults [1 3]:"),
          "",
        ]);
        break;

      // ── Section E: Review ────────────────────────────────────────
      case "review": {
        const WORK_LABELS = [
          "Software engineer", "Product/design", "Data scientist",
          "DevOps engineer", "Researcher", "Business", "Student", "Other",
        ];
        const STYLE_LABELS = ["concise", "balanced", "detailed", "socratic"];
        const ch = d.channels ?? [false, false, false];
        const ft = d.features ?? [true, false, true, false, false];

        const chList = ["CLI"];
        if (ch[0]) chList.push(`Web :${d.webPort ?? 3000}`);
        if (ch[1]) {
          const ids = d.telegramAllowedIds ?? [];
          chList.push(ids.length ? `Telegram (${ids.length} user${ids.length > 1 ? "s" : ""})` : "Telegram (open)");
        }
        if (ch[2]) chList.push("Slack");

        const ftList: string[] = [];
        const ftNames = ["Memory", "Proactive", "Debrief", "Voice", "Web Face"];
        ft.forEach((on, i) => { if (on) ftList.push(ftNames[i]); });

        const apiKeyMasked = d.provApiKey
          ? "•".repeat(Math.min(d.provApiKey.length, 20))
          : D("(none)");

        ui.printLines([
          ...sectionHeader("Section E — Review", 4),
          "🦉  Here's your configuration:",
          "",
          D("  Name         ") + W(d.userName ?? ""),
          D("  Work type    ") + W(WORK_LABELS[d.workIdx ?? 0]),
          D("  Style        ") + W(STYLE_LABELS[d.styleIdx ?? 1]),
          sep40(),
          D("  Provider     ") + W(d.provider ?? ""),
          D("  Model        ") + W(d.provModel ?? ""),
          D("  API Key      ") + W(apiKeyMasked),
          sep40(),
          D("  Channels     ") + W(chList.join(", ")),
          D("  Features     ") + W(ftList.length ? ftList.join(", ") : "none"),
          "",
          C("    Type \"yes\" to save  ·  \"no\" to cancel:"),
          "",
        ]);
        break;
      }
    }
  }

  // ── Step handler ────────────────────────────────────────────────

  private async _handle(input: string, ui: TerminalRenderer): Promise<boolean> {
    const d = this._data;

    switch (this._step) {

      case "welcome":
        this._step = "you_name";
        this._showStep(ui);
        return false;

      case "you_name":
        if (!input) { ui.printLines([R("  Name cannot be empty."), ""]); return false; }
        d.userName = input;
        this._step = "you_work";
        this._showStep(ui);
        return false;

      case "you_work": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 8) {
          ui.printLines([R("  Type a number 1–8."), ""]); return false;
        }
        d.workIdx = n - 1;
        this._step = "you_style";
        this._showStep(ui);
        return false;
      }

      case "you_style": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 4) {
          ui.printLines([R("  Type a number 1–4."), ""]); return false;
        }
        d.styleIdx = n - 1;
        this._step = "prov_choice";
        this._showStep(ui);
        return false;
      }

      case "prov_choice": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 6) {
          ui.printLines([R("  Type a number 1–6."), ""]); return false;
        }
        const map = ["anthropic", "openai", "ollama", "lmstudio", "minimax", "openai-compatible"];
        const typeMap = ["anthropic", "openai", "ollama", "openai-compatible", "minimax", "openai-compatible"];
        const urlMap  = [
          "https://api.anthropic.com",
          "https://api.openai.com/v1",
          "http://localhost:11434",
          "http://localhost:1234/v1",
          "https://api.minimax.io/anthropic",   // Anthropic-compatible endpoint
          "",
        ];
        d.provider    = map[n - 1];
        d.provType    = typeMap[n - 1];
        d.provBaseUrl = urlMap[n - 1];

        if (d.provider === "anthropic") { this._step = "prov_ant_key"; }
        else if (d.provider === "openai") { this._step = "prov_oai_key"; }
        else if (d.provider === "ollama") { this._step = "prov_ollama_url"; }
        else if (d.provider === "lmstudio") {
          // Auto-detect
          ui.printInfo("  Checking LM Studio on localhost:1234…");
          const models = await detectLMStudio();
          d.provDetectedModels = models;
          this._step = models.length > 0 ? "prov_lms_model_sel" : "prov_lms_model_txt";
        }
        else if (d.provider === "minimax") { this._step = "prov_mm_key"; }
        else { this._step = "prov_compat_url"; }

        this._showStep(ui);
        return false;
      }

      case "prov_ant_key":
        if (!input) { ui.printLines([R("  API key cannot be empty."), ""]); ui.setMasked(true); return false; }
        d.provApiKey = input;
        this._step   = "prov_ant_model";
        this._showStep(ui);
        return false;

      case "prov_ant_model": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 4) {
          ui.printLines([R("  Type a number 1–4."), ""]); return false;
        }
        const models = ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001", "claude-3-5-sonnet-20241022"];
        d.provModel = models[n - 1];
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;
      }

      case "prov_oai_key":
        if (!input) { ui.printLines([R("  API key cannot be empty."), ""]); ui.setMasked(true); return false; }
        d.provApiKey = input;
        this._step   = "prov_oai_model";
        this._showStep(ui);
        return false;

      case "prov_oai_model": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 4) {
          ui.printLines([R("  Type a number 1–4."), ""]); return false;
        }
        const models = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview"];
        d.provModel = models[n - 1];
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;
      }

      case "prov_ollama_url":
        d.provBaseUrl = input || "http://localhost:11434";
        // Try to detect models
        ui.printInfo(`  Checking ${d.provBaseUrl}…`);
        {
          const models = await detectOllama(d.provBaseUrl);
          d.provDetectedModels = models;
          this._step = models.length > 0 ? "prov_ollama_model_sel" : "prov_ollama_key";
        }
        this._showStep(ui);
        return false;

      case "prov_ollama_key":
        d.provApiKey = input || "";
        this._step   = "prov_ollama_model_txt";
        this._showStep(ui);
        return false;

      case "prov_ollama_model_sel": {
        const models = d.provDetectedModels ?? [];
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > models.length) {
          ui.printLines([R(`  Type a number 1–${models.length}.`), ""]); return false;
        }
        d.provModel = models[n - 1];
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;
      }

      case "prov_ollama_model_txt":
        if (!input) { ui.printLines([R("  Model name cannot be empty."), ""]); return false; }
        d.provModel = input;
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;

      case "prov_lms_model_sel": {
        const models = d.provDetectedModels ?? [];
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > models.length) {
          ui.printLines([R(`  Type a number 1–${models.length}.`), ""]); return false;
        }
        d.provModel = models[n - 1];
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;
      }

      case "prov_lms_model_txt":
        if (!input) { ui.printLines([R("  Model name cannot be empty."), ""]); return false; }
        d.provModel = input;
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;

      case "prov_mm_key":
        if (!input) { ui.printLines([R("  API key cannot be empty."), ""]); ui.setMasked(true); return false; }
        d.provApiKey = input;
        this._step   = "prov_mm_model";
        this._showStep(ui);
        return false;

      case "prov_mm_model": {
        const PRESET = ["MiniMax-M2.7", "MiniMax-Text-01", "abab6.5g-chat"];
        const n = parseInt(input, 10);
        d.provModel = (!isNaN(n) && n >= 1 && n <= 3) ? PRESET[n - 1] : (input || "MiniMax-M2.7");
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;
      }

      case "prov_compat_url":
        if (!input) { ui.printLines([R("  URL cannot be empty."), ""]); return false; }
        d.provBaseUrl = input;
        this._step    = "prov_compat_key";
        this._showStep(ui);
        return false;

      case "prov_compat_key":
        d.provApiKey = input || "";
        this._step   = "prov_compat_model";
        this._showStep(ui);
        return false;

      case "prov_compat_model":
        if (!input) { ui.printLines([R("  Model name cannot be empty."), ""]); return false; }
        d.provModel = input;
        this._step  = "chan_multi";
        this._showStep(ui);
        return false;

      // ── Channels multi-select ────────────────────────────────────
      case "chan_multi": {
        const channels = this._parseNumberList(input, 3);
        d.channels = channels;
        this._step = this._nextChannelStep(channels);
        this._showStep(ui);
        return false;
      }

      case "chan_web_port": {
        const port = parseInt(input || "3000", 10);
        d.webPort  = isNaN(port) ? 3000 : port;
        this._step = this._nextChannelStep(d.channels ?? [], "web");
        this._showStep(ui);
        return false;
      }

      case "chan_tg_token":
        if (!input) { ui.printLines([R("  Token cannot be empty."), ""]); ui.setMasked(true); return false; }
        d.telegramToken = input;
        this._step = "chan_tg_allowed";
        this._showStep(ui);
        return false;

      case "chan_tg_allowed": {
        const ids = input
          .split(/[\s,]+/)
          .map(s => parseInt(s.trim(), 10))
          .filter(n => Number.isFinite(n) && n > 0);
        d.telegramAllowedIds = ids;
        this._step = this._nextChannelStep(d.channels ?? [], "telegram");
        this._showStep(ui);
        return false;
      }

      case "chan_slack_bot":
        if (!input) { ui.printLines([R("  Token cannot be empty."), ""]); ui.setMasked(true); return false; }
        d.slackBotToken = input;
        this._step = "chan_slack_app";
        this._showStep(ui);
        return false;

      case "chan_slack_app":
        if (!input) { ui.printLines([R("  Token cannot be empty."), ""]); ui.setMasked(true); return false; }
        d.slackAppToken = input;
        this._step = "feat_multi";
        this._showStep(ui);
        return false;

      // ── Features multi-select ────────────────────────────────────
      case "feat_multi": {
        if (!input) {
          // Default: memory + debrief
          d.features = [true, false, true, false, false];
        } else {
          d.features = this._parseNumberList(input, 5);
        }
        this._step = "review";
        this._showStep(ui);
        return false;
      }

      // ── Review ───────────────────────────────────────────────────
      case "review": {
        const answer = input.toLowerCase();
        if (answer === "yes" || answer === "y") {
          const cfg = buildConfig(d);
          const workspacePath = resolve(dirname(this.configPath), "./workspace");
          await rm(workspacePath, { recursive: true, force: true });
          await writeFile(this.configPath, JSON.stringify(cfg, null, 2), "utf8");
          ui.printLines([
            "",
            G("  ✓ Configuration saved!"),
            D("  Restart StackOwl to apply the new settings."),
            "",
          ]);
          return true;  // done
        }
        if (answer === "no" || answer === "n") {
          ui.printLines(["", D("  Setup cancelled."), ""]);
          return true;  // done
        }
        ui.printLines([R("  Type \"yes\" to save or \"no\" to cancel."), ""]); return false;
      }
    }

    return false;
  }

  // ── Helpers ─────────────────────────────────────────────────────

  /** Parse space/comma-separated numbers into boolean flags array of length `max`. */
  private _parseNumberList(input: string, max: number): boolean[] {
    const result = new Array<boolean>(max).fill(false);
    if (!input.trim()) return result;
    const parts = input.split(/[\s,]+/);
    for (const p of parts) {
      const n = parseInt(p, 10);
      if (!isNaN(n) && n >= 1 && n <= max) result[n - 1] = true;
    }
    return result;
  }

  /** Determine next channel config step based on what's enabled and what's done. */
  private _nextChannelStep(
    channels: boolean[],
    doneUpTo?: "web" | "telegram",
  ): StepId {
    const [web, tg, slack] = channels;
    if (!doneUpTo && web)        return "chan_web_port";
    if (!doneUpTo && tg)         return "chan_tg_token";
    if (!doneUpTo && slack)      return "chan_slack_bot";
    if (doneUpTo === "web" && tg)    return "chan_tg_token";
    if (doneUpTo === "web" && slack) return "chan_slack_bot";
    if (doneUpTo === "telegram" && slack) return "chan_slack_bot";
    return "feat_multi";
  }
}
