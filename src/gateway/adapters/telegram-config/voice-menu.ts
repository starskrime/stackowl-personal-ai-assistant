/**
 * StackOwl — Telegram /voice Configuration Menu
 *
 * Handles all "vcfg:*" callback_query events and the /voice command.
 * Fully self-contained — no separate state/screens files needed because
 * the voice menu is simpler than the provider config menu.
 *
 * Screens:
 *   main    — overview of all settings with action buttons
 *   model   — Whisper model picker (tiny.en … large)
 *   voice   — macOS TTS voice picker (female/male)
 *   speed   — speaking rate with +/− controls and presets
 *   silence — silence duration and RMS threshold with +/− controls
 *
 * All screens edit a single message in-place (the one sent when /voice runs).
 *
 * Callback data format: "vcfg:<CMD>" — always ≤ 64 bytes.
 * Prefix "vcfg:" avoids collision with "cfg:" (provider config) or "fb:" (feedback).
 */

import type { Context } from "grammy";
import type { StackOwlConfig } from "../../../config/loader.js";

// ─── Constants ────────────────────────────────────────────────────

export const WHISPER_MODELS: Array<{ id: string; label: string; size: string }> = [
  { id: "tiny.en",  label: "tiny.en",  size: "39 MB"  },
  { id: "base.en",  label: "base.en",  size: "75 MB"  },
  { id: "small.en", label: "small.en", size: "244 MB" },
  { id: "medium",   label: "medium",   size: "769 MB" },
  { id: "large",    label: "large",    size: "1.5 GB" },
];

/** Curated macOS voice list. User can still type any voice name manually. */
export const MACOS_VOICES: Array<{ id: string; label: string; lang: string; gender: "F" | "M" }> = [
  // Female
  { id: "Samantha", label: "Samantha", lang: "US English",  gender: "F" },
  { id: "Victoria", label: "Victoria", lang: "US English",  gender: "F" },
  { id: "Karen",    label: "Karen",    lang: "Australian",  gender: "F" },
  { id: "Moira",    label: "Moira",    lang: "Irish",       gender: "F" },
  { id: "Fiona",    label: "Fiona",    lang: "Scottish",    gender: "F" },
  { id: "Tessa",    label: "Tessa",    lang: "S. African",  gender: "F" },
  // Male
  { id: "Alex",     label: "Alex",     lang: "US English",  gender: "M" },
  { id: "Daniel",   label: "Daniel",   lang: "British",     gender: "M" },
  { id: "Fred",     label: "Fred",     lang: "US English",  gender: "M" },
  { id: "Oliver",   label: "Oliver",   lang: "British",     gender: "M" },
];

const SPEED_PRESETS = [
  { label: "Slow 150",   value: 150 },
  { label: "Normal 200", value: 200 },
  { label: "Fast 250",   value: 250 },
  { label: "Rapid 300",  value: 300 },
];

const MENU_TTL_MS  = 10 * 60 * 1000; // 10 min
const SPEED_STEP   = 20;
const SPEED_MIN    = 100;
const SPEED_MAX    = 400;
const DUR_STEP     = 500;
const DUR_MIN      = 500;
const DUR_MAX      = 5000;
const THRESH_STEP  = 100;
const THRESH_MIN   = 100;
const THRESH_MAX   = 5000;

// ─── State ────────────────────────────────────────────────────────

type VoiceScreen = "main" | "model" | "voice" | "speed" | "silence";

interface VoiceMenuState {
  userId:    number;
  chatId:    number;
  messageId: number;
  screen:    VoiceScreen;
  lastActivity: number;
}

// ─── Helpers ─────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function voiceDefaults(cfg: StackOwlConfig) {
  return {
    model:            cfg.voice?.model            ?? "base.en",
    systemVoice:      cfg.voice?.systemVoice      ?? "Samantha",
    speakRate:        cfg.voice?.speakRate         ?? 200,
    silenceThreshold: cfg.voice?.silenceThreshold  ?? 500,
    silenceDurationMs:cfg.voice?.silenceDurationMs ?? 1500,
  };
}

function modelSize(id: string): string {
  return WHISPER_MODELS.find(m => m.id === id)?.size ?? "?";
}

function voiceMeta(id: string): string {
  const v = MACOS_VOICES.find(v => v.id === id);
  return v ? `${v.lang}, ${v.gender === "F" ? "female" : "male"}` : "custom";
}

