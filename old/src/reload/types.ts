/**
 * StackOwl — Hot Reload Types
 *
 * Defines the reloadable module abstraction.
 * Any module that implements ReloadableModule can be tracked and hot-reloaded
 * with dependency-aware ordering and rollback on failure.
 */

// ─── Reloadable Module Kinds ───────────────────────────────────

export type ReloadableKind =
  | "tool"
  | "skill"
  | "plugin"
  | "config"
  | "connector";

// ─── Reloadable Module Interface ───────────────────────────────

export interface ReloadableModule {
  /** Unique identifier for this module */
  readonly id: string;
  /** What kind of module this is */
  readonly kind: ReloadableKind;
  /** Path to the file being watched */
  readonly filePath: string;
  /** Monotonically increasing version counter */
  version: number;
  /** IDs of modules this depends on */
  readonly dependsOn: string[];

  /**
   * Validate the new version before loading.
   * Returns true if the module can be safely loaded.
   */
  validate(): Promise<boolean>;

  /**
   * Load/reload the module. Called after validate() returns true.
   */
  load(): Promise<void>;

  /**
   * Clean teardown. Called before reload or on removal.
   */
  unload(): Promise<void>;

  /**
   * Capture current state for rollback.
   */
  snapshot(): ModuleSnapshot;

  /**
   * Restore from a previous snapshot.
   */
  restore(snapshot: ModuleSnapshot): Promise<void>;
}

// ─── Module Snapshot ───────────────────────────────────────────

export interface ModuleSnapshot {
  moduleId: string;
  version: number;
  /** Serialized module state (tool definition, skill content, etc.) */
  state: unknown;
  timestamp: number;
}

// ─── Reload Event ──────────────────────────────────────────────

export interface ReloadEvent {
  moduleId: string;
  kind: ReloadableKind;
  action: "reload" | "add" | "remove";
  success: boolean;
  error?: string;
  rolledBack: boolean;
  durationMs: number;
}
