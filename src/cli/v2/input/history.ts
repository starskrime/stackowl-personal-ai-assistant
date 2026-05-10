/** 100-entry input history, ported from v1 input-handler.ts. */

const MAX_HISTORY = 100;

export class InputHistory {
  private _entries: string[] = [];
  private _idx = -1;

  push(entry: string): void {
    const trimmed = entry.trim();
    if (!trimmed) return;
    // Avoid consecutive duplicates
    if (this._entries[this._entries.length - 1] === trimmed) return;
    this._entries.push(trimmed);
    if (this._entries.length > MAX_HISTORY) this._entries.shift();
    this._idx = -1;
  }

  prev(_current: string): string | null {
    if (this._entries.length === 0) return null;
    if (this._idx === -1) this._idx = this._entries.length;
    if (this._idx <= 0) return null;
    this._idx--;
    return this._entries[this._idx];
  }

  next(): string | null {
    if (this._idx === -1 || this._idx >= this._entries.length - 1) {
      this._idx = -1;
      return null;
    }
    this._idx++;
    return this._entries[this._idx];
  }

  reset(): void {
    this._idx = -1;
  }
}
