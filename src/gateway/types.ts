/**
 * StackOwl — Gateway Types
 *
 * The Gateway is the single normalized interface between the OwlEngine
 * and every external channel (Telegram, CLI, Web, WhatsApp, Discord, ...).
 *
 * Every channel speaks the same language:
 *   IncomingMessage → [GatewayMessage] → OwlGateway → [GatewayResponse] → OutgoingMessage
 *
 * Adding a new channel = implementing ChannelAdapter. Nothing else changes.
 */

// ─── Incoming ────────────────────────────────────────────────────

/**
 * A normalized inbound message from any channel.
 * The channel adapter is responsible for filling these fields.
 */
export interface GatewayMessage {
  /** Unique ID for this message (uuid or platform message id) */
  id: string;
  /** Which channel this came from — "telegram" | "cli" | "web" | "whatsapp" | ... */
  channelId: string;
  /** Platform-specific user identifier (Telegram chat ID, "local" for CLI, socket id for web) */
  userId: string;
  /** Derived session key — usually `${channelId}:${userId}` */
  sessionId: string;
  /** The raw text of the message */
  text: string;
}

// ─── Callbacks ───────────────────────────────────────────────────

/**
 * Per-message callbacks the channel adapter provides.
 * The gateway calls these during processing to stream updates back
 * without the channel needing to poll.
 */
export interface GatewayCallbacks {
  /** Called with intermediate progress updates (typing indicators, tool status) */
  onProgress?: (text: string) => Promise<void>;
  /** Called when the engine wants to send a file/image to the user */
  onFile?: (filePath: string, caption?: string) => Promise<void>;
  /** Called when tool synthesis needs npm deps — adapter decides how to prompt user */
  askInstall?: (deps: string[]) => Promise<boolean>;
}

// ─── Outgoing ────────────────────────────────────────────────────

/**
 * A normalized outbound response from the gateway.
 * The channel adapter formats this for its platform (HTML, MarkdownV2, ANSI, etc).
 */
export interface GatewayResponse {
  content: string;
  owlName: string;
  owlEmoji: string;
  toolsUsed: string[];
  usage?: { promptTokens: number; completionTokens: number };
}

// ─── Channel Adapter Interface ───────────────────────────────────

/**
 * What every channel must implement.
 * The adapter owns all platform-specific transport concerns:
 *   - How to receive messages
 *   - How to format and deliver responses
 *   - How to show progress (typing indicators, etc)
 *
 * The adapter does NOT contain business logic — that all lives in OwlGateway.
 */
export interface ChannelAdapter {
  /** Unique channel identifier — "telegram", "cli", "web", etc. */
  readonly id: string;
  /** Human-readable name for logging */
  readonly name: string;

  /**
   * Called by the gateway to deliver a proactive message to a specific user.
   * (Used for morning briefs, check-ins, heartbeat pings.)
   */
  sendToUser(userId: string, response: GatewayResponse): Promise<void>;

  /**
   * Called by the gateway to broadcast a message to all active users on this channel.
   */
  broadcast(response: GatewayResponse): Promise<void>;

  /** Start the channel (connect to platform, begin listening). */
  start(): Promise<void>;

  /** Graceful shutdown. */
  stop(): void;
}

// ─── Gateway Context ─────────────────────────────────────────────

/**
 * All dependencies the OwlGateway needs.
 * Passed once at construction time.
 */
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { SessionStore } from "../memory/store.js";
import type { PelletStore } from "../pellets/store.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { EvolutionHandler } from "../evolution/handler.js";
import type { OwlEvolutionEngine } from "../owls/evolution.js";
import type { LearningEngine } from "../learning/self-study.js";
import type { InstinctRegistry } from "../instincts/registry.js";
import type { InstinctEngine } from "../instincts/engine.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { SkillsLoader } from "../skills/index.js";
import type { ReflexionEngine } from "../evolution/reflexion.js";
import type { ProviderRegistry } from "../providers/registry.js";

export interface GatewayContext {
  provider: ModelProvider;
  owl: OwlInstance;
  owlRegistry: OwlRegistry;
  config: StackOwlConfig;
  toolRegistry?: ToolRegistry;
  sessionStore: SessionStore;
  pelletStore?: PelletStore;
  capabilityLedger?: CapabilityLedger;
  evolution?: EvolutionHandler;
  evolutionEngine?: OwlEvolutionEngine;
  learningEngine?: LearningEngine;
  instinctRegistry?: InstinctRegistry;
  instinctEngine?: InstinctEngine;
  memoryContext?: string;
  preferenceStore?: PreferenceStore;
  reflexionEngine?: ReflexionEngine;
  skillsLoader?: SkillsLoader;
  cwd?: string;
  providerRegistry?: ProviderRegistry;
}