// ─── Screen renderers ────────────────────────────────────────────

function renderMain(cfg: StackOwlConfig): { text: string; keyboard: unknown } {
  const v = voiceDefaults(cfg);
  const text = [
    `🎤 <b>Voice Settings</b>`,
    ``,
    `🤖 <b>Model:</b>     <code>${esc(v.model)}</code>  <i>(${esc(modelSize(v.model))})</i>`,
    `🔊 <b>Voice:</b>     <code>${esc(v.systemVoice)}</code>  <i>(${esc(voiceMeta(v.systemVoice))})</i>`,
    `⚡ <b>Speed:</b>     <code>${v.speakRate} wpm</code>`,
    `🔇 <b>Silence:</b>   <code>${v.silenceDurationMs} ms</code> · RMS <code>${v.silenceThreshold}</code>`,
    ``,
    `<i>Changes save immediately to stackowl.config.json</i>`,
  ].join("\n");

  const keyboard = {
    inline_keyboard: [
      [
        { text: "🤖 Model",   callback_data: "vcfg:mp" },
        { text: "🔊 Voice",   callback_data: "vcfg:vp" },
      ],
      [
        { text: "⚡ Speed",   callback_data: "vcfg:sp" },
        { text: "🔇 Silence", callback_data: "vcfg:sl" },
      ],
      [
        { text: "✖ Close",   callback_data: "vcfg:cl" },
      ],
    ],
  };

  return { text, keyboard };
}

function renderModelPicker(cfg: StackOwlConfig): { text: string; keyboard: unknown } {
  const current = voiceDefaults(cfg).model;
  const text = [
    `🤖 <b>Whisper Model</b>`,
    ``,
    `Select the offline speech recognition model.`,
    `Larger = more accurate but slower first load.`,
    ``,
    ...WHISPER_MODELS.map(m =>
      `${m.id === current ? "✓ " : "  "}<code>${m.id}</code>  ${m.size}`,
    ),
  ].join("\n");

  const modelRows: unknown[][] = [];
  for (let i = 0; i < WHISPER_MODELS.length; i += 2) {
    const row = WHISPER_MODELS.slice(i, i + 2).map(m => ({
      text: `${m.id === current ? "✓ " : ""}${m.label} (${m.size})`,
      callback_data: `vcfg:m:${m.id}`,
    }));
    modelRows.push(row);
  }

  const keyboard = {
    inline_keyboard: [
      ...modelRows,
      [{ text: "← Back", callback_data: "vcfg:mn" }],
    ],
  };

  return { text, keyboard };
}

function renderVoicePicker(cfg: StackOwlConfig): { text: string; keyboard: unknown } {
  const current = voiceDefaults(cfg).systemVoice;
  const females = MACOS_VOICES.filter(v => v.gender === "F");
  const males   = MACOS_VOICES.filter(v => v.gender === "M");

  const listLine = (v: typeof MACOS_VOICES[0]) =>
    `${v.id === current ? "✓ " : "  "}<code>${v.id}</code> — ${v.lang}`;

  const text = [
    `🔊 <b>TTS Voice</b>  (macOS <code>say</code>)`,
    ``,
    `<b>Female</b>`,
    ...females.map(listLine),
    ``,
    `<b>Male</b>`,
    ...males.map(listLine),
  ].join("\n");

  const makeBtn = (v: typeof MACOS_VOICES[0]) => ({
    text: `${v.id === current ? "✓ " : ""}${v.label}`,
    callback_data: `vcfg:v:${v.id}`,
  });

  // 3 per row
  const allVoices = [...females, ...males];
  const voiceRows: unknown[][] = [];
  for (let i = 0; i < allVoices.length; i += 3) {
    voiceRows.push(allVoices.slice(i, i + 3).map(makeBtn));
  }

  const keyboard = {
    inline_keyboard: [
      ...voiceRows,
      [{ text: "← Back", callback_data: "vcfg:mn" }],
    ],
  };

  return { text, keyboard };
}

