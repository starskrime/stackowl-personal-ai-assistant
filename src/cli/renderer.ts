// src/cli/renderer.ts
import { EventEmitter } from "node:events";
import { ansi } from "./shared/ansi.js";
import { CONTENT_BG, PANEL_V, LBL, R } from "./shared/palette.js";
import { visLen, wrapText } from "./shared/text.js";
import { computeLayout } from "./layout.js";
import { renderTopBar } from "./components/top-bar.js";
import { renderLeftPanel, type LeftPanelProps, type OwlState, type ToolEntry } from "./components/left-panel.js";
import { renderRightPanel, type RightPanelProps, type RecentSession } from "./components/right-panel.js";
import { renderInputBox } from "./components/input-box.js";
import { renderCmdPopup } from "./components/cmd-popup.js";
import { renderShortcutsBar, type ShortcutEntry } from "./components/shortcuts-bar.js";
import { InputHandler } from "./input-handler.js";
import type { GatewayResponse } from "../gateway/types.js";
import type { StreamEvent } from "../providers/base.js";

// ─── Message model ────────────────────────────────────────────────

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  label?: string;        // assistant header e.g. "🦉 Owl"
  preformatted?: boolean; // render content as-is, no rewrap
}

// ─── State types ──────────────────────────────────────────────────

interface RendererState {
  mode: "home" | "session";
  // TopBar
  owlEmoji:  string;
  owlName:   string;
  model:     string;
  turn:      number;
  tokens:    number;
  cost:      number;
  // LeftPanel
  owlState:   OwlState;
  spinIdx:    number;
  dna:        { challenge: number; verbosity: number; mood: number };
  toolCalls:  ToolEntry[];
  instincts:  number;
  memFacts:   number;
  skillsHit:  number;
  generation: number;
  challenge:  number;
  provider:   string;
  skills:     number;
  // RightPanel
  messages:           ChatMessage[];
  scrollOff:          number;
  totalRenderedLines: number;
  recentSessions:     RecentSession[];
  // streaming
  streaming:  boolean;
  streamBuf:  string;
}

const DEFAULT_SHORTCUTS: ShortcutEntry[] = [
  { key: "ESC", label: "Stop" },
  { key: "^P",  label: "Parliament" },
  { key: "^L",  label: "Clear" },
  { key: "^C",  label: "Quit" },
];

// ─── TerminalRenderer ─────────────────────────────────────────────

export class TerminalRenderer extends EventEmitter {
  readonly input: InputHandler;

  private _state: RendererState = {
    mode: "home",
    owlEmoji: "🦉", owlName: "Owl", model: "", turn: 0, tokens: 0, cost: 0,
    owlState: "idle", spinIdx: 0,
    dna: { challenge: 5, verbosity: 5, mood: 7 },
    toolCalls: [], instincts: 0, memFacts: 0, skillsHit: 0,
    generation: 1, challenge: 5, provider: "", skills: 0,
    messages: [], scrollOff: 0, totalRenderedLines: 0, recentSessions: [],
    streaming: false, streamBuf: "",
  };

  private _thinkTimer:  ReturnType<typeof setInterval> | null = null;
  private _thinkStart   = 0;
  private _resizeTimer: ReturnType<typeof setTimeout>  | null = null;
  private _rendering    = false;
  private _renderQueued = false;
  private _closed       = false;
  private _origConsoleLog:  typeof console.log  | null = null;
  private _origConsoleWarn: typeof console.warn | null = null;

  constructor() {
    super();
    this.input = new InputHandler();
    this.input.on("change", () => this.redraw());
    this.input.on("quit",   () => this.emit("quit"));
    this.input.on("clear",  () => {
      this._state.messages = [];
      this._state.toolCalls = [];
      this._state.scrollOff = 0;
      this._state.totalRenderedLines = 0;
      this.redraw();
    });
    this.input.on("scroll", (delta: number) => {
      const max = Math.max(0, this._state.totalRenderedLines - this._convRows());
      this._state.scrollOff = Math.max(0, Math.min(this._state.scrollOff + delta, max));
      this.redraw();
    });
  }

