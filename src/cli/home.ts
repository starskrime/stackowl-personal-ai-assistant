/**
 * StackOwl — Home Screen  (Screen 1)
 *
 * Layout matches Screen 2 (TerminalUI) — same pixel-shadow design.
 * Left panel: owl identity info.
 * Right panel: empty conversation area with centered input (initially).
 * After first command → input moves to bottom (standard Screen 2 position).
 */

import { EventEmitter } from "node:events";
import chalk from "chalk";

// ─── ANSI helpers ──────────────────────────────────────────────

const E = "\x1B";
const H = {
  altIn: `${E}[?1049h`,
  altOut: `${E}[?1049l`,
  hide: `${E}[?25l`,
  show: `${E}[?25h`,
  clear: `${E}[2J\x1B[1;1H`,
  pos: (r: number, c = 1) => `${E}[${r};${c}H`,
  el: `${E}[2K`,
};

// ─── Color palette — Neon Accent ─────────────────────────────────

const AMBER  = chalk.rgb(250, 179, 135);
const BLUE   = chalk.rgb(137, 180, 250);
const GREEN  = chalk.rgb(166, 227, 161);
const W      = chalk.rgb(205, 214, 244);
const LBL    = chalk.rgb(69, 71, 90);
const MUT    = chalk.rgb(46, 46, 69);

const PANEL_BG   = chalk.bgRgb(12, 12, 24);
const CONTENT_BG = chalk.bgRgb(8, 8, 16);

// ─── Frame + panel constants ──────────────────────────────────────

const FRAME_V = CONTENT_BG(" ");
const FRAME_H = CONTENT_BG(" ");
const PANEL_V = AMBER(" │ "); // panel separator — amber, visible on any dark background

const DIV = "━";

// ─── Helpers ───────────────────────────────────────────────────

