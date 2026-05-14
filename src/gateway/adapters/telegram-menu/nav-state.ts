/**
 * StackOwl — Telegram Unified Nav: Navigation State Manager
 *
 * Per-user nav stack (screen history + message being edited in-place).
 * Pattern mirrors telegram-config/state.ts (MenuStateManager).
 */

const NAV_TTL_MS = 10 * 60 * 1000; // 10 minutes inactivity

export interface NavState {
  userId: number;
  chatId: number;
  /** The single Telegram message ID being edited in-place */
  messageId: number;
  /** Screen stack — last entry is current screen */
  stack: string[];
  /** When set: next plain-text message from user is consumed by this action */
  pendingText?: string;
  lastActivity: number;
}

export class NavStateManager {
  private states = new Map<number, NavState>();
  private cleanupInterval: ReturnType<typeof setInterval>;

  constructor() {
    this.cleanupInterval = setInterval(() => this.evict(), 5 * 60 * 1000);
    this.cleanupInterval.unref();
  }

  get(userId: number): NavState | undefined {
    return this.states.get(userId);
  }

  /** Create or reset a nav session at the root screen */
  open(userId: number, chatId: number, messageId: number): NavState {
    const state: NavState = { userId, chatId, messageId, stack: ["root"], lastActivity: Date.now() };
    this.states.set(userId, state);
    return state;
  }

  /** Navigate to a new screen (push) */
  push(userId: number, screen: string): NavState | undefined {
    const s = this.states.get(userId);
    if (!s) return undefined;
    s.stack.push(screen);
    s.lastActivity = Date.now();
    s.pendingText = undefined;
    return s;
  }

  /** Go back one screen (pop) — never goes below root */
  pop(userId: number): NavState | undefined {
    const s = this.states.get(userId);
    if (!s) return undefined;
    if (s.stack.length > 1) s.stack.pop();
    s.lastActivity = Date.now();
    s.pendingText = undefined;
    return s;
  }

  /** Return current screen name */
  current(userId: number): string | undefined {
    const s = this.states.get(userId);
    return s?.stack[s.stack.length - 1];
  }

  /** Update the message ID being edited (e.g. after sending a new message) */
  setMessageId(userId: number, messageId: number): void {
    const s = this.states.get(userId);
    if (s) { s.messageId = messageId; s.lastActivity = Date.now(); }
  }

  setPendingText(userId: number, action: string): void {
    const s = this.states.get(userId);
    if (s) { s.pendingText = action; s.lastActivity = Date.now(); }
  }

  clearPendingText(userId: number): void {
    const s = this.states.get(userId);
    if (s) { s.pendingText = undefined; s.lastActivity = Date.now(); }
  }

  touch(userId: number): void {
    const s = this.states.get(userId);
    if (s) s.lastActivity = Date.now();
  }

  delete(userId: number): void {
    this.states.delete(userId);
  }

  evict(): void {
    const now = Date.now();
    for (const [uid, s] of this.states) {
      if (now - s.lastActivity > NAV_TTL_MS) this.states.delete(uid);
    }
  }

  destroy(): void {
    clearInterval(this.cleanupInterval);
    this.states.clear();
  }
}