function renderSpeed(cfg: StackOwlConfig): { text: string; keyboard: unknown } {
  const current = voiceDefaults(cfg).speakRate;
  const text = [
    `⚡ <b>Speaking Speed</b>`,
    ``,
    `Current: <code>${current} wpm</code>`,
    ``,
    `Range: ${SPEED_MIN}–${SPEED_MAX} wpm`,
  ].join("\n");

  const keyboard = {
    inline_keyboard: [
      [
        { text: `◀ −${SPEED_STEP}`, callback_data: "vcfg:r-" },
        { text: `${current} wpm`,   callback_data: "vcfg:mn" },
        { text: `+${SPEED_STEP} ▶`, callback_data: "vcfg:r+" },
      ],
      SPEED_PRESETS.map(p => ({
        text: p.value === current ? `✓ ${p.label}` : p.label,
        callback_data: `vcfg:r:${p.value}`,
      })),
      [{ text: "← Back", callback_data: "vcfg:mn" }],
    ],
  };

  return { text, keyboard };
}

function renderSilence(cfg: StackOwlConfig): { text: string; keyboard: unknown } {
  const { silenceDurationMs: dur, silenceThreshold: thresh } = voiceDefaults(cfg);
  const text = [
    `🔇 <b>Silence Detection</b>`,
    ``,
    `<b>Duration</b> — how long to wait after speech stops`,
    `Current: <code>${dur} ms</code>  (${DUR_MIN}–${DUR_MAX} ms)`,
    ``,
    `<b>Threshold</b> — mic sensitivity (higher = less sensitive)`,
    `Current: <code>${thresh} RMS</code>  (${THRESH_MIN}–${THRESH_MAX})`,
  ].join("\n");

  const keyboard = {
    inline_keyboard: [
      [
        { text: `◀ −${DUR_STEP}ms`,    callback_data: "vcfg:sd-" },
        { text: `${dur} ms`,            callback_data: "vcfg:mn"  },
        { text: `+${DUR_STEP}ms ▶`,    callback_data: "vcfg:sd+" },
      ],
      [
        { text: `◀ −${THRESH_STEP} RMS`, callback_data: "vcfg:st-" },
        { text: `${thresh} RMS`,          callback_data: "vcfg:mn"  },
        { text: `+${THRESH_STEP} RMS ▶`, callback_data: "vcfg:st+" },
      ],
      [{ text: "← Back", callback_data: "vcfg:mn" }],
    ],
  };

  return { text, keyboard };
}

// ─── Controller ───────────────────────────────────────────────────

export class TelegramVoiceMenu {
  private states = new Map<number, VoiceMenuState>();
  private cleanupTimer: ReturnType<typeof setInterval>;

  constructor(
    private getConfig: () => StackOwlConfig,
    private saveConfigFn: (config: StackOwlConfig) => Promise<void>,
  ) {
    this.cleanupTimer = setInterval(() => this.evict(), 5 * 60 * 1000);
    this.cleanupTimer.unref();
  }

  destroy(): void {
    clearInterval(this.cleanupTimer);
    this.states.clear();
  }

  // ─── Entry point ─────────────────────────────────────────────

  async handleCommand(ctx: Context): Promise<void> {
    const userId = ctx.from?.id;
    const chatId = ctx.chat?.id;
    if (!userId || !chatId) return;

    const content = renderMain(this.getConfig());
    const sent = await ctx.reply(content.text, {
      parse_mode: "HTML",
      reply_markup: content.keyboard as any,
    });

    this.states.set(userId, {
      userId,
      chatId,
      messageId: sent.message_id,
      screen: "main",
      lastActivity: Date.now(),
    });
  }

  // ─── Callback router ─────────────────────────────────────────