  // ─── Lifecycle ────────────────────────────────────────────────

  enter(): void {
    // Silence console.log/warn so background services don't corrupt the alt screen
    if (!this._origConsoleLog) {
      this._origConsoleLog  = console.log;
      this._origConsoleWarn = console.warn;
      console.log  = (...args: unknown[]) => this.printInfo(args.map(String).join(" "));
      console.warn = (...args: unknown[]) => this.printInfo(args.map(String).join(" "));
    }
    process.stdout.write(ansi.altIn + ansi.hide);
    if (process.stdin.isTTY) process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", this._keyHandler);
    process.stdout.on("resize", this._resizeHandler);
    setTimeout(() => this.redraw(), 40);
  }

  close(): void {
    this._closed = true;
    this._stopThink();
    // Restore console before exiting alt screen
    if (this._origConsoleLog) {
      console.log  = this._origConsoleLog;
      console.warn = this._origConsoleWarn!;
      this._origConsoleLog  = null;
      this._origConsoleWarn = null;
    }
    process.stdin.off("data",   this._keyHandler);
    process.stdout.off("resize", this._resizeHandler);
    process.stdout.write(ansi.show + ansi.altOut);
    if (process.stdin.isTTY) {
      try { process.stdin.setRawMode(false); } catch { /**/ }
    }
  }

  // ─── Configuration ────────────────────────────────────────────

  setMode(mode: "home" | "session"): void {
    this._state.mode = mode;
    this.redraw();
  }

  setOwl(emoji: string, name: string, provider: string, model: string): void {
    Object.assign(this._state, { owlEmoji: emoji, owlName: name, provider, model });
  }

  updateDNA(dna: Partial<RendererState["dna"]>): void {
    Object.assign(this._state.dna, dna);
    this.redraw();
  }

  updateStats(tokens: number, cost: number): void {
    this._state.tokens = tokens;
    this._state.cost   = cost;
  }

  setRecentSessions(sessions: RecentSession[]): void {
    this._state.recentSessions = sessions;
  }

  setCommandList(names: string[]): void {
    this.input.setCommandList(names);
  }

  setInitialInput(buf: string): void {
    this.input.setInitialInput(buf);
  }

  setMasked(on: boolean):     void { this.input.setMasked(on); }
  setAllowEmptyInput(on: boolean): void { this.input.setAllowEmpty(on); }

  // ─── Public output API ────────────────────────────────────────

  showThinking(): void {
    this.input.setLocked(true);
    this._state.owlState = "thinking";
    this._thinkStart = Date.now();
    this._state.spinIdx  = 0;
    this._stopThink();
    this._thinkTimer = setInterval(() => {
      this._state.spinIdx++;
      this.redraw();
    }, 100);
  }

  stopThinking(): void {
    this._stopThink();
    this._state.owlState = "idle";
    this.input.setLocked(false);
    this.redraw();
  }

  showToolCall(name: string): void {
    this._state.owlState = "thinking";
    const [tool, ...rest] = name.split(" ");
    this._state.toolCalls.push({ name: tool, args: rest.join(" "), status: "running" });
    if (this._state.toolCalls.length > 12) this._state.toolCalls.shift();
    this.redraw();
  }

  completeToolCall(): void {
    const last = this._state.toolCalls.findLast?.((t) => t.status === "running")
      ?? this._state.toolCalls.filter(t => t.status === "running").at(-1);
    if (last) { last.status = "done"; last.ms = Date.now() - this._thinkStart; }
    this.redraw();
  }

  showUserMessage(text: string): void {
    this._pushMessage({ role: "user", content: text });
    this.redraw();
  }

  showResponse(response: GatewayResponse): void {
    this._stopThink();
    this._state.owlState = "done";
    this._state.turn++;
    this._pushMessage({
      role: "assistant",
      content: response.content,
      label: response.owlEmoji + " " + response.owlName,
    });
    this.input.setLocked(false);
    this.redraw();
  }

