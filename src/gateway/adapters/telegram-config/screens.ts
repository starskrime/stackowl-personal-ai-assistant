/**
 * StackOwl — Telegram Config Menu: Screen Renderers
 *
 * Pure functions that return { text: string, keyboard: InlineKeyboard }
 * for each menu screen. No side-effects, no async — just view building.
 *
 * Callback data prefix: "cfg:" (max 64 bytes per Telegram limit).
 * Abbreviations used to stay well within the 64-byte limit:
 *   pr  = providers list      pd  = provider detail
 *   pa  = provider add type   pu  = provider add url
 *   pk  = provider model pick  ky  = key input
 *   rl  = model roles          rp  = role provider pick
 *   rm  = role model pick      fb  = fallback chain
 *   fa  = fallback add prov    hc  = health check
 *   mp  = model pick (index)   pp  = provider pick (index)
 *   bc  = back                 cl  = close
 */

import { InlineKeyboard } from "grammy";
import type { StackOwlConfig } from "../../../config/loader.js";

// ─── Types ────────────────────────────────────────────────────────

export interface ScreenContent {
  text: string;
  keyboard: InlineKeyboard;
}

// ─── Static model catalog (Anthropic has no /models endpoint) ─────

export const ANTHROPIC_MODELS: Array<{ id: string; label: string }> = [
  { id: "claude-opus-4-5",            label: "Opus 4.5 🔮 Powerful" },
  { id: "claude-sonnet-4-6",          label: "Sonnet 4.6 💡 Balanced" },
  { id: "claude-sonnet-4-5-20241022", label: "Sonnet 4.5 💡 Previous" },
  { id: "claude-haiku-3-5",           label: "Haiku 3.5 ⚡ Fast" },
];

// ─── Provider type display metadata ───────────────────────────────

export const PROVIDER_TYPE_META: Record<string, { emoji: string; label: string }> = {
  ollama:          { emoji: "🦙", label: "Ollama" },
  "ollama-cloud":  { emoji: "☁️",  label: "Ollama Cloud" },
  lmstudio:        { emoji: "🧪", label: "LM Studio" },
  openai:          { emoji: "🤖", label: "OpenAI" },
  anthropic:       { emoji: "🔮", label: "Anthropic" },
  minimax:         { emoji: "🌊", label: "MiniMax" },
  "openai-compatible": { emoji: "⚙️", label: "OpenAI-Compat" },
};

function providerEmoji(key: string, config: StackOwlConfig): string {
  const entry = config.providers[key];
  // Infer type from baseUrl/apiKey heuristics
  if (entry?.apiKey?.startsWith("sk-ant-"))       return "🔮";
  if (entry?.baseUrl?.includes(":11434") || entry?.baseUrl?.includes("ollama")) return "🦙";
  if (entry?.baseUrl?.includes(":1234"))          return "🧪";
  if (entry?.apiKey?.startsWith("sk-"))           return "🤖";
  if (entry?.baseUrl?.includes("minimax"))        return "🌊";
  return PROVIDER_TYPE_META[key]?.emoji ?? "⚙️";
}

/** Mask an API key for display: show first 8 and last 4 chars */
function maskKey(key: string): string {
  if (!key || key.length < 14) return "••••••••";
  return `${key.slice(0, 8)}...${key.slice(-4)}`;
}

// ─── Status dot from health result ───────────────────────────────

export type HealthMap = Record<string, { ok: boolean; latencyMs?: number }>;

function statusDot(providerKey: string, health?: HealthMap): string {
  if (!health) return "⚪";
  const h = health[providerKey];
  if (!h) return "⚪";
  return h.ok ? "🟢" : "🔴";
}

// ─── Screen: Main Menu ────────────────────────────────────────────