  async handleCallback(ctx: Context, data: string): Promise<boolean> {
    const userId = ctx.from?.id;
    if (!userId) return false;

    const state = this.states.get(userId);
    if (!state) {
      await ctx.answerCallbackQuery({ text: "⏱ Session expired. Send /voice to restart." });
      return true;
    }

    state.lastActivity = Date.now();
    await ctx.answerCallbackQuery();

    const cmd = data.slice("vcfg:".length); // strip prefix

    // ── Navigation ──────────────────────────────────────────────
    if (cmd === "mn") {
      await this.show(ctx, state, "main");
      return true;
    }
    if (cmd === "cl") {
      await ctx.editMessageText("🎤 Voice settings closed.", {
        parse_mode: "HTML",
      });
      this.states.delete(userId);
      return true;
    }

    // ── Screen transitions ───────────────────────────────────────
    if (cmd === "mp") { await this.show(ctx, state, "model");   return true; }
    if (cmd === "vp") { await this.show(ctx, state, "voice");   return true; }
    if (cmd === "sp") { await this.show(ctx, state, "speed");   return true; }
    if (cmd === "sl") { await this.show(ctx, state, "silence"); return true; }

    const config = this.getConfig();

    // ── Model selection: vcfg:m:<modelId> ───────────────────────
    if (cmd.startsWith("m:")) {
      const modelId = cmd.slice(2);
      if (WHISPER_MODELS.some(m => m.id === modelId)) {
        config.voice = { ...config.voice, model: modelId };
        await this.save(config);
        await this.show(ctx, state, "model");
      }
      return true;
    }

    // ── Voice selection: vcfg:v:<voiceId> ───────────────────────
    if (cmd.startsWith("v:")) {
      const voiceId = cmd.slice(2);
      config.voice = { ...config.voice, systemVoice: voiceId };
      await this.save(config);
      await this.show(ctx, state, "voice");
      return true;
    }

    // ── Speed: vcfg:r+ / vcfg:r- / vcfg:r:<value> ───────────────
    if (cmd === "r+") {
      const cur = voiceDefaults(config).speakRate;
      config.voice = { ...config.voice, speakRate: Math.min(cur + SPEED_STEP, SPEED_MAX) };
      await this.save(config);
      await this.show(ctx, state, "speed");
      return true;
    }
    if (cmd === "r-") {
      const cur = voiceDefaults(config).speakRate;
      config.voice = { ...config.voice, speakRate: Math.max(cur - SPEED_STEP, SPEED_MIN) };
      await this.save(config);
      await this.show(ctx, state, "speed");
      return true;
    }
    if (cmd.startsWith("r:")) {
      const val = parseInt(cmd.slice(2), 10);
      if (!isNaN(val) && val >= SPEED_MIN && val <= SPEED_MAX) {
        config.voice = { ...config.voice, speakRate: val };
        await this.save(config);
        await this.show(ctx, state, "speed");
      }
      return true;
    }

    // ── Silence duration: vcfg:sd+ / vcfg:sd- ───────────────────
    if (cmd === "sd+") {
      const cur = voiceDefaults(config).silenceDurationMs;
      config.voice = { ...config.voice, silenceDurationMs: Math.min(cur + DUR_STEP, DUR_MAX) };
      await this.save(config);
      await this.show(ctx, state, "silence");
      return true;
    }
    if (cmd === "sd-") {
      const cur = voiceDefaults(config).silenceDurationMs;
      config.voice = { ...config.voice, silenceDurationMs: Math.max(cur - DUR_STEP, DUR_MIN) };
      await this.save(config);
      await this.show(ctx, state, "silence");
      return true;
    }

    // ── Silence threshold: vcfg:st+ / vcfg:st- ──────────────────
    if (cmd === "st+") {
      const cur = voiceDefaults(config).silenceThreshold;
      config.voice = { ...config.voice, silenceThreshold: Math.min(cur + THRESH_STEP, THRESH_MAX) };
      await this.save(config);
      await this.show(ctx, state, "silence");
      return true;
    }
    if (cmd === "st-") {
      const cur = voiceDefaults(config).silenceThreshold;
      config.voice = { ...config.voice, silenceThreshold: Math.max(cur - THRESH_STEP, THRESH_MIN) };
      await this.save(config);
      await this.show(ctx, state, "silence");
      return true;
    }

    return false; // unrecognised command
  }

  // ─── Internals ───────────────────────────────────────────────

  private async show(
    ctx: Context,
    state: VoiceMenuState,
    screen: VoiceScreen,
  ): Promise<void> {
    state.screen = screen;
    const config = this.getConfig();

    let content: { text: string; keyboard: unknown };
    switch (screen) {
      case "model":   content = renderModelPicker(config); break;
      case "voice":   content = renderVoicePicker(config); break;
      case "speed":   content = renderSpeed(config);       break;
      case "silence": content = renderSilence(config);     break;
      default:        content = renderMain(config);        break;
    }

    try {
      await ctx.api.editMessageText(
        state.chatId,
        state.messageId,
        content.text,
        { parse_mode: "HTML", reply_markup: content.keyboard as any },
      );
    } catch {
      // Message unchanged — ignore Telegram "message is not modified" error
    }
  }

  private async save(config: StackOwlConfig): Promise<void> {
    await this.saveConfigFn(config);
  }

  private evict(): void {
    const now = Date.now();
    for (const [uid, s] of this.states) {
      if (now - s.lastActivity > MENU_TTL_MS) this.states.delete(uid);
    }
  }
}
