/**
 * StackOwl — Plugin System Types
 *
 * Defines the plugin manifest, lifecycle, hooks, and managed plugin wrapper.
 * Plugins extend StackOwl via a scoped sandbox — never the full GatewayContext.
 */

import type { GatewayMessage, GatewayResponse } from "../gateway/types.js";
import type { MiddlewareContext } from "../gateway/middleware.js";

// ─── Plugin Manifest ───────────────────────────────────────────

export interface PluginManifest {
  /** Unique plugin name (kebab-case recommended) */
  name: string;
  /** Semver version string */
  version: string;
  /** Human-readable description */
  description: string;
  /** Author name or org */
  author?: string;

  /** What this plugin provides to the system */
  provides: {
    /** Tool names this plugin registers */
    tools?: string[];
    /** Skill names this plugin provides */
    skills?: string[];
    /** Named services other plugins can consume */
    services?: string[];
    /** ACP channels this plugin listens on */
    channels?: string[];
    /** Middleware names this plugin registers */
    middleware?: string[];
  };

  /** What this plugin requires to function */
  requires: {
    /** Other plugins this depends on */
    plugins?: PluginDependency[];
    /** Services from other plugins */
    services?: string[];
    /** Environment variables that must be set */
    env?: string[];
    /** Binaries that must exist on PATH */
    bins?: string[];
    /** Minimum Node.js version (semver range) */
    nodeVersion?: string;
  };

  /** JSON Schema for plugin-specific configuration */
  configSchema?: Record<string, unknown>;

  /** Relative path to the main module (from plugin root) */
  entryPoint: string;
}

export interface PluginDependency {
  name: string;
  /** Semver range, e.g. ">=1.0.0" */
  version?: string;
  /** If true, plugin loads even if this dep is missing */
  optional?: boolean;
}

// ─── Plugin State Machine ──────────────────────────────────────

export type PluginState =
  | "unloaded"
  | "initializing"
  | "initialized"
  | "starting"
  | "ready"
  | "stopping"
  | "stopped"
  | "destroying"
  | "error";

// ─── Plugin Hooks ──────────────────────────────────────────────

export interface PluginHooks {
  /** Intercept before engine processes message. Return response to short-circuit. */
  beforeEngine?(
    message: GatewayMessage,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse | null>;

  /** Transform response after engine processing */
  afterEngine?(
    message: GatewayMessage,
    response: GatewayResponse,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse>;

  /** Intercept and optionally transform tool call args before execution */
  beforeToolCall?(
    toolName: string,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;

  /** Intercept and optionally transform tool result after execution */
  afterToolCall?(
    toolName: string,
    result: string,
    args: Record<string, unknown>,
  ): Promise<string>;

  /** Called when a new session is created */
  onSessionCreated?(sessionId: string): Promise<void>;

  /** Called when a session ends */
  onSessionEnded?(sessionId: string): Promise<void>;

  /** Called when owl evolution is triggered */
  onEvolutionTriggered?(owlName: string): Promise<void>;

  /** Called when configuration changes (via hot reload) */
  onConfigChanged?(changes: Record<string, unknown>): Promise<void>;
}

// ─── Plugin Instance ───────────────────────────────────────────

export interface PluginInstance {
  readonly manifest: PluginManifest;
  readonly state: PluginState;
  readonly error?: Error;

  /** Initialize with scoped sandbox. Set up internal state. */
  init(sandbox: import("./sandbox.js").PluginSandbox): Promise<void>;

  /** Start the plugin (connect to APIs, start timers, etc.) */
  start(): Promise<void>;

  /** Called after ALL plugins have started. Cross-plugin init goes here. */
  ready?(): Promise<void>;

  /** Graceful shutdown. Stop timers, close connections. */
  stop(): Promise<void>;

  /** Final teardown. Release all resources. */
  destroy(): Promise<void>;

  /** Optional hook implementations */
  hooks?: PluginHooks;
}

// ─── Managed Plugin ────────────────────────────────────────────

export interface ManagedPlugin {
  manifest: PluginManifest;
  instance: PluginInstance;
  sandbox: import("./sandbox.js").PluginSandbox;
  state: PluginState;
  loadedAt: number;
  /** Path to the plugin directory */
  pluginDir: string;
}
