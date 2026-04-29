import { EventEmitter } from "node:events";
import type { CompletionEngine, CompletionResult } from "./completion-engine.js";

const ESC = "\x1B";

export class InputHandler extends EventEmitter {
  private _buf      = "";
  private _cursor   = 0;
  private _history: string[] = [];
  private _histIdx  = -1;
  private _histTemp = "";
  private _cmdIdx   = 0;
  private _completion: CompletionResult = { items: [], mode: "command" };
  private _engine: CompletionEngine | null = null;
  private _masked     = false;
  private _allowEmpty = false;
  private _locked     = false;

  // ─── State (read by renderer each frame) ─────────────────────

  get buf()            { return this._buf; }
  get cursor()         { return this._cursor; }
  get locked()         { return this._locked; }
  get masked()         { return this._masked; }
  get cmdPopupActive() { return this._completion.items.length > 0 && this._buf.startsWith("/"); }
  get cmdMatches()     { return [...this._completion.items]; }
  get cmdIdx()         { return this._cmdIdx; }

  // ─── Configuration ────────────────────────────────────────────

  setCompletionEngine(engine: CompletionEngine): void {
    this._engine = engine;
    this._refreshCompletion();
  }

  setMasked(on: boolean)       { this._masked = on; }
  setAllowEmpty(on: boolean)   { this._allowEmpty = on; }
  setLocked(on: boolean)       { this._locked = on; }
  setInitialInput(buf: string) { this._buf = buf; this._cursor = buf.length; this._refreshCompletion(); }

  // ─── Key feed ─────────────────────────────────────────────────

  /** Feed a raw stdin data chunk. Emits: "line", "quit", "change", "scroll", "clear". */
  feed(data: string): void {
    if (data === "\x03" || data === "\x04") { this.emit("quit"); return; }

    data = data.replace(/\x1B\[200~/g, "").replace(/\x1B\[201~/g, "");
    if (!data) return;

    this._handleKey(data);
  }

  // ─── y/n prompt ───────────────────────────────────────────────

  async promptYesNo(): Promise<boolean> {
    return new Promise(resolve => {
      const onKey = (chunk: unknown) => {
        const k = typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
        if (k.toLowerCase() === "y") { process.stdin.off("data", onKey); resolve(true); }
        else if (k.toLowerCase() === "n" || k === "\x03") { process.stdin.off("data", onKey); resolve(false); }
      };
      process.stdin.on("data", onKey);
    });
  }

  // ─── Private ──────────────────────────────────────────────────

  private _handleKey(data: string): void {
    if (this._locked) return;

    // ─── Submit / select ──────────────────────────────────────
    if (data === "\r" || data === "\n") {
      if (this.cmdPopupActive) {
        const selected = this._completion.items[this._cmdIdx];
        // Guard is intentional: _cmdIdx is reset to 0 on every _refreshCompletion call,
        // so it is always in range while the list is stable. The check protects against
        // a stale index if the list is ever updated between navigation and selection.
        if (selected !== undefined) {
          if (this._completion.mode === "command") {
            this._buf = "/" + selected + " ";
          } else {
            const spaceIdx = this._buf.indexOf(" ");
            this._buf = this._buf.slice(0, spaceIdx + 1) + selected + " ";
          }
          this._cursor = this._buf.length;
          this._refreshCompletion();
          this.emit("change");
          return;
        }
      }
      const line = this._buf.trim();
      this._buf = ""; this._cursor = 0; this._histIdx = -1;
      this._refreshCompletion();
      if (line) {
        this._history.unshift(line);
        if (this._history.length > 100) this._history.pop();
        this._masked = false;
        this.emit("line", line);
      } else if (this._allowEmpty) {
        this.emit("line", "");
      }
      this.emit("change");
      return;
    }

    // ─── Backspace ────────────────────────────────────────────
    if (data === "\x7f") {
      if (this._cursor > 0) {
        this._buf = this._buf.slice(0, this._cursor - 1) + this._buf.slice(this._cursor);
        this._cursor--;
        this._refreshCompletion();
        this.emit("change");
      }
      return;
    }

    // ─── Arrow Up ─────────────────────────────────────────────
    if (data === ESC + "[A") {
      if (this.cmdPopupActive) {
        this._cmdIdx = Math.max(0, this._cmdIdx - 1);
        this.emit("change");
      } else {
        if (this._histIdx === -1) this._histTemp = this._buf;
        if (this._histIdx < this._history.length - 1) {
          this._histIdx++;
          this._buf    = this._history[this._histIdx];
          this._cursor = this._buf.length;
          this._refreshCompletion();
          this.emit("change");
        }
      }
      return;
    }

    // ─── Arrow Down ───────────────────────────────────────────
    if (data === ESC + "[B") {
      if (this.cmdPopupActive) {
        this._cmdIdx = Math.min(this._completion.items.length - 1, this._cmdIdx + 1);
        this.emit("change");
      } else {
        if (this._histIdx > -1) {
          this._histIdx--;
          this._buf    = this._histIdx === -1 ? this._histTemp : this._history[this._histIdx];
          this._cursor = this._buf.length;
          this._refreshCompletion();
          this.emit("change");
        }
      }
      return;
    }

    // ─── Other navigation ─────────────────────────────────────
    if (data === ESC + "[D" && this._cursor > 0)                { this._cursor--; this.emit("change"); return; }
    if (data === ESC + "[C" && this._cursor < this._buf.length) { this._cursor++; this.emit("change"); return; }
    if (data === ESC + "[5~") { this.emit("scroll",  5); return; }
    if (data === ESC + "[6~") { this.emit("scroll", -5); return; }
    if (data === "\x0C")      { this.emit("clear");       return; }

    // ─── ESC ──────────────────────────────────────────────────
    if (data === ESC) {
      if (this.cmdPopupActive) {
        this._buf = ""; this._cursor = 0;
        this._refreshCompletion();
        this.emit("change");
      }
      return;
    }

    // ─── Printable characters ─────────────────────────────────
    if (data.length >= 1) {
      const printable = data.length === 1
        ? (data >= " " ? data : "")
        : data.replace(/[\x00-\x1F\x7F]/g, "");
      if (!printable) return;
      this._buf    = this._buf.slice(0, this._cursor) + printable + this._buf.slice(this._cursor);
      this._cursor += printable.length;
      this._refreshCompletion();
      this.emit("change");
    }
  }

  private _refreshCompletion(): void {
    if (this._engine) {
      this._completion = this._engine.complete(this._buf);
    } else {
      this._completion = { items: [], mode: "command" };
    }
    this._cmdIdx = 0;
  }
}
