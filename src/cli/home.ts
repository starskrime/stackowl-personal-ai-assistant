/**
 * StackOwl — Home Screen  (Screen 1)
 *
 * Shown immediately after the boot splash.
 * First keypress → emits "activate" with that key → CLIAdapter
 * transitions to Screen 2 (TerminalUI) without leaving the alt screen.
 *
 * Layout (all borders in yellow):
 *   ┌──────────────────────────────────────────────────────────┐
 *   │                                                          │
 *   │              ◈  S T A C K O W L                         │
 *   │              personal ai assistant                       │
 *   │                                                          │
 *   │  ┌──────────────────────────────────────────────────┐   │
 *   │  │  › _                                             │   │
 *   │  │                                                  │   │
 *   │  └──────────────────────────────────────────────────┘   │
 *   │  Type anything to start  ·  /help for commands          │
 *   │                                                          │
 *   │  [ Active Owl ]            [ Environment ]               │
 *   │  ◉ Archimedes gen4 ⚡7     ⌬ darwin  M-series            │
 *   │  Provider : Anthropic      🔌 MCP   : 4 active           │
 *   │  Model    : sonnet-4-6     📦 Skills: 12 loaded          │
 *   │                                                          │
 *   │  [ Recent Sessions ]                                     │
 *   │  → 2h   Refactored auth module                47 turns   │
 *   │  → 1d   Optimized vLLM dockerfile             23 turns   │
 *   │                                                          │
 *   │ ──────────────────────────────────────────────────────── │
 *   │  [/help] Commands   [/owls] Owls   [Ctrl+C] Quit        │
 *   └──────────────────────────────────────────────────────────┘
 */

import { EventEmitter } from "node:events";
import chalk            from "chalk";

// ─── ANSI ────────────────────────────────────────────────────────

const E  = "\x1B";
const H  = {
  altIn:  `${E}[?1049h`,
  altOut: `${E}[?1049l`,
  hide:   `${E}[?25l`,
  show:   `${E}[?25h`,
  clear:  `${E}[2J`,
  pos:    (r: number, c = 1) => `${E}[${r};${c}H`,
  el:     `${E}[2K`,    // erase line
};

const Y  = chalk.yellow;
const YB = chalk.yellow.bold;
const D  = chalk.dim;
const W  = chalk.white;
const C  = chalk.cyan;

// ─── Box-drawing ─────────────────────────────────────────────────

const B = {
  tl: "┌", tr: "┐", bl: "└", br: "┘",
  h:  "─", v:  "│",
  row: (w: number) => "─".repeat(w),
};

// ─── Helpers ────────────────────────────────────────────────────

