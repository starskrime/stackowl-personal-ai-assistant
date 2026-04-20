/**
 * StackOwl — Active Session UI  (Screen 2)
 *
 * Dark Glass design:
 *   - Thin borders with ASCII-safe chars (+-+|+-+)
 *   - Left panel with subtle dark tint + 1-char gap from right panel
 *   - Yellow only for border structure; white for content; cyan/green for interactive
 *   - Panels defined by spacing and background, not heavy borders
 *   - Clean section dividers with thin --- rules
 */

import { EventEmitter } from "node:events";
import chalk from "chalk";
import type { StreamEvent } from "../providers/base.js";

// ─── ANSI helpers ────────────────────────────────────────────────

const ESC = "\x1B";
const ansi = {
  altIn: `${ESC}[?1049h`,
  altOut: `${ESC}[?1049l`,
  hide: `${ESC}[?25l`,
  show: `${ESC}[?25h`,
  clear: `${ESC}[2J\x1B[1;1H`, // erase screen + home cursor (no scrollback clear — avoids flicker)
  el: `${ESC}[2K`,
  pos: (r: number, c = 1) => `${ESC}[${r};${c}H`,
};

// ─── Color shortcuts ─────────────────────────────────────────────
// Dark Glass palette:
//   Y  = border/structure (used sparingly)
//   C  = interactive/active
//   G  = success/done
//   R  = error
//   D  = dim/secondary
//   W  = primary content
//   Wb = bold white (headings)

const Y = chalk.yellow;
const D = chalk.dim;
const W = chalk.white;
const Wb = chalk.white.bold;
const G = chalk.green;
const R = chalk.red;
const C = chalk.cyan;

// Panel tint backgrounds (subtle dark glass effect)
const TOP_BG = chalk.bgBlack.rgb(15, 15, 18);

// ─── Box drawing (thin, elegant, ASCII-safe) ────────────────────
// Using standard ASCII for reliable parsing:
//   + top-left   - horizontal   + top-right
//   | vertical   + cross        | vertical
//   + bot-left   - horizontal   + bot-right

const B = {
  tl: "+",
  tr: "+",
  bl: "+",
  br: "+",
  h: "-",
  v: "|",
  mt: "+",
  mb: "+",
  ml: "+",
  mr: "+",
};

// Thin divider char
const DIV = "-";

// ─── Constants ───────────────────────────────────────────────────

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const OWL_FACES = {
  idle: " ( o  o ) ",
  thinking: [" (-_-)  ", " (o_-)  ", " (-_o)  ", " (o_o)  "],
  done: " ( ^‿^ ) ",
  error: " ( >_< ) ",
};

type OwlState = "idle" | "thinking" | "done" | "error";

interface ToolEntry {
  name: string;
  args: string;
  status: "running" | "done" | "error";
  summary?: string;
  ms?: number;
}

interface DNA {
  challenge: number;
  verbosity: number;
  mood: number;
}

// ─── Helpers ─────────────────────────────────────────────────────

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
  return plain.length > max ? plain.slice(0, max - 1) + "..." : s;
}

// ─── TerminalUI ─────────────────────────────────────────────────

export class TerminalUI extends EventEmitter {
  sessionId = "";

  private owlEmoji = "🦉";
  private owlName = "Owl";
  private owlModel = "";

  private _turn = 0;
  private _tokens = 0;
  private _cost = 0;

  private _owlState: OwlState = "idle";
  private _faceIdx = 0;
  private _spinIdx = 0;
  private _toolCalls: ToolEntry[] = [];
  private _instincts = 0;
  private _memFacts = 0;
  private _skillsHit = 0;

  private _dna: DNA = { challenge: 5, verbosity: 5, mood: 7 };

  private _lines: string[] = [];
  private _scrollOff = 0;

  private _inputBuf = "";
  private _inputCursor = 0;
  private _inputLocked = false;
  private _inputMasked = false;
  private _allowEmptyInput = false;
  private _history: string[] = [];
  private _histIdx = -1;
  private _histTemp = "";

  private _streaming = false;
  private _streamBuf = "";
  private _streamHeaderIdx = -1;

