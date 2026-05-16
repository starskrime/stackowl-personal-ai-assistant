/**
 * Renders CoreCommandResult to a plain string for non-TUI channels.
 *
 * Each channel adapter calls the renderer appropriate for its format.
 * TUI channels continue using CommandResult directly (panels, etc.).
 */

import type { CoreCommandResult } from "../../cli/v2/commands/registry.js";

// ─── Telegram (MarkdownV2) ─────────────────────────────────────────

const TG_ESCAPE_RE = /[_*[\]()~`>#+\-=|{}.!\\]/g;

function tgEsc(s: string): string {
  return s.replace(TG_ESCAPE_RE, (c) => `\\${c}`);
}

export function renderForTelegram(result: CoreCommandResult): string {
  switch (result.kind) {
    case "system-message":
      return formatTelegramText(result.text);
    case "error":
      return `❌ ${tgEsc(result.text)}`;
    case "action":
      return "";
  }
}

function formatTelegramText(text: string): string {
  const lines = text.split("\n");
  return lines
    .map((line) => {
      // Key-value lines — bold the key
      const kv = line.match(/^(\s*)(\S[^:]*?):\s+(.+)$/);
      if (kv) {
        const [, indent, key, val] = kv;
        return `${indent}*${tgEsc(key)}:* ${tgEsc(val)}`;
      }
      // Section headers (no colon, ends the line)
      if (/^[A-Z][^:]+:$/.test(line.trim())) {
        return `*${tgEsc(line.trim())}*`;
      }
      return tgEsc(line);
    })
    .join("\n");
}

// ─── Plain text (Slack, Discord, generic) ─────────────────────────

export function renderAsPlainText(result: CoreCommandResult): string {
  switch (result.kind) {
    case "system-message": return result.text;
    case "error":          return `Error: ${result.text}`;
    case "action":         return "";
  }
}

// ─── HTML (grammY HTML parse_mode fallback) ───────────────────────

function htmlEsc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function renderAsHtml(result: CoreCommandResult): string {
  switch (result.kind) {
    case "system-message": return `<pre>${htmlEsc(result.text)}</pre>`;
    case "error":          return `❌ <b>${htmlEsc(result.text)}</b>`;
    case "action":         return "";
  }
}