  printResponse(emoji: string, name: string, content: string): void {
    this.showResponse({ content, owlName: name, owlEmoji: emoji, toolsUsed: [] });
  }

  printError(msg: string): void {
    this._stopThink();
    this._state.owlState = "error";
    this._pushMessage({ role: "system", content: "  " + R("✕ ") + R(msg), preformatted: true });
    this.input.setLocked(false);
    this.redraw();
  }

  printInfo(msg: string): void {
    this._pushMessage({ role: "system", content: "  " + LBL(msg), preformatted: true });
    this.redraw();
  }

  printLines(lines: string[]): void {
    const content = lines.map(l => l === "" ? "" : "  " + l).join("\n");
    this._pushMessage({ role: "system", content, preformatted: true });
    this.redraw();
  }

  // ─── Streaming ────────────────────────────────────────────────

  createStreamHandler(): { handler: (event: StreamEvent) => Promise<void>; didStream: () => boolean } {
    let streamed = false;
    const handler = async (ev: StreamEvent) => {
      switch (ev.type) {
        case "text_delta": {
          const chunk = ev.content.replace(/\[DONE\]/g, "");
          if (!chunk) break;
          this._stopThink();
          this._state.owlState = "done";
          if (!this._state.streaming) {
            this._state.streaming = true;
            this._state.streamBuf = "";
            this._state.turn++;
            this._state.messages.push({
              role: "assistant",
              label: this._state.owlEmoji + " " + this._state.owlName,
              content: "",
            });
          }
          this._state.streamBuf += chunk;
          this._state.messages[this._state.messages.length - 1]!.content = this._state.streamBuf;
          this.redraw();
          streamed = true;
          break;
        }
        case "tool_start": this.stopThinking(); this.showToolCall(ev.toolName); break;
        case "tool_end":   this.completeToolCall(); break;
        case "done":
          this._stopThink();
          this._state.owlState    = "idle";
          this._state.streaming   = false;
          this._state.streamBuf   = "";
          this.input.setLocked(false);
          this.redraw();
          break;
      }
    };
    return { handler, didStream: () => streamed };
  }

  // ─── Redraw ───────────────────────────────────────────────────

