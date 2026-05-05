export type SignalSource =
  | "calendar"
  | "git"
  | "clipboard"
  | "active_file"
  | "time_of_day"
  | "system"
  | "weather"
  | "email"
  | "perch"
  | "heartbeat"
  | "user_pattern";

export type SignalPriority = "low" | "medium" | "high" | "critical";

export interface ContextSignal {
  id: string;
  source: SignalSource;
  priority: SignalPriority;
  title: string;
  content: string;
  timestamp: number;
  ttlMs: number;
  metadata?: Record<string, unknown>;
  /**
   * True only after a goal-conditioning verifier has classified the signal
   * as ADVANCES against an active goal. Drives AmbientContextLayer.shouldFire.
   */
  userSurfaceable?: boolean;
}

export type ConsentMap = Partial<Record<SignalSource, boolean>>;

/**
 * Default consent matrix. Sources missing from the user's config fall back to these.
 * Privacy-by-default: clipboard, email, calendar, weather are off until explicitly granted.
 */
export const DEFAULT_CONSENT: Required<ConsentMap> = {
  git: true,
  active_file: true,
  time_of_day: true,
  system: true,
  perch: true,
  heartbeat: true,
  user_pattern: true,
  clipboard: false,
  email: false,
  calendar: false,
  weather: false,
};

export interface SignalCollector {
  readonly source: SignalSource;
  readonly mode: "poll" | "push";
  /** Required when mode === "poll" */
  readonly intervalMs?: number;
  /** Required when mode === "poll" */
  collect?(): Promise<ContextSignal[]>;
  /** Required when mode === "push" */
  start?(emit: (signal: ContextSignal) => void): void;
  /** Required when mode === "push" */
  stop?(): void;
}

export interface MeshState {
  signals: ContextSignal[];
  lastUpdate: number;
  activeContext: string;
}

export interface AmbientRule {
  name: string;
  condition: (signals: ContextSignal[]) => boolean;
  action: "notify" | "inject" | "suggest";
  template: string;
  cooldownMs: number;
  lastFired?: number;
}