function stripAnsi(s: string): string {
  return s.replace(/\x1B\[[0-9;]*[A-Za-z]/g, "");
}

function visLen(s: string): number {
  const plain = stripAnsi(s);
  let len = 0;
  for (const ch of plain) {
    const cp = ch.codePointAt(0) ?? 0;
    len += cp > 0xffff ? 2 : 1;
  }
  return len;
}

function padR(s: string, w: number): string {
  return s + " ".repeat(Math.max(0, w - visLen(s)));
}

function trunc(s: string, max: number): string {
  const plain = stripAnsi(s);
  return plain.length > max ? plain.slice(0, max - 1) + "…" : s;
}

// ─── Types ────────────────────────────────────────────────────────

export interface HomeOpts {
  owlEmoji: string;
  owlName: string;
  generation: number;
  challenge: number;
  provider: string;
  model: string;
  skills: number;
  recentSessions: Array<{ title: string; turns: number; ago: string }>;
}

// ─── HomeScreen ──────────────────────────────────────────────────

export class HomeScreen extends EventEmitter {
  private _pulse = false;
  private _timer: ReturnType<typeof setInterval> | null = null;
  private _buf = "";

  private _rendering = false;
  private _renderPending = false;
  private _resizeTimer: ReturnType<typeof setTimeout> | null = null;
  private _closed = false;
  private _shuttingDown = false;

  constructor(private opts: HomeOpts) {
    super();
  }

  // ─── Dimensions ────────────────────────────────────────────────

  private get cols() {
    return Math.max(process.stdout.columns ?? 80, 60);
  }
  private get rows() {
    return Math.max(process.stdout.rows ?? 24, 20);
  }

  private get leftW(): number {
    return Math.max(32, Math.floor((this.cols - 4) * 0.38));
  }
  private get rightW(): number {
    return this.cols - 4 - this.leftW;
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  enter(): void {
    process.stdout.write(H.altIn + H.hide);
    if (process.stdin.isTTY) process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding("utf8");

    process.stdin.on("data", this._onKey);
    process.stdout.on("resize", this._onResize);

    setTimeout(() => {
      this._renderQueued();
      this._timer = setInterval(() => {
        this._pulse = !this._pulse;
        this._renderInputBoxQueued();
      }, 800);
    }, 40);
  }

  transition(): void {
    this._closed = true;
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    process.stdin.off("data", this._onKey);
    process.stdout.off("resize", this._onResize);
  }

  close(): void {
    if (this._shuttingDown) return;
    this._shuttingDown = true;
    this._closed = true;
    while (this._rendering) {
      // wait for in-progress render to finish
    }
    this.transition();
    process.stdout.write(H.show + H.altOut);
    if (process.stdin.isTTY) {
      try {
        process.stdin.setRawMode(false);
      } catch {
        /**/
      }
    }
  }

  // ─── Key handler ───────────────────────────────────────────────

  private _onKey = (chunk: unknown): void => {
    const key =
      typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");

    if (key === "\x03" || key === "\x04") {
      this.emit("quit");
      return;
    }

    if (key === "\r" || key === "\n") {
      if (this._buf.length > 0) {
        const payload = this._buf;
        this._buf = "";
        this._renderInputBoxQueued();
        this.emit("activate", payload);
      }
      return;
    }

    if (key === "\x7f") {
      if (this._buf.length > 0) {
        this._buf = this._buf.slice(0, -1);
        this._renderInputBoxQueued();
      }
      return;
    }

    if (key.length >= 1 && key >= " ") {
      this._buf += key;
      this._renderInputBoxQueued();
    }
  };

  private _onResize = () => {
    if (this._resizeTimer) clearTimeout(this._resizeTimer);
    this._resizeTimer = setTimeout(() => {
      this._resizeTimer = null;
      this._renderQueued();
    }, 100);
  };

  // ─── Render queue ──────────────────────────────────────────────

  private _renderQueued(): void {
    if (this._closed) return;
    if (this._renderPending) return;
    this._renderPending = true;
    setImmediate(() => {
      if (this._closed) return;
      this._renderPending = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        this._render();
      } finally {
        this._rendering = false;
      }
    });
  }

  private _renderInputBoxQueued(): void {
    if (this._closed) return;
    if (this._renderPending) return;
    this._renderPending = true;
    setImmediate(() => {
      if (this._closed) return;
      this._renderPending = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        this._renderInputBox();
      } finally {
        this._rendering = false;
      }
    });
  }

  // ─── Full render ───────────────────────────────────────────────

  private _render(): void {
    const c = this.cols;
    const r = this.rows;

    let out = "";

    out += H.clear + H.pos(1);

    // Frame (outer border - shadow blocks)
    out += H.pos(1, 1) + FRAME_H.repeat(c);
    for (let row = 2; row < r; row++) {
      out += H.pos(row, 1) + FRAME_V;
      out += H.pos(row, c) + FRAME_V;
    }
    out += H.pos(r, 1) + FRAME_H.repeat(c);

    // Top bar
    out += this._buildTopBar();

    // Body: left panel + right panel (conversation + centered input)
    out += this._buildBody();

    // Shortcuts bar
    out += this._buildShortcuts();

    process.stdout.write(out);
  }

  // ─── Top bar ───────────────────────────────────────────────────

  private _buildTopBar(): string {
    const c = this.cols;
    const inner = c - 2;
    const { owlName, generation, challenge, skills } = this.opts;

    const leftBadge = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(" ◈ STACKOWL ");
    const rightBadge = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(
      " " + this.opts.owlEmoji + " " + owlName + " "
    );
    const meta =
      " " + MUT("[") + BLUE(this.opts.model.replace("claude-", "").slice(0, 14)) + MUT("]") +
      " " + MUT("·") + " " + LBL("gen" + generation) +
      " " + MUT("·") + " " + AMBER("⚡" + challenge) +
      " " + MUT("·") + " " + GREEN("📦" + skills + " skills");

    const leftLen  = visLen(leftBadge);
    const rightLen = visLen(rightBadge + meta);
    const gap = Math.max(2, inner - leftLen - rightLen);

    const row2 = leftBadge + " ".repeat(gap) + rightBadge + meta;
    let out = "";
    out += H.pos(2, 2) + PANEL_BG(padR(row2, inner));
    out += H.pos(3, 2) + PANEL_BG(AMBER(DIV.repeat(inner)));
    return out;
  }

  // ─── Body: left panel + right panel ───────────────────────────

  private _buildBody(): string {
    const r = this.rows;

    // Body rows: 4 through r-5 (input panel rows r-4 to r-2, shortcuts r-1)
    const bodyRows = r - 7;

    const lW = this.leftW;
    const rW = this.rightW;

    const leftLines = this._buildLeft(lW, bodyRows);
    const rightLines = this._buildRight(rW, bodyRows);

    let out = "";

    for (let i = 0; i < bodyRows; i++) {
      const row = 3 + i;
      const lLn = leftLines[i] ?? { t: "", v: 0 };
      const rLn = rightLines[i] ?? { t: "", v: 0 };
      const lPad = " ".repeat(Math.max(0, lW - lLn.v));
      const rPad = " ".repeat(Math.max(0, rW - rLn.v));

      out += H.pos(row, 2) + lLn.t + lPad;
      out += H.pos(row, lW + 2) + PANEL_V;
      out += H.pos(row, lW + 3) + rLn.t + rPad;
    }

    return out;
  }

  // ─── Left panel (owl identity) ───────────────────────────────

  private _buildLeft(
    w: number,
    rows: number,
  ): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add   = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    const secHdr = (label: string) => {
      const line = MUT("─".repeat(Math.max(0, w - label.length - 5)));
      return "  " + AMBER.bold(label) + " " + line;
    };

    const { owlEmoji, owlName, generation, challenge, provider, model, skills } =
      this.opts;

    blank();
    add("  " + chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(" " + owlEmoji + " " + owlName + " "));
    blank();
    add(secHdr("IDENTITY"));
    add("  " + LBL("Generation") + "  " + W(String(generation)));
    add("  " + LBL("Challenge ") + "  " + AMBER("⚡" + String(challenge)));
    blank();
    add(secHdr("BACKEND"));
    add("  " + LBL("Provider") + "   " + BLUE(provider));
    add("  " + LBL("Model   ") + "   " + W(model.replace("claude-", "").slice(0, 14)));
    add("  " + LBL("Skills  ") + "   " + GREEN(String(skills) + " loaded"));

    while (lines.length < rows) blank();
    return lines.slice(0, rows);
  }

  // ─── Right panel (conversation + centered input) ──────────────

  private _buildRight(
    w: number,
    rows: number,
  ): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add   = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    // Center row for the input prompt
    const centerRow = Math.floor(rows / 2) - 1;

    for (let i = 0; i < centerRow; i++) blank();

    // Centered prompt label
    const labelText = "What do you want to work on?";
    const labelPad  = Math.max(0, Math.floor((w - labelText.length) / 2));
    add(" ".repeat(labelPad) + LBL(labelText));
    blank(); // input box rendered separately by _renderInputBox

    // Recent sessions — show up to 3
    const sessions = this.opts.recentSessions.slice(0, 3);
    if (sessions.length > 0) {
      blank();
      add("  " + MUT("─".repeat(Math.max(0, w - 4))));
      add("  " + LBL("recent sessions"));
      blank();
      for (const s of sessions) {
        const title    = trunc(s.title, w - 24);
        const turns    = MUT(String(s.turns) + "t");
        const ago      = MUT(s.ago);
        const spacer   = " ".repeat(
          Math.max(1, w - 2 - visLen(title) - visLen(String(s.turns) + "t") - visLen(s.ago) - 4),
        );
        add("  " + W(title) + spacer + turns + "  " + ago);
      }
    }

    while (lines.length < rows) blank();
    return lines.slice(0, rows);
  }

  // ─── Shortcuts bar ────────────────────────────────────────────────

  private _buildShortcuts(): string {
    const c = this.cols;
    const r = this.rows;
    const inner = c - 4;

    const key = (k: string) =>
      chalk.bgRgb(26, 26, 44).rgb(205, 214, 244).bold(` ${k} `);

    const line =
      key("ESC") + LBL("  Stop     ") +
      key("^P")  + LBL("  Parliament     ") +
      key("^L")  + LBL("  Clear     ") +
      key("^C")  + LBL("  Quit");

    return H.pos(r - 1, 3) + PANEL_BG(padR(line, inner));
  }

  // ─── Input box render (800ms pulse updates) ─────────────────────

  private _renderInputBox(): void {
    const r = this.rows;
    const bodyRows    = r - 7;
    const inputCenterRow = Math.floor(bodyRows / 2);

    const lW = this.leftW;
    const rW = this.rightW;

    const cursor = chalk.bgYellow.black(" ");
    let contentLine: string;

    if (this._buf.length > 0) {
      contentLine = AMBER("  › ") + W(this._buf) + cursor;
    } else {
      contentLine = AMBER("  › ") + LBL("Ask anything or type / for commands") + W(" ") + cursor;
    }

    const contentLen = visLen(contentLine);
    const rowPad = " ".repeat(Math.max(0, rW - contentLen));
    const row    = 3 + inputCenterRow;

    // Amber top/bottom border, panel-bg content
    process.stdout.write(
      H.pos(row - 1, lW + 3) + PANEL_BG(AMBER("▔".repeat(rW))) +
      H.pos(row,     lW + 3) + PANEL_BG(contentLine + rowPad)   +
      H.pos(row + 1, lW + 3) + PANEL_BG(AMBER("▁".repeat(rW))),
    );
  }
}