function stripAnsi(s: string): string {
  return s.replace(/\x1B\[[0-9;]*[A-Za-z]/g, "");
}

function visLen(s: string): number {
  const plain = stripAnsi(s);
  let len = 0;
  for (const ch of plain) {
    const cp = ch.codePointAt(0) ?? 0;
    len += cp > 0xFFFF ? 2 : 1;
  }
  return len;
}

/** Pad a possibly-chalk-colored string to `width` visible columns. */
function pad(s: string, width: number): string {
  const vl = visLen(s);
  return s + " ".repeat(Math.max(0, width - vl));
}

function center(s: string, width: number): string {
  const vl   = visLen(s);
  const left = Math.max(0, Math.floor((width - vl) / 2));
  return " ".repeat(left) + s;
}

// ─── Types ────────────────────────────────────────────────────────

export interface HomeOpts {
  owlEmoji:   string;
  owlName:    string;
  generation: number;
  challenge:  number;
  provider:   string;
  model:      string;
  skills:     number;
  recentSessions: Array<{ title: string; turns: number; ago: string }>;
}

// ─── HomeScreen ──────────────────────────────────────────────────

export class HomeScreen extends EventEmitter {
  private _pulse  = false;
  private _timer: ReturnType<typeof setInterval> | null = null;
  private _buf    = "";

  constructor(private opts: HomeOpts) { super(); }

  // ─── Dimensions ────────────────────────────────────────────────

  private get cols() { return Math.max(process.stdout.columns ?? 80, 60); }
  private get rows() { return Math.max(process.stdout.rows    ?? 24, 20); }

  // ─── Lifecycle ─────────────────────────────────────────────────

  enter(): void {
    process.stdout.write(H.altIn + H.hide);
    if (process.stdin.isTTY) process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding("utf8");

    process.stdin.on("data",   this._onKey);
    process.stdout.on("resize", this._onResize);

    setTimeout(() => {
      this._render();
      this._timer = setInterval(() => {
        this._pulse = !this._pulse;
        this._renderInputBox();
      }, 800);
    }, 40);
  }

  /** Transition to Screen 2 — keeps alt screen + raw mode alive. */
  transition(): void {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    process.stdin.off("data",    this._onKey);
    process.stdout.off("resize", this._onResize);
    // intentionally NOT writing altOut / show cursor — TerminalUI takes over
  }

  /** Full quit (Ctrl+C from home). */
  close(): void {
    this.transition();
    process.stdout.write(H.show + H.altOut);
    if (process.stdin.isTTY) { try { process.stdin.setRawMode(false); } catch { /**/ } }
  }

  // ─── Key handler ───────────────────────────────────────────────

  private _onKey = (chunk: unknown) => {
    const key = typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
    if (key === "\x03" || key === "\x04") { this.emit("quit"); return; }
    // Any other key → activate Screen 2
    this.emit("activate", key);
  };

  private _onResize = () => this._render();

  // ─── Full render ───────────────────────────────────────────────

  private _render(): void {
    const c = this.cols;
    const r = this.rows;
    let   o = "";

    // Clear + position
    o += H.clear + H.pos(1);

    // ── Row 1: top border ─────────────────────────────────────────
    o += H.pos(1) + Y(B.tl + B.row(c - 2) + B.tr);

    // ── Inner rows 2..rows-1 ─────────────────────────────────────
    for (let i = 2; i < r; i++) {
      o += H.pos(i) + Y(B.v) + " ".repeat(c - 2) + Y(B.v);
    }

    // ── Row rows: bottom border ───────────────────────────────────
    o += H.pos(r) + Y(B.bl + B.row(c - 2) + B.br);

    process.stdout.write(o);

    // ── Content sections ──────────────────────────────────────────
    this._renderLogo();
    this._renderInputBox();
    this._renderInfo();
    this._renderSessions();
    this._renderShortcuts();
  }

  // ── Logo ────────────────────────────────────────────────────────

  private _renderLogo(): void {
    const c = this.cols;
    const inner = c - 2;  // width inside the border

    const line1 = YB("◈  S T A C K O W L");
    const line2 = D("personal ai assistant");

    process.stdout.write(
      H.pos(3) + Y(B.v) + center(line1, inner) + Y(B.v) +
      H.pos(4) + Y(B.v) + center(line2, inner) + Y(B.v),
    );
  }

  // ── Input box ───────────────────────────────────────────────────

  private _inputBoxRow(): number { return 6; }

  private _renderInputBox(): void {
    const c      = this.cols;
    const margin = 3;                         // spaces from outer border
    const boxW   = c - 2 - margin * 2;       // width of the inner box
    const innerW = boxW - 2;                  // content width (minus 2 border chars)
    const startR = this._inputBoxRow();

    const borderColor = this._pulse ? chalk.yellow : chalk.dim.yellow;
    const topBorder   = borderColor(B.tl + B.row(innerW) + B.tr);
    const botBorder   = borderColor(B.bl + B.row(innerW) + B.br);
    const side        = borderColor(B.v);

    // Build input line content
    const prompt  = chalk.bold.white("  › ");
    const cursor  = chalk.bgYellow.black(" ");
    const display = this._buf
      ? chalk.white(this._buf) + cursor
      : chalk.dim("What do you want to work on?") + D(" ") + cursor;
    const inputLine = prompt + display;
    const inputPad  = " ".repeat(Math.max(0, innerW - visLen(inputLine)));
    const emptyLine = " ".repeat(innerW);

    process.stdout.write(
      H.pos(startR, 1)     + Y(B.v) + " ".repeat(margin) + topBorder + " ".repeat(margin) + Y(B.v) +
      H.pos(startR + 1, 1) + Y(B.v) + " ".repeat(margin) + side + inputLine + inputPad + side + " ".repeat(margin) + Y(B.v) +
      H.pos(startR + 2, 1) + Y(B.v) + " ".repeat(margin) + side + emptyLine + side + " ".repeat(margin) + Y(B.v) +
      H.pos(startR + 3, 1) + Y(B.v) + " ".repeat(margin) + botBorder + " ".repeat(margin) + Y(B.v) +
      H.pos(startR + 4, 1) + Y(B.v) + " ".repeat(margin) + D("Type anything to start  ·  ") + C("/help") + D(" for commands") + " ".repeat(margin + 10) + Y(B.v),
    );
  }

  // ── Info sections ────────────────────────────────────────────────

  private _renderInfo(): void {
    const c      = this.cols;
    const startR = this._inputBoxRow() + 6;  // 2 gap rows after input hint
    const inner  = c - 2;
    const half   = Math.floor(inner / 2);

    const { owlEmoji, owlName, generation, challenge, provider, model, skills } = this.opts;

    // Section headers
    const leftHdr  = YB("[ Active Owl ]");
    const rightHdr = YB("[ Environment ]");
    const hdrRow   = pad(leftHdr, half) + rightHdr;

    // Owl info
    const owl1 = Y("◉ ") + W(`${owlEmoji} ${owlName}`) + D(` gen${generation} ⚡${challenge}`);
    const env1 = D("⌬ ") + W(process.platform === "darwin" ? "macOS" : process.platform);
    const row1 = pad(owl1, half) + env1;

    const owl2 = D("  Provider : ") + W(provider);
    const env2 = D("🔌 MCP   : ") + W("active");
    const row2 = pad(owl2, half) + env2;

    const owl3 = D("  Model   : ") + W(model.replace("claude-", "").slice(0, 14));
    const env3 = D("📦 Skills : ") + W(`${skills} loaded`);
    const row3 = pad(owl3, half) + env3;

    const lv = Y(B.v);
    process.stdout.write(
      H.pos(startR,     1) + lv + pad(hdrRow, inner) + lv +
      H.pos(startR + 1, 1) + lv + pad(row1,   inner) + lv +
      H.pos(startR + 2, 1) + lv + pad(row2,   inner) + lv +
      H.pos(startR + 3, 1) + lv + pad(row3,   inner) + lv,
    );
  }

  // ── Recent sessions ─────────────────────────────────────────────

  private _renderSessions(): void {
    const c      = this.cols;
    const inner  = c - 2;
    const startR = this._inputBoxRow() + 11;
    const lv     = Y(B.v);
    const { recentSessions: sessions } = this.opts;

    process.stdout.write(
      H.pos(startR, 1) + lv + pad(YB("[ Recent Sessions ]"), inner) + lv,
    );

    if (sessions.length === 0) {
      process.stdout.write(
        H.pos(startR + 1, 1) + lv + pad(D("  No sessions yet."), inner) + lv,
      );
    } else {
      sessions.slice(0, 3).forEach((s, i) => {
        const turnsStr = D(`${s.turns} turns`);
        const titleStr = Y("→ ") + D(`${s.ago.padEnd(4)} `) + W(s.title.slice(0, inner - 22));
        const padded   = pad(titleStr, inner - 10) + turnsStr;
        process.stdout.write(
          H.pos(startR + 1 + i, 1) + lv + " " + pad(padded, inner - 1) + lv,
        );
      });
    }
  }

  // ── Shortcuts bar ────────────────────────────────────────────────

  private _renderShortcuts(): void {
    const c      = this.cols;
    const r      = this.rows;
    const inner  = c - 2;
    const lv     = Y(B.v);

    // Divider row
    process.stdout.write(
      H.pos(r - 2, 1) + lv + D(" " + B.row(inner - 2) + " ") + lv,
    );

    const shortcuts =
      C("[/help]") + D(" Commands   ") +
      C("[/owls]") + D(" Owls   ") +
      C("[↑↓]")   + D(" History   ") +
      C("[^C]")   + D(" Quit");

    process.stdout.write(
      H.pos(r - 1, 1) + lv + " " + pad(shortcuts, inner - 2) + lv,
    );
  }
}