export function renderMain(config: StackOwlConfig): ScreenContent {
  const provCount = Object.keys(config.providers).length;
  const defaultModel = config.defaultModel ?? "—";
  const defaultProv  = config.defaultProvider ?? "—";

  const text =
    `⚙️ <b>StackOwl Configuration</b>\n\n` +
    `Active: <code>${defaultProv}</code> · <code>${defaultModel}</code>\n` +
    `Providers configured: ${provCount}`;

  const keyboard = new InlineKeyboard()
    .text("📡 Providers",      "cfg:pr").text("🎯 Model Roles",   "cfg:rl").row()
    .text("🔗 Fallback Chain", "cfg:fb").text("🏥 Health Check",  "cfg:hc").row()
    .text("❌ Close",          "cfg:cl");

  return { text, keyboard };
}

// ─── Screen: Provider List ────────────────────────────────────────

export function renderProviders(
  config: StackOwlConfig,
  health?: HealthMap,
): ScreenContent {
  const entries = Object.entries(config.providers);
  const lines = entries.map(([key, entry]) => {
    const dot     = statusDot(key, health);
    const emoji   = providerEmoji(key, config);
    const isDefault = key === config.defaultProvider ? " ★" : "";
    const model   = entry.defaultModel ?? "—";
    return `${dot} ${emoji} <b>${key}</b>${isDefault}  <code>${model}</code>`;
  });

  const text =
    `📡 <b>Providers</b> (${entries.length} configured)\n\n` +
    (lines.length > 0 ? lines.join("\n") : "<i>No providers configured yet.</i>");

  const keyboard = new InlineKeyboard();
  for (const [key] of entries) {
    // Each provider gets its own row — provider name is the callback payload
    keyboard.text(
      `${providerEmoji(key, config)} ${key}${key === config.defaultProvider ? " ★" : ""}`,
      `cfg:pd:${key}`,
    ).row();
  }
  keyboard
    .text("➕ Add Provider", "cfg:pa").row()
    .text("← Back",         "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Provider Detail ──────────────────────────────────────

export function renderProviderDetail(
  providerKey: string,
  config: StackOwlConfig,
  health?: HealthMap,
): ScreenContent {
  const entry = config.providers[providerKey];
  if (!entry) {
    return {
      text: `❌ Provider <b>${providerKey}</b> not found.`,
      keyboard: new InlineKeyboard().text("← Back", "cfg:bc"),
    };
  }

  const dot       = statusDot(providerKey, health);
  const emoji     = providerEmoji(providerKey, config);
  const isDefault = providerKey === config.defaultProvider;
  const model     = entry.defaultModel ?? "—";
  const baseUrl   = entry.baseUrl ?? "(none)";
  const hasKey    = !!entry.apiKey;

  const latency = health?.[providerKey]?.latencyMs;
  const latencyStr = latency !== undefined ? `${latency}ms` : "—";

  const text =
    `${dot} ${emoji} <b>${providerKey}</b>${isDefault ? "  ★ DEFAULT" : ""}\n\n` +
    `Model:    <code>${model}</code>\n` +
    `Base URL: <code>${baseUrl}</code>\n` +
    `API Key:  ${hasKey ? maskKey(entry.apiKey!) : "<i>not set</i>"}\n` +
    `Latency:  ${latencyStr}`;

  const keyboard = new InlineKeyboard()
    .text("🔬 Test",         `cfg:pt:${providerKey}`).row()
    .text("✏️ Change Model", `cfg:pk:${providerKey}`).row();

  if (!isDefault) {
    keyboard.text("⭐ Set as Default", `cfg:pd_def:${providerKey}`).row();
  }
  keyboard
    .text("🗑 Remove…",  `cfg:pd_rm:${providerKey}`).row()
    .text("← Back",     "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Provider Remove Confirm ─────────────────────────────

export function renderProviderRemoveConfirm(providerKey: string): ScreenContent {
  const text =
    `⚠️ Remove provider <b>${providerKey}</b>?\n\n` +
    `This will delete its connection settings from your config.`;

  const keyboard = new InlineKeyboard()
    .text("✅ Yes, remove",  `cfg:pd_rx:${providerKey}`)
    .text("❌ Cancel",       `cfg:pd:${providerKey}`);

  return { text, keyboard };
}

// ─── Screen: Add Provider — Type Picker ──────────────────────────

export function renderAddProviderType(): ScreenContent {
  const text =
    `➕ <b>Add Provider</b>\n\n` +
    `Choose the provider type:`;

  const keyboard = new InlineKeyboard()
    .text("🦙 Ollama (Local)",   "cfg:pa:ollama").row()
    .text("☁️ Ollama Cloud",     "cfg:pa:ollama-cloud").row()
    .text("🧪 LM Studio",        "cfg:pa:lmstudio").row()
    .text("🤖 OpenAI",           "cfg:pa:openai").row()
    .text("🔮 Anthropic",        "cfg:pa:anthropic").row()
    .text("⚙️ Custom (OpenAI-compat)", "cfg:pa:openai-compatible").row()
    .text("← Back",              "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Add Provider — URL needed ───────────────────────────

export function renderAddProviderUrl(providerType: string): ScreenContent {
  const defaults: Record<string, string> = {
    ollama:              "http://127.0.0.1:11434",
    "ollama-cloud":      "https://your-cloud.endpoint.com",
    lmstudio:            "http://127.0.0.1:1234",
    openai:              "https://api.openai.com/v1",
    anthropic:           "(no URL needed)",
    "openai-compatible": "https://your-endpoint.com/v1",
  };

  const emoji = PROVIDER_TYPE_META[providerType]?.emoji ?? "⚙️";
  const text =
    `${emoji} <b>Add ${PROVIDER_TYPE_META[providerType]?.label ?? providerType}</b>\n\n` +
    `Send the <b>base URL</b> for this provider.\n` +
    `Default: <code>${defaults[providerType] ?? "—"}</code>\n\n` +
    `<i>Or tap Skip to use the default.</i>`;

  const keyboard = new InlineKeyboard()
    .text("⏭ Use Default URL", `cfg:pu_skip:${providerType}`).row()
    .text("← Cancel",          "cfg:pa");

  return { text, keyboard };
}

// ─── Screen: Add Provider — API Key ──────────────────────────────

export function renderAddProviderKey(
  providerType: string,
  _gatewayPort: number,
): ScreenContent {
  const emoji = PROVIDER_TYPE_META[providerType]?.emoji ?? "⚙️";
  const text =
    `${emoji} <b>API Key for ${PROVIDER_TYPE_META[providerType]?.label ?? providerType}</b>\n\n` +
    `Send your API key now.\n\n` +
    `🗑 <b>I will delete your message immediately</b> so it won't stay in chat history.\n\n` +
    `Alternatively, use the secure local web form — your key never enters Telegram.`;

  const keyboard = new InlineKeyboard()
    .text("🔒 Use secure web form", `cfg:ky_web:${providerType}`).row()
    .text("⏭ Skip (no key needed)", `cfg:ky_skip:${providerType}`).row()
    .text("← Cancel",               "cfg:pa");

  return { text, keyboard };
}

// ─── Screen: Model Picker ─────────────────────────────────────────

export function renderModelPicker(
  models: string[],
  currentModel: string | undefined,
  contextLabel: string,
  page: number = 0,
): ScreenContent {
  const PAGE_SIZE = 6;
  const totalPages = Math.ceil(models.length / PAGE_SIZE);
  const pageModels = models.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const text =
    `🎯 <b>Select Model</b>\n` +
    `for: <i>${contextLabel}</i>\n` +
    (currentModel ? `Current: <code>${currentModel}</code>\n` : "") +
    (models.length === 0 ? "\n⚠️ No models found. Is the provider running?" : "");

  const keyboard = new InlineKeyboard();
  for (let i = 0; i < pageModels.length; i++) {
    const globalIdx = page * PAGE_SIZE + i;
    const model = pageModels[i]!;
    const tick = model === currentModel ? "✓ " : "";
    keyboard.text(`${tick}${model}`, `cfg:mp:${globalIdx}`).row();
  }

  // Pagination controls
  if (totalPages > 1) {
    const pagRow = new InlineKeyboard();
    if (page > 0)              pagRow.text("◀", `cfg:mp_pg:${page - 1}`);
    pagRow.text(`${page + 1}/${totalPages}`, "cfg:noop");
    if (page < totalPages - 1) pagRow.text("▶", `cfg:mp_pg:${page + 1}`);
    keyboard.row().add(...pagRow.inline_keyboard.flat());
  }

  keyboard.row().text("← Back", "cfg:bc");
  return { text, keyboard };
}

// ─── Screen: Model Roles ──────────────────────────────────────────

export function renderModelRoles(config: StackOwlConfig): ScreenContent {
  const roles = (config as any).modelRoles as Record<string, { provider: string; model: string }> | undefined;

  const fmt = (role: string, fallbackProv: string, fallbackModel: string): string => {
    const r = roles?.[role];
    const prov  = r?.provider ?? fallbackProv;
    const model = r?.model    ?? fallbackModel;
    return `<code>${prov}</code> · <code>${model}</code>`;
  };

  const text =
    `🎯 <b>Model Roles</b>\n\n` +
    `Assign different models to each system role:\n\n` +
    `💬 <b>Default Chat:</b>    ${fmt("chat",       config.defaultProvider, config.defaultModel)}\n` +
    `🔬 <b>Synthesis:</b>       ${fmt("synthesis",  config.synthesis?.provider ?? "anthropic", config.synthesis?.model ?? "—")}\n` +
    `🔍 <b>Embedding:</b>       ${fmt("embedding",  "ollama", config.pellets?.embeddingModel ?? "nomic-embed-text")}\n` +
    `⚡ <b>Fast Routing:</b>    ${fmt("fastRouting", config.defaultProvider, config.defaultModel)}\n` +
    `🏛 <b>Parliament:</b>      ${fmt("parliament",  config.defaultProvider, config.defaultModel)}`;

  const keyboard = new InlineKeyboard()
    .text("💬 Default Chat",   "cfg:rl:chat").row()
    .text("🔬 Synthesis",      "cfg:rl:synthesis").row()
    .text("🔍 Embedding",      "cfg:rl:embedding").row()
    .text("⚡ Fast Routing",   "cfg:rl:fastRouting").row()
    .text("🏛 Parliament",     "cfg:rl:parliament").row()
    .text("← Back",            "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Role → Provider Picker ──────────────────────────────

export function renderRoleProviderPicker(
  role: string,
  providers: string[],
  config: StackOwlConfig,
): ScreenContent {
  const roleLabels: Record<string, string> = {
    chat:        "💬 Default Chat",
    synthesis:   "🔬 Synthesis",
    embedding:   "🔍 Embedding",
    fastRouting: "⚡ Fast Routing",
    parliament:  "🏛 Parliament",
  };

  const text =
    `${roleLabels[role] ?? role}\n\n` +
    `Select the <b>provider</b> to use for this role:`;

  const keyboard = new InlineKeyboard();
  providers.forEach((p, i) => {
    keyboard.text(`${providerEmoji(p, config)} ${p}`, `cfg:pp:${i}`).row();
  });
  keyboard.text("← Back", "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Smart Routing ────────────────────────────────────────

export function renderSmartRouting(config: StackOwlConfig): ScreenContent {
  const sr      = config.smartRouting;
  const enabled = sr?.enabled ?? false;
  const roster  = sr?.availableModels ?? [];

  const toggleLabel = enabled ? "🔴 Disable Smart Routing" : "🟢 Enable Smart Routing";

  const rosterLines = roster.length > 0
    ? roster.map((e, i) => {
        const tier = i === 0 ? "light" : i === roster.length - 1 ? "heavy" : "mid";
        return `${i + 1}. <code>${e.providerName}</code> · <b>${e.modelName}</b>  <i>${tier}</i>`;
      }).join("\n")
    : "<i>No models in roster. Add at least 2 to enable routing.</i>";

  const fallbackLine = sr?.fallbackProvider
    ? `\nFallback: <code>${sr.fallbackProvider}</code> · <b>${sr.fallbackModel ?? "—"}</b>`
    : "";

  const text =
    `⚡ <b>Smart Routing</b>\n\n` +
    `Status: ${enabled ? "🟢 ON" : "🔴 OFF"}\n\n` +
    rosterLines +
    fallbackLine;

  const keyboard = new InlineKeyboard()
    .text(toggleLabel, "cfg:sr_tog").row();

  roster.forEach((_, i) => {
    const upCb   = i === 0                 ? "cfg:noop" : `cfg:sr_up:${i}`;
    const downCb = i === roster.length - 1 ? "cfg:noop" : `cfg:sr_dn:${i}`;
    const upTxt  = i === 0                 ? "·" : "↑";
    const downTxt = i === roster.length - 1 ? "·" : "↓";
    keyboard
      .text(upTxt,                          upCb)
      .text(downTxt,                        downCb)
      .text(`✕ ${roster[i].modelName}`,     `cfg:sr_rm:${i}`)
      .row();
  });

  keyboard.text("➕ Add Model", "cfg:sr_add").row();
  keyboard.text("← Back", "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Smart Routing — Provider Picker ─────────────────────

export function renderSmartRoutingProviderPicker(providers: string[]): ScreenContent {
  const text = `⚡ <b>Add to Roster</b>\n\nChoose provider:`;
  const keyboard = new InlineKeyboard();
  providers.forEach((p) => {
    keyboard.text(p, `cfg:sr_ap:${p}`).row();
  });
  keyboard.text("← Back", "cfg:bc");
  return { text, keyboard };
}

// ─── Screen: Smart Routing — Model Picker ────────────────────────

export function renderSmartRoutingModelPicker(
  providerName: string,
  models: string[],
): ScreenContent {
  const text = `⚡ <b>Add to Roster</b>\n\nProvider: <code>${providerName}</code>\nChoose model:`;
  const keyboard = new InlineKeyboard();
  models.forEach((m) => {
    keyboard.text(m, `cfg:sr_am:${providerName}:${m}`).row();
  });
  keyboard.text("← Back", "cfg:bc");
  return { text, keyboard };
}

// ─── Screen: Health Check ─────────────────────────────────────────

export function renderHealthCheck(
  health: HealthMap,
  config: StackOwlConfig,
  loading: boolean,
): ScreenContent {
  const entries = Object.entries(config.providers);

  const lines = loading
    ? ["<i>⏳ Checking all providers…</i>"]
    : entries.map(([key]) => {
        const h       = health[key];
        const dot     = h ? (h.ok ? "🟢" : "🔴") : "⚪";
        const latency = h?.latencyMs !== undefined ? ` · ${h.latencyMs}ms` : "";
        return `${dot} <b>${key}</b>${latency}`;
      });

  const text =
    `🏥 <b>Provider Health</b>\n\n` +
    lines.join("\n");

  const keyboard = new InlineKeyboard()
    .text("🔄 Re-check All", "cfg:hc_r").row()
    .text("← Back",         "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Secure Web Form Link ────────────────────────────────

export function renderWebFormLink(
  providerType: string,
  token: string,
  port: number,
): ScreenContent {
  const url   = `http://localhost:${port}/config/key?token=${token}`;
  const emoji = PROVIDER_TYPE_META[providerType]?.emoji ?? "⚙️";
  const text  =
    `${emoji} <b>Secure Key Entry</b>\n\n` +
    `Open this link on your local machine to enter your API key.\n` +
    `It will <b>never</b> pass through Telegram.\n\n` +
    `🔗 <a href="${url}">Open secure form</a>\n\n` +
    `<i>Link expires in 5 minutes.</i>`;

  const keyboard = new InlineKeyboard()
    .text("✅ I've entered the key", `cfg:ky_done:${providerType}`).row()
    .text("← Cancel",                "cfg:pa");

  return { text, keyboard };
}

// ─── Screen: Generic Error ────────────────────────────────────────

export function renderError(message: string): ScreenContent {
  return {
    text: `❌ <b>Error</b>\n\n${message}`,
    keyboard: new InlineKeyboard().text("← Back", "cfg:bc"),
  };
}

// ─── Screen: Success confirmation ────────────────────────────────

export function renderSuccess(message: string): ScreenContent {
  return {
    text: `✅ <b>Done</b>\n\n${message}`,
    keyboard: new InlineKeyboard()
      .text("⚙️ Back to Main Menu", "cfg:~").row()
      .text("← Back",              "cfg:bc"),
  };
}