  private _thinkTimer: ReturnType<typeof setInterval> | null = null;
  private _thinkStart = 0;

  /** Phase 1: Re-entrancy guard — prevents concurrent renders */
  private _rendering = false;
  /** Phase 2: Render queue — dedupes redundant renders, runs once per tick */
  private _renderQueued = false;
  /** Fix 1: Resize debounce */
  private _resizeTimer: ReturnType<typeof setTimeout> | null = null;

  // ─── Dimensions ────────────────────────────────────────────────

  private get cols() {
    return Math.max(process.stdout.columns ?? 100, 80);
  }
  private get rows() {
    return Math.max(process.stdout.rows ?? 30, 20);
  }

  private get leftW(): number {
    return Math.max(32, Math.floor((this.cols - 4) * 0.38));
  }
  private get rightW(): number {
    return this.cols - 4 - this.leftW;
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  private _keyHandler = (chunk: unknown): void => {
    const key =
      typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
    this._onKey(key);
  };

  private _resizeHandler = (): void => {
    if (this._resizeTimer) clearTimeout(this._resizeTimer);
    this._resizeTimer = setTimeout(() => {
      this._resizeTimer = null;
      this.redraw();
    }, 100);
  };

  enter(): void {
    process.stdout.write(ansi.altIn + ansi.hide);
    if (process.stdin.isTTY) process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding("utf8");

    process.stdout.on("resize", this._resizeHandler);
    process.stdin.on("data", this._keyHandler);

    setTimeout(() => this.redraw(), 40);
  }

  suspend(): void {
    this._stopThink();
    process.stdin.off("data", this._keyHandler);
    process.stdout.off("resize", this._resizeHandler);
  }

  close(): void {
    this.suspend();
    process.stdout.write(ansi.show + ansi.altOut);
    if (process.stdin.isTTY) {
      try {
        process.stdin.setRawMode(false);
      } catch {
        /**/
      }
    }
  }

  feedChar(ch: string): void {
    this._onKey(ch);
  }

  // ─── Owl identity ──────────────────────────────────────────────

  setOwl(
    emoji: string,
    name: string,
    _provider?: string,
    model?: string,
  ): void {
    this.owlEmoji = emoji;
    this.owlName = name;
    this.owlModel = model ?? "";
  }

  updateDNA(d: Partial<DNA>): void {
    Object.assign(this._dna, d);
    this._renderBody();
  }

  updateStats(tokens: number, cost: number): void {
    this._tokens = tokens;
    this._cost = cost;
    this._renderTopBar();
  }

  setMasked(on: boolean): void {
    this._inputMasked = on;
    this._renderInput();
  }

  setAllowEmptyInput(on: boolean): void {
    this._allowEmptyInput = on;
  }

  // ─── Public output API ─────────────────────────────────────────

  showThinking(): void {
    this._inputLocked = true;
    this._owlState = "thinking";
    this._thinkStart = Date.now();
    this._spinIdx = 0;
    this._faceIdx = 0;
    this._stopThink();
    this._thinkTimer = setInterval(() => {
      this._spinIdx++;
      if (this._spinIdx % 6 === 0) this._faceIdx++;
      // Queue render via the render system (deduplicated, one per tick)
      this._renderBodyQueued();
    }, 100);
  }

  /** Direct queue — used by thinking timer to update face/spin without full render */
  private _renderBodyQueued(): void {
    if (this._rendering) return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        this._doBuildBody();
      } finally {
        this._rendering = false;
      }
    });
  }

  stopThinking(): void {
    this._stopThink();
    this._owlState = "idle";
    this._renderBody();
  }

  showToolCall(name: string): void {
    this._owlState = "thinking";
    const [tool, ...rest] = name.split(" ");
    this._toolCalls.push({
      name: tool,
      args: rest.join(" "),
      status: "running",
    });
    if (this._toolCalls.length > 12) this._toolCalls.shift();
    this._renderBody();
  }

  completeToolCall(): void {
    const last =
      this._toolCalls.findLast?.((t) => t.status === "running") ??
      this._toolCalls.filter((t) => t.status === "running").at(-1);
    if (last) {
      last.status = "done";
      last.ms = Date.now() - this._thinkStart;
    }
    this._renderBody();
  }

  printResponse(emoji: string, name: string, content: string): void {
    this._stopThink();
    this._owlState = "done";
    this._turn++;
    this._pushLine(Wb("  " + emoji + " " + name) + D(":"));
    for (const l of this._wrapText(content, this.rightW - 4)) {
      this._pushLine("  " + W(l));
    }
    this._pushLine("");
    this._inputLocked = false;
    this._renderBody();
  }

  printError(msg: string): void {
    this._stopThink();
    this._owlState = "error";
    this._pushLine("  " + R("x ") + R(msg));
    this._pushLine("");
    this._inputLocked = false;
    this._renderBody();
  }

  printInfo(msg: string): void {
    this._pushLine("  " + D(msg));
    this._renderBody();
  }

  printLines(lines: string[]): void {
    for (const l of lines) {
      this._pushLine(l === "" ? "" : "  " + l);
    }
    this._renderBody();
  }

  showPrompt(): void {
    this._renderBody();
  }

  // ─── Stream handler ───────────────────────────────────────────

  createStreamHandler(): {
    handler: (event: StreamEvent) => Promise<void>;
    didStream: () => boolean;
  } {
    let streamed = false;
    const handler = async (ev: StreamEvent) => {
      switch (ev.type) {
        case "text_delta": {
          const chunk = ev.content.replace(/\[DONE\]/g, "");
          if (!chunk) break;
          this._stopThink();
          this._owlState = "done";
          if (!this._streaming) {
            this._streaming = true;
            this._streamBuf = "";
            this._streamHeaderIdx = this._lines.length;
            this._turn++;
            this._pushLine(
              Wb("  " + this.owlEmoji + " " + this.owlName) + D(":"),
            );
          }
          this._streamBuf += chunk;
          this._lines.splice(this._streamHeaderIdx + 1);
          for (const l of this._wrapText(this._streamBuf, this.rightW - 4)) {
            this._lines.push("  " + W(l));
          }
          this._renderBody();
          streamed = true;
          break;
        }
        case "tool_start":
          this.stopThinking();
          this.showToolCall(ev.toolName);
          break;
        case "tool_end":
          this.completeToolCall();
          break;
        case "done":
          this._stopThink(); // stop thinking timer immediately
          this._owlState = "idle";
          this._streaming = false;
          this._streamBuf = "";
          this._streamHeaderIdx = -1;
          this._pushLine("");
          this._inputLocked = false;
          this._renderBody();
          break;
      }
    };
    return { handler, didStream: () => streamed };
  }

  // ─── Key handler ───────────────────────────────────────────────

  private _onKey(data: string): void {
    if (data === "\x03" || data === "\x04") {
      this.emit("quit");
      return;
    }

    if (data === "\r" || data === "\n") {
      if (this._inputLocked) return;
      const line = this._inputBuf.trim();
      this._inputBuf = "";
      this._inputCursor = 0;
      this._histIdx = -1;
      if (line) {
        this._history.unshift(line);
        if (this._history.length > 100) this._history.pop();
        this._turn++;
        this._pushLine(D("  You:"));
        const echo = this._inputMasked
          ? D("  " + "*".repeat(Math.min(line.length, 24)))
          : "  " + W(line);
        this._pushLine(echo);
        this._pushLine("");
        this._inputMasked = false;
        this.emit("line", line);
      } else if (this._allowEmptyInput) {
        this.emit("line", "");
      }
      this._renderBody();
      return;
    }

    if (data === "\x7f") {
      if (this._inputCursor > 0) {
        this._inputBuf =
          this._inputBuf.slice(0, this._inputCursor - 1) +
          this._inputBuf.slice(this._inputCursor);
        this._inputCursor--;
        this._renderInput();
      }
      return;
    }

    if (data === ESC + "[A") {
      if (this._histIdx === -1) this._histTemp = this._inputBuf;
      if (this._histIdx < this._history.length - 1) {
        this._histIdx++;
        this._inputBuf = this._history[this._histIdx];
        this._inputCursor = this._inputBuf.length;
        this._renderInput();
      }
      return;
    }
    if (data === ESC + "[B") {
      if (this._histIdx > -1) {
        this._histIdx--;
        this._inputBuf =
          this._histIdx === -1 ? this._histTemp : this._history[this._histIdx];
        this._inputCursor = this._inputBuf.length;
        this._renderInput();
      }
      return;
    }
    if (data === ESC + "[D" && this._inputCursor > 0) {
      this._inputCursor--;
      this._renderInput();
      return;
    }
    if (data === ESC + "[C" && this._inputCursor < this._inputBuf.length) {
      this._inputCursor++;
      this._renderInput();
      return;
    }

    if (data === ESC + "[5~") {
      this._scrollOff = Math.min(
        this._scrollOff + 5,
        Math.max(0, this._lines.length - this._convRows()),
      );
      this._renderBody();
      return;
    }
    if (data === ESC + "[6~") {
      this._scrollOff = Math.max(0, this._scrollOff - 5);
      this._renderBody();
      return;
    }

    if (data === "\x0C") {
      this._lines = [];
      this._toolCalls = [];
      this._scrollOff = 0;
      this._renderBody();
      return;
    }

    if (data === "/") {
      this._inputBuf = "/";
      this._inputCursor = 1;
      this._renderInput();
      return;
    }

    if (data.length >= 1 && data >= " ") {
      this._inputBuf =
        this._inputBuf.slice(0, this._inputCursor) +
        data +
        this._inputBuf.slice(this._inputCursor);
      this._inputCursor += data.length;
      this._renderInput();
    }
  }

  // ─── Full redraw ───────────────────────────────────────────────

  /**
   * Request a full redraw on the next event loop tick.
   * All writes are batched into a single process.stdout.write() call —
   * atomic relative to the event loop, no interleaving possible.
   */
  redraw(): void {
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        const out =
          ansi.clear +
          this._buildFrame() +
          this._buildTopBar() +
          this._doBuildBody() +
          this._buildShortcuts();
        process.stdout.write(out);
      } finally {
        this._rendering = false;
      }
    });
  }

  // ─── Frame ────────────────────────────────────────────────────

  /**
   * Build the full frame as a string — top border, cleared inner rows,
   * and bottom border. All rows explicitly written to prevent ghost content.
   */
  private _buildFrame(): string {
    const c = this.cols;
    const r = this.rows;
    let out = ansi.pos(1) + Y(B.tl + B.h.repeat(c - 2) + B.tr);
    const rowInner = " ".repeat(c - 2);
    for (let i = 2; i < r; i++) {
      out += ansi.pos(i) + Y(B.v) + rowInner + Y(B.v);
    }
    out += ansi.pos(r) + Y(B.bl + B.h.repeat(c - 2) + B.br);
    return out;
  }

  // ─── Top bar ───────────────────────────────────────────────────

  private _buildTopBar(): string {
    const c = this.cols;
    const bar = this._buildTopBarContent(c - 2);
    let out = ansi.pos(2) + Y(B.v) + TOP_BG(W(" " + bar) + " ") + Y(B.v);
    out += ansi.pos(3) + Y(B.v + DIV.repeat(c - 2) + B.v);
    return out;
  }

  private _renderTopBar(): void {
    // Deprecated — use _buildTopBar() in redraw()
  }

  private _buildTopBarContent(_innerW?: number): string {
    const name = Wb(this.owlEmoji + " " + this.owlName);
    const model = this.owlModel
      ? D(" . ") + C(this.owlModel.replace("claude-", "").slice(0, 18))
      : "";
    const turn = this._turn > 0 ? D(" . ") + W("turn " + this._turn) : "";
    const tokens =
      this._tokens > 0
        ? D(" . ") + W((this._tokens / 1000).toFixed(1) + "k tokens")
        : "";
    const cost = this._cost > 0 ? D(" . $") + W(this._cost.toFixed(3)) : "";

    return name + model + turn + tokens + cost;
  }

  // ─── Body ──────────────────────────────────────────────────────

  /**
   * Queue a body render on the next tick.
   * Deduplicates concurrent callers; writes are batched into a single
   * process.stdout.write() call for atomic rendering.
   */
  private _renderBody(): void {
    if (this._rendering) return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        process.stdout.write(this._doBuildBody());
      } finally {
        this._rendering = false;
      }
    });
  }

  private _doBuildBody(): string {
    const c = this.cols;
    const lW = this.leftW;
    const rW = this.rightW;
    const bodyRows = this.rows - 6;

    const leftLines = this._buildLeft(lW, bodyRows);
    const rightLines = this._buildRight(rW, bodyRows);

    let out = "";
    for (let i = 0; i < bodyRows; i++) {
      const row = 4 + i;
      const lLine = leftLines[i] ?? { t: "", v: 0 };
      const rLine = rightLines[i] ?? { t: "", v: 0 };
      const lPad = " ".repeat(Math.max(0, lW - lLine.v));
      const rPad = " ".repeat(Math.max(0, rW - rLine.v));

      out += ansi.pos(row, 1) + Y(B.v);
      out += ansi.pos(row, 2) + lLine.t + lPad;
      out += ansi.pos(row, lW + 3) + D(":");
      out += ansi.pos(row, lW + 5) + rLine.t + rPad;
      out += ansi.pos(row, c) + Y(B.v);
    }

    return out;
  }

  private _renderInput(): void {
    if (this._rendering) return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        this._doRenderInput();
      } finally {
        this._rendering = false;
      }
    });
  }

  private _doRenderInput(): void {
    const rW = this.rightW;
    const bodyRows = this.rows - 6;
    const inputRow = 4 + bodyRows - 1;

    const line = this._buildInputLine(rW);
    const rPad = " ".repeat(Math.max(0, rW - line.v));

    process.stdout.write(ansi.pos(inputRow, this.leftW + 5) + line.t + rPad);
  }

  // ─── Left panel ───────────────────────────────────────────────

  private _buildLeft(w: number, rows: number): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    blank();
    add("  " + Y("+-- ") + Wb("OWL MIND") + Y(" --+"));
    blank();

    add("  " + Y(this._currentFace()));
    blank();

    add(
      "  " +
        C("*") +
        " " +
        D("Instincts") +
        "   " +
        (this._instincts > 0 ? W(this._instincts + " triggered") : D("none")),
    );
    add(
      "  " +
        C("*") +
        " " +
        D("Memory   ") +
        "   " +
        (this._memFacts > 0 ? W(this._memFacts + " facts") : D("none")),
    );
    add(
      "  " +
        C("*") +
        " " +
        D("Skills   ") +
        "   " +
        (this._skillsHit > 0 ? W(this._skillsHit + " invoked") : D("none")),
    );
    blank();

    if (this._toolCalls.length > 0) {
      const divLen = Math.max(0, w - 14);
      add("  " + D("REASONING") + " " + D(DIV.repeat(divLen)));
      const visible = this._toolCalls.slice(-8);
      visible.forEach((tc, i) => {
        const isLast = i === visible.length - 1;
        const branch = isLast ? "   L " : "   + ";
        const spinner = SPINNER[this._spinIdx % SPINNER.length];
        const icon =
          tc.status === "running"
            ? C(spinner)
            : tc.status === "done"
              ? G("Y")
              : R("X");
        const name = trunc(tc.name, w - 18);
        const ms = tc.ms ? D(" " + tc.ms + "ms") : "";
        add(branch + icon + " " + W(name) + ms);
        if (tc.summary) {
          const indent = isLast ? "        " : "   |    ";
          add(indent + D(trunc(tc.summary, w - 12)));
        }
      });
      blank();
    }

    const dnaStartIdx = lines.length;
    const dnaRows = rows - dnaStartIdx - 2;

    if (dnaRows > 4) {
      blank();
      add("  " + D("DNA" + " " + DIV.repeat(Math.max(0, w - 8))));
      blank();
      add("  " + this._dnaBar("challenge", this._dna.challenge));
      add("  " + this._dnaBar("verbosity", this._dna.verbosity));
      add("  " + this._dnaBar("mood     ", this._dna.mood));
    }

    while (lines.length < rows - 1) blank();
    add("  " + D(DIV.repeat(Math.max(0, w - 4))) + " FIREWALL");

    return lines.slice(0, rows);
  }

  // ─── Right panel ─────────────────────────────────────────────

  private _convRows(): number {
    return this.rows - 8;
  }

  private _buildRight(
    w: number,
    rows: number,
  ): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const convRows = rows - 2;

    if (this._lines.length === 0) {
      const hint = "  " + D("What do you want to work on?");
      lines.push({ t: hint, v: visLen(hint) });
    } else {
      const total = this._lines.length;
      const end = Math.max(0, total - this._scrollOff);
      const start = Math.max(0, end - this._convRows());
      const visible = this._lines.slice(start, end);
      for (let i = 0; i < convRows; i++) {
        const l = visible[i] ?? "";
        lines.push({ t: l, v: visLen(l) });
      }
    }

    while (lines.length < convRows) lines.push({ t: "", v: 0 });

    const sep = "  " + D(DIV.repeat(w - 4));
    lines.push({ t: sep, v: visLen(sep) });

    lines.push(this._buildInputLine(w));

    return lines.slice(0, rows);
  }

  private _buildInputLine(_w?: number): { t: string; v: number } {
    if (this._inputLocked) {
      const spin = C(SPINNER[this._spinIdx % SPINNER.length]);
      const t = "  " + spin + D("  thinking...");
      return { t, v: visLen(t) };
    }
    const prefix = "  " + C("> ") + Wb("");
    let before: string, atCur: string, after: string;
    if (this._inputMasked) {
      before = "*".repeat(this._inputCursor);
      atCur = this._inputBuf[this._inputCursor] ? "*" : " ";
      after = "*".repeat(
        Math.max(0, this._inputBuf.length - this._inputCursor - 1),
      );
    } else {
      before = this._inputBuf.slice(0, this._inputCursor);
      atCur = this._inputBuf[this._inputCursor] ?? " ";
      after = this._inputBuf.slice(this._inputCursor + 1);
    }
    const display = W(before) + chalk.bgYellow.black(atCur) + W(after);
    const t = prefix + display;
    return { t, v: visLen(t) };
  }

  // ─── Shortcuts bar ────────────────────────────────────────────

  private _buildShortcuts(): string {
    const c = this.cols;
    const r = this.rows;
    const inner = c - 4;

    const line =
      C("[Esc]") +
      D("  Stop     ") +
      C("[^P]") +
      D("  Parliament     ") +
      C("[^L]") +
      D("  Clear     ") +
      C("[^C]") +
      D("  Quit");

    return ansi.pos(r - 1, 2) + padR(line, inner);
  }

  // ─── Owl face ─────────────────────────────────────────────────

  private _currentFace(): string {
    switch (this._owlState) {
      case "thinking": {
        const faces = OWL_FACES.thinking as string[];
        return faces[this._faceIdx % faces.length];
      }
      case "done":
        return OWL_FACES.done;
      case "error":
        return OWL_FACES.error;
      default:
        return OWL_FACES.idle;
    }
  }

  // ─── DNA bar ──────────────────────────────────────────────────

  private _dnaBar(label: string, val: number): string {
    const v = Math.max(0, Math.min(10, Math.round(val)));
    const filled = G("#").repeat(v);
    const empty = D(".").repeat(10 - v);
    return D(label) + " " + filled + empty + " " + W(String(val));
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private _pushLine(line: string): void {
    this._lines.push(line);
    if (this._lines.length > 5000) this._lines.shift();
    if (this._scrollOff > 0) this._scrollOff++;
  }

  private _stopThink(): void {
    if (this._thinkTimer) {
      clearInterval(this._thinkTimer);
      this._thinkTimer = null;
    }
  }

  private _wrapText(text: string, maxCols: number): string[] {
    const result: string[] = [];
    for (const para of text.split("\n")) {
      if (!para) {
        result.push("");
        continue;
      }
      let rem = para;
      while (rem.length > maxCols) {
        let bp = rem.lastIndexOf(" ", maxCols);
        if (bp < 0) bp = maxCols;
        result.push(rem.slice(0, bp));
        rem = rem.slice(bp).trimStart();
      }
      if (rem) result.push(rem);
    }
    return result;
  }
}
