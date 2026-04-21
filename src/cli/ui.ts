/**
 * StackOwl — Active Session UI  (Screen 2)
 *
 * Pixel-frame design — NO border lines.
 * Frame is defined by shadow blocks on all 4 sides:
 *   Row 1:  full-width shadow row
 *   Col 1:  shadow column (left edge)
 *   Col c:  shadow column (right edge)
 *   Row r:  full-width shadow row
 * Content lives in the inner area between shadows.
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
  clear: `${ESC}[2J\x1B[1;1H`,
  el: `${ESC}[2K`,
  pos: (r: number, c = 1) => `${ESC}[${r};${c}H`,
};

// ─── Color palette — Neon Accent ─────────────────────────────────

const AMBER  = chalk.rgb(250, 179, 135);   // primary accent
const BLUE   = chalk.rgb(137, 180, 250);   // secondary accent
const GREEN  = chalk.rgb(166, 227, 161);   // success / high mood
const PURPLE = chalk.rgb(203, 166, 247);   // metadata (turns, triggered)
const W      = chalk.rgb(205, 214, 244);   // primary text
const LBL    = chalk.rgb(69, 71, 90);      // labels / dim text
const MUT    = chalk.rgb(46, 46, 69);      // muted (borders, timings)
const R      = chalk.rgb(243, 139, 168);   // error

// Backgrounds
const PANEL_BG   = chalk.bgRgb(12, 12, 24);   // top bar / input zone bg
const CONTENT_BG = chalk.bgRgb(8, 8, 16);     // body panels bg

// ─── Frame + panel constants ──────────────────────────────────────

const FRAME_V = CONTENT_BG(" ");          // transparent frame cell (invisible)
const FRAME_H = CONTENT_BG(" ");          // transparent frame cell (invisible)
const PANEL_V = MUT(" │ ");               // panel separator — explicit muted color

const DIV = "━"; // heavy horizontal divider (U+2501)

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

  private _rendering = false; // re-entrancy guard
  private _renderQueued = false; // dedupe flag
  private _resizeTimer: ReturnType<typeof setTimeout> | null = null;
  private _closed = false; // prevents renders after close()

  // ─── Command popup ─────────────────────────────────────────────
  private _cmdPopupActive = false;
  private _cmdPopupMatches: string[] = [];
  private _cmdPopupIdx = 0;
  private _cmdNames: string[] = [];

  setCommandList(names: string[]): void {
    this._cmdNames = names;
  }

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
    this._closed = true;
    while (this._rendering) {
      // wait for in-progress render to finish
    }
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

  setInitialInput(buf: string): void {
    this._inputBuf = buf;
    this._inputCursor = buf.length;
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
    this.redraw();
  }

  updateStats(tokens: number, cost: number): void {
    this._tokens = tokens;
    this._cost = cost;
  }

  setMasked(on: boolean): void {
    this._inputMasked = on;
    this.redraw();
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
      this._renderBodyQueued();
    }, 100);
  }

  stopThinking(): void {
    this._stopThink();
    this._owlState = "idle";
    this.redraw();
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
    this.redraw();
  }

  completeToolCall(): void {
    const last =
      this._toolCalls.findLast?.((t) => t.status === "running") ??
      this._toolCalls.filter((t) => t.status === "running").at(-1);
    if (last) {
      last.status = "done";
      last.ms = Date.now() - this._thinkStart;
    }
    this.redraw();
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
    this.redraw();
  }

  printError(msg: string): void {
    this._stopThink();
    this._owlState = "error";
    this._pushLine("  " + R("x ") + R(msg));
    this._pushLine("");
    this._inputLocked = false;
    this.redraw();
  }

  printInfo(msg: string): void {
    this._pushLine("  " + D(msg));
    this.redraw();
  }

  printLines(lines: string[]): void {
    for (const l of lines) {
      this._pushLine(l === "" ? "" : "  " + l);
    }
    this.redraw();
  }

  showPrompt(): void {
    this.redraw();
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
          this.redraw();
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
          this._stopThink();
          this._owlState = "idle";
          this._streaming = false;
          this._streamBuf = "";
          this._streamHeaderIdx = -1;
          this._pushLine("");
          this._inputLocked = false;
          this.redraw();
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

    // ── Command popup active ───────────────────────────────────
    if (this._cmdPopupActive) {
      if (data === ESC + "[A") {
        // Arrow up
        this._cmdPopupIdx = Math.max(0, this._cmdPopupIdx - 1);
        this._renderCmdPopup();
        return;
      }
      if (data === ESC + "[B") {
        // Arrow down
        this._cmdPopupIdx = Math.min(
          this._cmdPopupMatches.length - 1,
          this._cmdPopupIdx + 1,
        );
        this._renderCmdPopup();
        return;
      }
      if (data === "\r" || data === "\n") {
        // Enter — select command
        const selected = this._cmdPopupMatches[this._cmdPopupIdx];
        if (selected) {
          this._inputBuf = "/" + selected;
          this._inputCursor = this._inputBuf.length;
        }
        this._cmdPopupActive = false;
        this.redraw();
        return;
      }
      if (data === ESC) {
        // Escape — dismiss popup
        this._inputBuf = "";
        this._inputCursor = 0;
        this._cmdPopupActive = false;
        this.redraw();
        return;
      }
      if (data === "\x7f") {
        // Backspace — remove last char after "/"
        if (this._inputBuf.length <= 1) {
          this._inputBuf = "";
          this._inputCursor = 0;
          this._cmdPopupActive = false;
          this.redraw();
        } else {
          this._inputBuf = this._inputBuf.slice(0, -1);
          this._inputCursor--;
          this._updatePopupMatches();
          this.redraw();
        }
        return;
      }
      if (data.length >= 1 && data >= " ") {
        // Any printable char — add to input, update filter
        this._inputBuf =
          this._inputBuf.slice(0, this._inputCursor) +
          data +
          this._inputBuf.slice(this._inputCursor);
        this._inputCursor += data.length;
        this._updatePopupMatches();
        this.redraw();
        return;
      }
      return;
    }

    // ── Normal input (popup not active) ────────────────────────

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
      this.redraw();
      return;
    }

    if (data === "\x7f") {
      if (this._inputCursor > 0) {
        this._inputBuf =
          this._inputBuf.slice(0, this._inputCursor - 1) +
          this._inputBuf.slice(this._inputCursor);
        this._inputCursor--;
        this.redraw();
      }
      return;
    }

    if (data === ESC + "[A") {
      if (this._histIdx === -1) this._histTemp = this._inputBuf;
      if (this._histIdx < this._history.length - 1) {
        this._histIdx++;
        this._inputBuf = this._history[this._histIdx];
        this._inputCursor = this._inputBuf.length;
        this.redraw();
      }
      return;
    }
    if (data === ESC + "[B") {
      if (this._histIdx > -1) {
        this._histIdx--;
        this._inputBuf =
          this._histIdx === -1 ? this._histTemp : this._history[this._histIdx];
        this._inputCursor = this._inputBuf.length;
        this.redraw();
      }
      return;
    }
    if (data === ESC + "[D" && this._inputCursor > 0) {
      this._inputCursor--;
      this.redraw();
      return;
    }
    if (data === ESC + "[C" && this._inputCursor < this._inputBuf.length) {
      this._inputCursor++;
      this.redraw();
      return;
    }

    if (data === ESC + "[5~") {
      this._scrollOff = Math.min(
        this._scrollOff + 5,
        Math.max(0, this._lines.length - this._convRows()),
      );
      this.redraw();
      return;
    }
    if (data === ESC + "[6~") {
      this._scrollOff = Math.max(0, this._scrollOff - 5);
      this.redraw();
      return;
    }

    if (data === "\x0C") {
      this._lines = [];
      this._toolCalls = [];
      this._scrollOff = 0;
      this.redraw();
      return;
    }

    if (data === "/") {
      this._inputBuf = "/";
      this._inputCursor = 1;
      this._cmdPopupActive = true;
      this._updatePopupMatches();
      this._cmdPopupIdx = 0;
      this.redraw();
      return;
    }

    if (data.length >= 1 && data >= " ") {
      this._inputBuf =
        this._inputBuf.slice(0, this._inputCursor) +
        data +
        this._inputBuf.slice(this._inputCursor);
      this._inputCursor += data.length;
      this.redraw();
    }
  }

  // ─── Popup helpers ─────────────────────────────────────────────

  private _updatePopupMatches(): void {
    const filter = this._inputBuf.slice(1).toLowerCase();
    if (!filter) {
      this._cmdPopupMatches = [...this._cmdNames];
    } else {
      this._cmdPopupMatches = this._cmdNames.filter((n) =>
        n.startsWith(filter),
      );
    }
    this._cmdPopupIdx = 0;
    if (this._cmdPopupMatches.length === 0) {
      this._cmdPopupActive = false;
    }
  }

  private _renderCmdPopup(): void {
    if (!this._cmdPopupActive || this._cmdPopupMatches.length === 0) return;
    const rW = this.rightW;
    const { startRow } = this._getPopupPosition();
    const popupRows = Math.min(8, this._cmdPopupMatches.length);

    let out = "";
    for (let i = 0; i < popupRows + 1; i++) {
      out +=
        ansi.pos(startRow + i, this.leftW + 3) + PANEL_BG(" ".repeat(rW - 2));
    }
    for (let i = 0; i < popupRows; i++) {
      const cmd = this._cmdPopupMatches[i];
      const isSelected = i === this._cmdPopupIdx;
      const line = isSelected ? PANEL_BG(Wb("  " + cmd)) : C("  " + cmd);
      const lineLen = visLen(cmd) + 2;
      const pad = " ".repeat(Math.max(0, rW - 2 - lineLen));
      out += ansi.pos(startRow + i, this.leftW + 3) + line + pad;
    }
    process.stdout.write(out);
  }

  // ─── Full redraw ───────────────────────────────────────────────

  redraw(): void {
    if (this._closed) return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      if (this._closed) return;
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try {
        const out =
          ansi.clear +
          this._buildFrame() +
          this._buildTopBar() +
          this._doBuildBody() +
          this._buildInputPanel() +
          this._buildCmdPopup() +
          this._buildShortcuts();
        process.stdout.write(out);
      } finally {
        this._rendering = false;
      }
    });
  }

  // ─── Frame (pixel shadow, no borders) ─────────────────────────

  /**
   * Pixel frame — shadow only, NO border lines.
   * Layout:
   *   Row 1:   full-width top shadow row
   *   Rows 2..r-1: shadow col col 1 + shadow col col c
   *   Row r:   full-width bottom shadow row
   */
  private _buildFrame(): string {
    const c = this.cols;
    const r = this.rows;
    let out = "";
    out += ansi.pos(1) + FRAME_H.repeat(c); // top shadow row
    for (let i = 2; i < r; i++) {
      out += ansi.pos(i, 1) + FRAME_V; // left shadow
      out += ansi.pos(i, c) + FRAME_V; // right shadow
    }
    out += ansi.pos(r) + FRAME_H.repeat(c); // bottom shadow row
    return out;
  }

  // ─── Top bar ───────────────────────────────────────────────────

  private _buildTopBar(): string {
    const c = this.cols;
    const bar = this._buildTopBarContent();
    return (
      ansi.pos(2) +
      PANEL_BG("  " + bar + "  ") +
      ansi.pos(3) +
      PANEL_BG(AMBER(DIV.repeat(c)))
    );
  }

  private _buildTopBarContent(): string {
    const badge = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(
      " " + this.owlEmoji + " " + this.owlName + " "
    );
    const model = this.owlModel
      ? " " + MUT("[") + BLUE(this.owlModel.replace("claude-", "").slice(0, 18)) + MUT("]")
      : "";
    const turn  = this._turn > 0
      ? " " + MUT("·") + " " + PURPLE("turn " + this._turn)
      : "";
    const toks  = this._tokens > 0
      ? " " + MUT("·") + " " + LBL((this._tokens / 1000).toFixed(1) + "k")
      : "";
    const cost  = this._cost > 0
      ? " " + MUT("·") + " " + GREEN("$" + this._cost.toFixed(3))
      : "";
    return badge + model + turn + toks + cost;
  }

  // ─── Body ──────────────────────────────────────────────────────

  private _renderBodyQueued(): void {
    if (this._closed) return;
    if (this._rendering) return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      if (this._closed) return;
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
    const lW = this.leftW;
    const rW = this.rightW;
    // rows 1=frame, 2=topbar, 3..r-5=body, r-4..r-2=input panel, r-1=shortcuts, r=frame
    const bodyRows = this.rows - 7;

    const leftLines = this._buildLeft(lW, bodyRows);
    const rightLines = this._buildRight(rW, bodyRows);

    let out = "";
    for (let i = 0; i < bodyRows; i++) {
      const row = 3 + i;
      const lLn = leftLines[i] ?? { t: "", v: 0 };
      const rLn = rightLines[i] ?? { t: "", v: 0 };
      const lPad = " ".repeat(Math.max(0, lW - lLn.v));
      const rPad = " ".repeat(Math.max(0, rW - rLn.v));

      out += ansi.pos(row, 2) + lLn.t + lPad; // left content (col 1 = left frame)
      out += ansi.pos(row, lW + 2) + PANEL_V; // panel separator
      out += ansi.pos(row, lW + 3) + rLn.t + rPad; // right content
    }
    return out;
  }

  // ─── Input panel (recessed box) ────────────────────────────────

  private _buildInputPanel(): string {
    const rW = this.rightW;
    const topRow = this.rows - 4;
    const inputRow = this.rows - 3;
    const botRow = this.rows - 2;
    const line = this._buildInputLine(rW);

    const topBorder = PANEL_BG(" ".repeat(rW + 2));
    const content = PANEL_BG(
      " " + line.t + " ".repeat(Math.max(0, rW - line.v)) + " ",
    );
    const botBorder = PANEL_BG(" ".repeat(rW + 2));

    return (
      ansi.pos(topRow, this.leftW + 2) +
      topBorder +
      ansi.pos(inputRow, this.leftW + 2) +
      content +
      ansi.pos(botRow, this.leftW + 2) +
      botBorder
    );
  }

  // ─── Command popup ─────────────────────────────────────────────

  private _getPopupPosition(): { startRow: number; above: boolean } {
    const inputRow = this.rows - 3; // input panel's text row
    const popupRows = Math.min(8, this._cmdPopupMatches.length);
    const spaceBelow = this.rows - 1 - (inputRow + 1 + popupRows);
    if (spaceBelow >= 0) {
      return { startRow: inputRow + 1, above: false };
    }
    return { startRow: inputRow - 1 - popupRows, above: true };
  }

  private _buildCmdPopup(): string {
    if (!this._cmdPopupActive || this._cmdPopupMatches.length === 0) return "";

    const rW = this.rightW;
    const { startRow } = this._getPopupPosition();
    const popupRows = Math.min(8, this._cmdPopupMatches.length);

    let out = "";
    for (let i = 0; i < popupRows + 1; i++) {
      out +=
        ansi.pos(startRow + i, this.leftW + 3) + PANEL_BG(" ".repeat(rW - 2));
    }
    for (let i = 0; i < popupRows; i++) {
      const cmd = this._cmdPopupMatches[i];
      const isSelected = i === this._cmdPopupIdx;
      const line = isSelected ? PANEL_BG(C("  " + cmd)) : C("  " + cmd);
      const lineLen = visLen(cmd) + 2;
      const pad = " ".repeat(Math.max(0, rW - 2 - lineLen));
      out += ansi.pos(startRow + i, this.leftW + 3) + line + pad;
    }
    return out;
  }

  // ─── Left panel ───────────────────────────────────────────────

  private _buildLeft(w: number, rows: number): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    // ── Section header helper ──────────────────────────────────────
    const secHdr = (label: string) => {
      const line = MUT("─".repeat(Math.max(0, w - label.length - 5)));
      return "  " + AMBER.bold(label) + " " + line;
    };

    blank();
    add(secHdr("OWL MIND"));
    blank();
    add("  " + AMBER(this._currentFace()));
    if (this._owlState === "thinking") {
      add("  " + BLUE(SPINNER[this._spinIdx % SPINNER.length] + " thinking..."));
    }
    blank();
    add(
      "  " + PURPLE("◆") + " " + LBL("Instincts") + "   " +
      (this._instincts > 0 ? AMBER.bold(this._instincts + " triggered") : MUT("—")),
    );
    add(
      "  " + PURPLE("◆") + " " + LBL("Memory   ") + "   " +
      (this._memFacts > 0 ? AMBER.bold(this._memFacts + " facts") : MUT("—")),
    );
    add(
      "  " + PURPLE("◆") + " " + LBL("Skills   ") + "   " +
      (this._skillsHit > 0 ? GREEN.bold(this._skillsHit + " invoked") : MUT("—")),
    );
    blank();

    if (this._toolCalls.length > 0) {
      add(secHdr("REASONING"));
      const visible = this._toolCalls.slice(-8);
      visible.forEach((tc, i) => {
        const isLast = i === visible.length - 1;
        const branch = isLast ? MUT("  └ ") : MUT("  ├ ");
        const spinner = SPINNER[this._spinIdx % SPINNER.length];
        const icon =
          tc.status === "running"
            ? BLUE(spinner)
            : tc.status === "done"
              ? GREEN("✓")
              : R("✕");
        const name = tc.status === "running"
          ? BLUE(trunc(tc.name, w - 18))
          : W(trunc(tc.name, w - 18));
        const ms = tc.ms ? MUT(" " + tc.ms + "ms") : "";
        add(branch + icon + " " + name + ms);
        if (tc.summary) {
          const indent = isLast ? "        " : "  │     ";
          add(indent + LBL(trunc(tc.summary, w - 12)));
        }
      });
      blank();
    }

    const dnaStartIdx = lines.length;
    const dnaRows = rows - dnaStartIdx - 2;

    if (dnaRows > 4) {
      blank();
      add(secHdr("DNA"));
      blank();
      add("  " + this._dnaBar("challenge", this._dna.challenge, "challenge"));
      add("  " + this._dnaBar("verbosity", this._dna.verbosity, "verbosity"));
      add("  " + this._dnaBar("mood     ", this._dna.mood, "mood"));
    }

    while (lines.length < rows - 1) blank();
    add("  " + MUT("─".repeat(Math.max(0, w - 4))) + " " + MUT("FIREWALL"));

    return lines.slice(0, rows);
  }

  // ─── Right panel ─────────────────────────────────────────────

  private _convRows(): number {
    return this.rows - 4;
  }

  private _buildRight(
    w: number,
    rows: number,
  ): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const convRows = rows - 1;

    if (this._lines.length === 0) {
      lines.push({ t: "  " + D("What do you want to work on?"), v: 26 });
    } else {
      const total = this._lines.length;
      const end = Math.max(0, total - this._scrollOff);
      const start = Math.max(0, end - this._convRows());
      const vis = this._lines.slice(start, end);
      for (let i = 0; i < convRows; i++) {
        const l = vis[i] ?? "";
        lines.push({ t: l, v: visLen(l) });
      }
    }

    while (lines.length < convRows) lines.push({ t: "", v: 0 });

    lines.push({
      t: "  " + D(DIV.repeat(w - 4)),
      v: visLen("  " + DIV.repeat(w - 4)),
    });
    lines.push(this._buildInputLine(w));

    return lines.slice(0, rows);
  }

  private _buildInputLine(_w?: number): { t: string; v: number } {
    if (this._inputLocked) {
      const spin = C(SPINNER[this._spinIdx % SPINNER.length]);
      return {
        t: "  " + spin + D("  thinking..."),
        v: visLen("  thinking...") + 3,
      };
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
    const inner = c - 4; // 2-char inset on each side
    const line =
      Wb("[Esc]") +
      Wbr("  Stop     ") +
      Wb("[^P]") +
      Wbr("  Parliament     ") +
      Wb("[^L]") +
      Wbr("  Clear     ") +
      Wb("[^C]") +
      Wbr("  Quit");
    return ansi.pos(r - 1, 3) + SHORT_BG(padR(line, inner));
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

  private _dnaBar(
    label: string,
    val: number,
    trait: "challenge" | "verbosity" | "mood",
  ): string {
    const v = Math.max(0, Math.min(10, Math.round(val)));
    const color =
      trait === "challenge" ? AMBER : trait === "verbosity" ? BLUE : GREEN;
    const filled = color("█").repeat(v);
    const empty  = MUT("█").repeat(10 - v);
    return LBL(label) + " " + filled + empty + " " + MUT(String(val));
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