  redraw(): void {
    if (this._closed)       return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      if (this._closed)    return;
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try { process.stdout.write(this._buildFrame()); }
      finally { this._rendering = false; }
    });
  }

  // ─── Frame builder ────────────────────────────────────────────

  private _buildFrame(): string {
    const layout = computeLayout();
    const { cols, rows, leftW, rightW } = layout;
    const FRAME_H = CONTENT_BG(" ");
    const FRAME_V = CONTENT_BG(" ");

    let out = ansi.clear;

    // Pixel-shadow frame
    out += ansi.pos(1)   + FRAME_H.repeat(cols);
    for (let i = 2; i < rows; i++) {
      out += ansi.pos(i, 1)    + FRAME_V;
      out += ansi.pos(i, cols) + FRAME_V;
    }
    out += ansi.pos(rows) + FRAME_H.repeat(cols);

    // Top bar (rows 2–3)
    const topBarStr = renderTopBar({
      owlEmoji: this._state.owlEmoji, owlName: this._state.owlName,
      model: this._state.model, turn: this._state.turn,
      tokens: this._state.tokens, cost: this._state.cost,
    }, cols);
    out += ansi.pos(2) + topBarStr;

    // Body (rows 4 to rows-5)
    // bodyRows must end at rows-5 so the input box (rows-4 .. rows-2) never overlaps.
    const bodyRows = rows - 8;
    const leftLines   = renderLeftPanel(this._leftProps(),  leftW,  bodyRows);
    const rightResult = renderRightPanel(this._rightProps(), rightW, bodyRows);
    this._state.totalRenderedLines = rightResult.totalLines;
    const rightLines = rightResult.lines;

    for (let i = 0; i < bodyRows; i++) {
      const row  = 4 + i;
      const lLn  = leftLines[i]  ?? "";
      const rLn  = rightLines[i] ?? "";
      const lPad = " ".repeat(Math.max(0, leftW  - visLen(lLn)));
      const rPad = " ".repeat(Math.max(0, rightW - visLen(rLn)));
      out += ansi.pos(row, 2)        + lLn + lPad;
      out += ansi.pos(row, leftW + 2) + PANEL_V;
      out += ansi.pos(row, leftW + 5) + rLn + rPad;
    }

    // Input panel (rows rows-4 to rows-2)
    const inputStr = renderInputBox({
      buf: this.input.buf, cursor: this.input.cursor,
      locked: this.input.locked, masked: this.input.masked,
      spinIdx: this._state.spinIdx,
    }, rightW);
    const inputLines = inputStr.split("\n");
    out += ansi.pos(rows - 4, leftW + 2) + inputLines[0];
    out += ansi.pos(rows - 3, leftW + 2) + inputLines[1];
    out += ansi.pos(rows - 2, leftW + 2) + inputLines[2];
    // Restore panel separator over the input box's left 3 columns
    out += ansi.pos(rows - 4, leftW + 2) + PANEL_V;
    out += ansi.pos(rows - 3, leftW + 2) + PANEL_V;
    out += ansi.pos(rows - 2, leftW + 2) + PANEL_V;

    // Command popup
    if (this.input.cmdPopupActive) {
      const popupLines = renderCmdPopup({ matches: this.input.cmdMatches, selectedIdx: this.input.cmdIdx }, rightW);
      const startRow   = rows - 4 - popupLines.length;
      for (let i = 0; i < popupLines.length; i++) {
        out += ansi.pos(startRow + i, leftW + 3) + popupLines[i];
      }
    }

    // Shortcuts bar (row rows-1)
    out += ansi.pos(rows - 1, 3) + renderShortcutsBar(DEFAULT_SHORTCUTS, cols);

    return out;
  }

  // ─── Props builders ───────────────────────────────────────────

  private _leftProps(): LeftPanelProps {
    const s = this._state;
    return {
      mode: s.mode, owlState: s.owlState, spinIdx: s.spinIdx,
      dna: s.dna, toolCalls: s.toolCalls,
      instincts: s.instincts, memFacts: s.memFacts, skillsHit: s.skillsHit,
      owlEmoji: s.owlEmoji, owlName: s.owlName, generation: s.generation,
      challenge: s.challenge, provider: s.provider, model: s.model, skills: s.skills,
    };
  }

  private _rightProps(): RightPanelProps {
    const s = this._state;
    return { mode: s.mode, messages: s.messages, scrollOff: s.scrollOff, recentSessions: s.recentSessions };
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private _convRows(): number { return computeLayout().rows - 4; }

  private _pushMessage(msg: ChatMessage): void {
    this._state.messages.push(msg);
    if (this._state.messages.length > 500) this._state.messages.shift();
    if (this._state.scrollOff > 0) {
      // Estimate display lines added so the viewport doesn't jump when reading history
      const { rightW } = computeLayout();
      this._state.scrollOff += this._estimateLines(msg, rightW);
    }
  }

  private _estimateLines(msg: ChatMessage, rightW: number): number {
    if (msg.preformatted) return msg.content.split("\n").length;
    const wrapW = msg.role === "user"
      ? Math.max(10, Math.min(Math.floor(rightW * 0.70), rightW - 4))
      : Math.max(10, rightW - 4);
    const bodyLines = wrapText(msg.content, wrapW).length;
    const labelLines = msg.label ? 1 : 0;
    return bodyLines + labelLines + 1; // +1 blank separator
  }

  private _stopThink(): void {
    if (this._thinkTimer) { clearInterval(this._thinkTimer); this._thinkTimer = null; }
  }

  private _keyHandler = (chunk: unknown): void => {
    const key = typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
    this.input.feed(key);
  };

  private _resizeHandler = (): void => {
    if (this._resizeTimer) clearTimeout(this._resizeTimer);
    this._resizeTimer = setTimeout(() => { this._resizeTimer = null; this.redraw(); }, 100);
  };
}
