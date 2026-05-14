/**
 * StackOwl — Telegram Unified Nav: Screen Renderers
 *
 * Pure functions: data in → ScreenContent out.
 * No side effects, no async, no imports from gateway.
 *
 * Callback prefix: "nav:"
 *   nav:st  = status screen
 *   nav:cfg = AI config (delegates to TelegramConfigMenu)
 *   nav:vc  = voice (delegates to TelegramVoiceMenu)
 *   nav:mcp = MCP server list
 *   nav:mcp:dis:{name} = disable MCP server
 *   nav:mcp:en:{name}  = enable MCP server
 *   nav:mcp:rc:{name}  = reconnect MCP server
 *   nav:owl = owl list
 *   nav:owl:sw:{name}  = switch to owl
 *   nav:mem = memory info
 *   nav:sk  = skills list
 *   nav:sk:en:{name}   = enable skill
 *   nav:sk:dis:{name}  = disable skill
 *   nav:bk  = go back
 */

import { InlineKeyboard } from "grammy";

export interface ScreenContent {
  text: string;
  keyboard: InlineKeyboard;
}

// ─── Helpers ──────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Truncate a string to fit within callback_data byte limit when prefixed */
function truncKey(s: string, maxBytes = 40): string {
  const enc = new TextEncoder();
  if (enc.encode(s).length <= maxBytes) return s;
  let t = s;
  while (enc.encode(t).length > maxBytes && t.length > 0) t = t.slice(0, -1);
  return t;
}

// ─── Root screen ──────────────────────────────────────────────────

export function renderRoot(): ScreenContent {
  const text =
    `🦉 <b>StackOwl Control Panel</b>\n\n` +
    `Tap any section to manage it.`;

  const keyboard = new InlineKeyboard()
    .text("🤖 AI Config", "nav:cfg").text("🎤 Voice", "nav:vc").row()
    .text("🔌 MCP Servers", "nav:mcp").text("🧠 Memory", "nav:mem").row()
    .text("🦉 Owls", "nav:owl").text("🔧 Skills", "nav:sk").row()
    .text("📊 Status", "nav:st");

  return { text, keyboard };
}

// ─── Status screen ────────────────────────────────────────────────

export function renderStatus(
  model: string,
  owlEmoji: string,
  owlName: string,
  sessionCount = 0,
): ScreenContent {
  const text =
    `📊 <b>Status</b>\n\n` +
    `<b>Model:</b> <code>${esc(model)}</code>\n` +
    `<b>Owl:</b> ${esc(owlEmoji)} ${esc(owlName)}\n` +
    `<b>Active sessions:</b> ${sessionCount}`;

  const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
  return { text, keyboard };
}

// ─── MCP screens ──────────────────────────────────────────────────

export interface McpServerInfo {
  name: string;
  connected: boolean;
  toolCount: number;
}

export function renderMcpList(servers: McpServerInfo[]): ScreenContent {
  if (servers.length === 0) {
    const text =
      `🔌 <b>MCP Servers</b>\n\nNo MCP servers configured.\n\n` +
      `Use <code>/mcp add &lt;package&gt;</code> to add one.`;
    const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
    return { text, keyboard };
  }

  const lines = servers.map(s =>
    `${s.connected ? "🟢" : "🔴"} <b>${esc(s.name)}</b> (${s.toolCount} tools)`
  );
  const text = `🔌 <b>MCP Servers</b>\n\n${lines.join("\n")}`;

  const keyboard = new InlineKeyboard();
  for (const s of servers) {
    const key = truncKey(s.name);
    if (s.connected) {
      keyboard.text(`⏸ ${s.name}`, `nav:mcp:dis:${key}`).text(`🔄 Reconnect`, `nav:mcp:rc:${key}`).row();
    } else {
      keyboard.text(`▶️ Enable ${s.name}`, `nav:mcp:en:${key}`).row();
    }
  }
  keyboard.text("← Back", "nav:bk");

  return { text, keyboard };
}

// ─── Owl screens ──────────────────────────────────────────────────

export interface OwlInfo {
  name: string;
  emoji: string;
  isPinned: boolean;
}

export function renderOwlList(owls: OwlInfo[], currentOwlName: string): ScreenContent {
  if (owls.length === 0) {
    const text =
      `🦉 <b>Owls</b>\n\nNo custom owls found.\n\n` +
      `Use <code>/owl create</code> to create one.`;
    const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
    return { text, keyboard };
  }

  const lines = owls.map(o => {
    const active = o.name === currentOwlName ? " ✅" : "";
    return `${o.emoji} <b>${esc(o.name)}</b>${active}`;
  });
  const text = `🦉 <b>Owls</b>\n\n${lines.join("\n")}`;

  const keyboard = new InlineKeyboard();
  for (const o of owls) {
    if (o.name !== currentOwlName) {
      const key = truncKey(o.name);
      keyboard.text(`Switch → ${o.emoji} ${o.name}`, `nav:owl:sw:${key}`).row();
    }
  }
  keyboard.text("← Back", "nav:bk");

  return { text, keyboard };
}

// ─── Memory screen ────────────────────────────────────────────────

export function renderMemoryInfo(statsText: string): ScreenContent {
  const text =
    `🧠 <b>Memory</b>\n\n${esc(statsText)}\n\n` +
    `<i>Use /memory for full management.</i>`;
  const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
  return { text, keyboard };
}

// ─── Skills screen ────────────────────────────────────────────────

export interface SkillInfo {
  name: string;
  enabled: boolean;
}

export function renderSkillsList(skills: SkillInfo[]): ScreenContent {
  if (skills.length === 0) {
    const text =
      `🔧 <b>Skills</b>\n\nNo skills installed.\n\n` +
      `Use <code>/skills install</code> to browse ClawHub.`;
    const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
    return { text, keyboard };
  }

  const enabledCount = skills.filter(s => s.enabled).length;
  const lines = skills.map(s => `${s.enabled ? "✅" : "⬜"} ${esc(s.name)}`);
  const text =
    `🔧 <b>Skills</b> (${enabledCount}/${skills.length} enabled)\n\n${lines.join("\n")}`;

  const keyboard = new InlineKeyboard();
  for (const s of skills) {
    const key = truncKey(s.name);
    if (s.enabled) {
      keyboard.text(`⬜ Disable ${s.name}`, `nav:sk:dis:${key}`).row();
    } else {
      keyboard.text(`✅ Enable ${s.name}`, `nav:sk:en:${key}`).row();
    }
  }
  keyboard.text("← Back", "nav:bk");

  return { text, keyboard };
}
