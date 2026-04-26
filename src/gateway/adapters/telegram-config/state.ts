/**
 * StackOwl — Telegram Config Menu: State Types
 *
 * Per-user menu session state with TTL-based expiry.
 * Keyed by userId (Telegram numeric ID).
 */

// ─── Screen identifiers ───────────────────────────────────────────

export type MenuScreen =
  | "main"
  | "providers"
  | "provider_detail"
  | "provider_add_type"
  | "provider_add_url"
  | "provider_add_key"
  | "provider_model_pick"
  | "model_roles"
  | "model_role_prov_pick"
  | "model_role_model_pick"
  | "smart_routing"
  | "sr_prov_pick"
  | "sr_model_pick"
  | "health_check";

// ─── Awaiting input marker ────────────────────────────────────────

export interface PendingInput {
  /** What field the next plain-text message from the user fills */
  field: "baseUrl" | "apiKey" | "modelSearch";
  /** Context: which provider or role this input is for */
  contextKey: string;
}

// ─── Per-user menu state ──────────────────────────────────────────

export interface MenuState {
  userId: number;
  chatId: number;
  /** The single Telegram message ID being edited in-place */
  messageId: number;
  /** Current screen */
  screen: MenuScreen;
  /** Navigation stack for the ← Back button */
  breadcrumb: MenuScreen[];

  // ── Pending flows ──────────────────────────────────────────────
  /** Provider key being edited or added (e.g. "anthropic", "ollama-cloud") */
  pendingProviderKey?: string;
  /** Partial provider entry built up during the add flow */
  pendingEntry?: {
    providerType: string;
    baseUrl?: string;
    apiKey?: string;
    defaultModel?: string;
  };
  /** Model role being assigned ("chat" | "synthesis" | "embedding" | ...) */
  pendingRole?: string;
  /** Provider selected for role assignment */
  pendingRoleProvider?: string;
  /** Model list fetched for the current picker (array index = callback data) */
  modelList?: string[];
  /** Provider list for role provider picker */
  providerList?: string[];
  /** Provider selected during smart routing add-model flow */
  pendingSrProvider?: string;
  /** Whether expecting a plain-text message next */
  pendingInput?: PendingInput;

  /** Last interaction — for TTL eviction */
  lastActivity: number;
}

// ─── State manager ────────────────────────────────────────────────

const MENU_TTL_MS = 10 * 60 * 1000; // 10 minutes inactivity

export class MenuStateManager {
  private states: Map<number, MenuState> = new Map();
  private cleanupInterval: ReturnType<typeof setInterval>;

  constructor() {
    // Evict stale menu sessions every 5 minutes
    this.cleanupInterval = setInterval(() => this.evict(), 5 * 60 * 1000);
    this.cleanupInterval.unref();
  }

  get(userId: number): MenuState | undefined {
    return this.states.get(userId);
  }

  set(state: MenuState): void {
    state.lastActivity = Date.now();
    this.states.set(state.userId, state);
  }

  touch(userId: number): void {
    const s = this.states.get(userId);
    if (s) s.lastActivity = Date.now();
  }

  delete(userId: number): void {
    this.states.delete(userId);
  }

  /** Move to a new screen, pushing the previous onto breadcrumb */
  navigate(userId: number, screen: MenuScreen): MenuState | undefined {
    const s = this.states.get(userId);
    if (!s) return undefined;
    s.breadcrumb.push(s.screen);
    s.screen = screen;
    s.lastActivity = Date.now();
    // Clear paginated sub-state on navigation
    s.modelList = undefined;
    s.pendingInput = undefined;
    return s;
  }

  /** Go back one screen in the breadcrumb */
  back(userId: number): MenuState | undefined {
    const s = this.states.get(userId);
    if (!s) return undefined;
    const prev = s.breadcrumb.pop() ?? "main";
    s.screen = prev;
    s.lastActivity = Date.now();
    s.pendingInput = undefined;
    return s;
  }

  private evict(): void {
    const now = Date.now();
    for (const [uid, s] of this.states) {
      if (now - s.lastActivity > MENU_TTL_MS) {
        this.states.delete(uid);
      }
    }
  }

  destroy(): void {
    clearInterval(this.cleanupInterval);
    this.states.clear();
  }
}
