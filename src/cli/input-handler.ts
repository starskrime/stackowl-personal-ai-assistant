// src/cli/input-handler.ts
import { EventEmitter } from "node:events";

const ESC = "\x1B";

export class InputHandler extends EventEmitter {
  private _buf    = "";
  private _cursor = 0;
  private _history: string[]  = [];
  private _histIdx  = -1;
  private _histTemp = "";
  private _cmdPopupActive = false;
  private _cmdNames:   string[] = [];
  private _cmdMatches: string[] = [];
  private _cmdIdx  = 0;
  private _masked  = false;
  private _allowEmpty = false;
  private _locked  = false;

  // ─── State (read by renderer each frame) ─────────────────────

  get buf()            { return this._buf; }
  get cursor()         { return this._cursor; }
  get locked()         { return this._locked; }
  get masked()         { return this._masked; }
  get cmdPopupActive() { return this._cmdPopupActive; }
  get cmdMatches()     { return [...this._cmdMatches]; }
  get cmdIdx()         { return this._cmdIdx; }

  // ─── Configuration ────────────────────────────────────────────

  setCommandList(names: string[])  { this._cmdNames = names; }
  setMasked(on: boolean)           { this._masked = on; }
  setAllowEmpty(on: boolean)       { this._allowEmpty = on; }
  setLocked(on: boolean)           { this._locked = on; }
  setInitialInput(buf: string)     { this._buf = buf; this._cursor = buf.length; }

  // ─── Key feed ─────────────────────────────────────────────────

  /** Feed a raw stdin data chunk. Emits: "line", "quit", "change", "scroll", "clear". */
  feed(data: string): void {
    if (data === "\x03" || data === "\x04") { this.emit("quit"); return; }

    // Strip bracketed-paste wrappers sent by modern terminals (ESC[200~ ... ESC[201~).
    // Without this, pasted text is silently dropped because the data starts with ESC
    // which fails the printable-character guard below.
    data = data.replace(/\x1B\[200~/g, "").replace(/\x1B\[201~/g, "");
    if (!data) return;

    if (this._cmdPopupActive) { this._handlePopupKey(data); return; }
    this._handleNormalKey(data);
  }

  // ─── y/n prompt (used by askInstall) ─────────────────────────

  /** Pause normal input, wait for y/n keypress, then return. */
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

  private _handleNormalKey(data: string): void {
    if (this._locked) return;

    if (data === "\r" || data === "\n") {
      const line = this._buf.trim();
      this._buf = ""; this._cursor = 0; this._histIdx = -1;
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
    if (data === "\x7f") {
      if (this._cursor > 0) {
        this._buf = this._buf.slice(0, this._cursor - 1) + this._buf.slice(this._cursor);
        this._cursor--;
        this.emit("change");
      }
      return;
    }
    if (data === ESC + "[A") {
      if (this._histIdx === -1) this._histTemp = this._buf;
      if (this._histIdx < this._history.length - 1) {
        this._histIdx++;
        this._buf    = this._history[this._histIdx];
        this._cursor = this._buf.length;
        this.emit("change");
      }
      return;
    }
    if (data === ESC + "[B") {
      if (this._histIdx > -1) {
        this._histIdx--;
        this._buf    = this._histIdx === -1 ? this._histTemp : this._history[this._histIdx];
        this._cursor = this._buf.length;
        this.emit("change");
      }
      return;
    }
    if (data === ESC + "[D" && this._cursor > 0)                    { this._cursor--; this.emit("change"); return; }
    if (data === ESC + "[C" && this._cursor < this._buf.length)     { this._cursor++; this.emit("change"); return; }
    if (data === ESC + "[5~") { this.emit("scroll",  5); return; }
    if (data === ESC + "[6~") { this.emit("scroll", -5); return; }
    if (data === "\x0C")      { this.emit("clear");       return; }

    if (data === "/") {
      this._buf = "/"; this._cursor = 1;
      this._cmdPopupActive = true;
      this._updateMatches();
      this._cmdIdx = 0;
      this.emit("change");
      return;
    }
    if (data.length >= 1) {
      // For single chars: accept printable only. For paste chunks (multi-char): strip
      // any residual control characters and insert the printable content.
      const printable = data.length === 1
        ? (data >= " " ? data : "")
        : data.replace(/[\x00-\x1F\x7F]/g, "");
      if (!printable) return;
      this._buf    = this._buf.slice(0, this._cursor) + printable + this._buf.slice(this._cursor);
      this._cursor += printable.length;
      this.emit("change");
    }
  }

  private _handlePopupKey(data: string): void {
    if (data === ESC + "[A") { this._cmdIdx = Math.max(0, this._cmdIdx - 1); this.emit("change"); return; }
    if (data === ESC + "[B") { this._cmdIdx = Math.min(this._cmdMatches.length - 1, this._cmdIdx + 1); this.emit("change"); return; }
    if (data === "\r" || data === "\n") {
      const selected = this._cmdMatches[this._cmdIdx];
      if (selected) { this._buf = "/" + selected; this._cursor = this._buf.length; }
      this._cmdPopupActive = false;
      this.emit("change");
      return;
    }
    if (data === ESC) {
      this._buf = ""; this._cursor = 0;
      this._cmdPopupActive = false;
      this.emit("change");
      return;
    }
    if (data === "\x7f") {
      if (this._buf.length <= 1) { this._buf = ""; this._cursor = 0; this._cmdPopupActive = false; }
      else { this._buf = this._buf.slice(0, -1); this._cursor--; this._updateMatches(); }
      this.emit("change");
      return;
    }
    if (data.length >= 1 && data >= " ") {
      this._buf    = this._buf.slice(0, this._cursor) + data + this._buf.slice(this._cursor);
      this._cursor += data.length;
      this._updateMatches();
      this.emit("change");
    }
  }

  private _updateMatches(): void {
    const filter    = this._buf.slice(1).toLowerCase();
    this._cmdMatches = filter
      ? this._cmdNames.filter(n => n.startsWith(filter))
      : [...this._cmdNames];
    this._cmdIdx = 0;
    if (this._cmdMatches.length === 0) this._cmdPopupActive = false;
  }
}
