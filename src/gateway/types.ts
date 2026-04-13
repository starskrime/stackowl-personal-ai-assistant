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
  /** Called when tool synthesis needs npm deps — adapter decides how to prompt user */
  askInstall?: (deps: string[]) => Promise<boolean>;
  /**
   * Called with fine-grained streaming events during model generation.
   * Enables real-time text streaming and live tool status in channels.
   * If not provided, the engine falls back to onProgress for status updates.
   */
  onStreamEvent?: (event: StreamEvent) => Promise<void>;
  /**
   * When true, suppress internal reasoning/thinking messages from being
   * delivered to the user (e.g. _Thinking..._ output from the ReAct loop).
   * The user only sees final answers, not intermediate reasoning traces.
   */
  suppressThinking?: boolean;
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
  /** Estimated cost for this response (populated when cost tracking is enabled) */
  estimatedCostUsd?: number;
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

  /**
   * Called by the gateway to deliver a file to a specific user.
   * Adapters that don't support file delivery can omit this.
   */
  deliverFile?(
    userId: string,
    filePath: string,
    caption?: string,
  ): Promise<void>;
}

// ─── Gateway Context ─────────────────────────────────────────────

/**
 * All dependencies the OwlGateway needs.
 * Passed once at construction time.
 */
import type { ModelProvider, StreamEvent } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { SessionStore } from "../memory/store.js";
import type { PelletStore } from "../pellets/store.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { EvolutionHandler } from "../evolution/handler.js";
import type { OwlEvolutionEngine } from "../owls/evolution.js";
import type { LearningEngine } from "../learning/self-study.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { OwlInnerLife } from "../owls/inner-life.js";
import type { InstinctRegistry } from "../instincts/registry.js";
import type { InstinctEngine } from "../instincts/engine.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { SkillsLoader } from "../skills/index.js";
import type { ReflexionEngine } from "../evolution/reflexion.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { ContextMesh } from "../ambient/mesh.js";
import type { TrustChain } from "../trust/chain.js";
import type { KnowledgeGraph } from "../knowledge/graph.js";
import type { KnowledgeReasoner } from "../knowledge/reasoner.js";
import type { TimelineManager } from "../timeline/manager.js";
import type { CollabSessionManager } from "../collab/session-manager.js";
import type { CollabFacilitator } from "../collab/facilitator.js";
import type { PatternAnalyzer } from "../predictive/analyzer.js";
import type { PredictiveQueue } from "../predictive/queue.js";
import type { DemoRecorder } from "../forge/recorder.js";
import type { ForgeSynthesizer } from "../forge/synthesizer.js";
import type { SkillArena } from "../tournaments/arena.js";
import type { SwarmCoordinator } from "../swarm/coordinator.js";
import type { VoicePersona } from "../voice/persona.js";
import type { VoiceAdapter } from "../voice/adapter.js";
import type { MicroLearner } from "../learning/micro-learner.js";
import type { ProactiveAnticipator } from "../learning/anticipator.js";
import type { EventBus } from "../events/bus.js";
import type { TaskQueue } from "../queue/task-queue.js";
import type { CostTracker } from "../costs/tracker.js";
import type { AgentRegistry } from "../agents/types.js";
import type { RateLimiter } from "../ratelimit/limiter.js";
import type { PluginRegistry } from "../plugins/registry.js";
import type { ServiceRegistry } from "../plugins/services.js";
import type { HookPipeline } from "../plugins/hook-pipeline.js";
import type { HotReloadManager } from "../reload/manager.js";
import type { ACPRouter } from "../acp/router.js";
import type { IntentStateMachine, CommitmentTracker } from "../intent/index.js";
import type { ProactiveIntentionLoop } from "../intent/proactive-loop.js";
import type { GoalGraph } from "../goals/graph.js";
import type { UserPreferenceModel } from "../preferences/model.js";
import type { WorkingContextManager } from "../memory/working-context.js";
import type { EpisodicMemory } from "../memory/episodic.js";
import type { SelfLearningCoordinator } from "../learning/coordinator.js";
import type { FactStore } from "../memory/fact-store.js";
import type { FactExtractor } from "../memory/fact-extractor.js";
import type { MemoryRetriever } from "../memory/memory-retriever.js";
import type { MemoryFeedback } from "../memory/memory-feedback.js";
import type { FeedbackStore } from "../feedback/store.js";
import type { ConversationDigestManager } from "../memory/conversation-digest.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { MemoryBus } from "../memory/bus.js";

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
  learningOrchestrator?: LearningOrchestrator;
  innerLife?: OwlInnerLife;
  instinctRegistry?: InstinctRegistry;
  instinctEngine?: InstinctEngine;
  memoryContext?: string;
  preferenceStore?: PreferenceStore;
  reflexionEngine?: ReflexionEngine;
  skillsLoader?: SkillsLoader;
  cwd?: string;
  providerRegistry?: ProviderRegistry;

  // ─── New Feature Modules ──────────────────────────────────────
  contextMesh?: ContextMesh;
  trustChain?: TrustChain;
  knowledgeGraph?: KnowledgeGraph;
  knowledgeReasoner?: KnowledgeReasoner;
  timelineManager?: TimelineManager;
  collabManager?: CollabSessionManager;
  collabFacilitator?: CollabFacilitator;
  patternAnalyzer?: PatternAnalyzer;
  predictiveQueue?: PredictiveQueue;
  demoRecorder?: DemoRecorder;
  forgeSynthesizer?: ForgeSynthesizer;
  skillArena?: SkillArena;
  swarmCoordinator?: SwarmCoordinator;
  voicePersona?: VoicePersona;
  voiceAdapter?: VoiceAdapter;
  microLearner?: MicroLearner;
  anticipator?: ProactiveAnticipator;
  memorySearcher?: import("../memory-threads/searcher.js").MemorySearcher;
  echoChamberDetector?: import("../echo-chamber/detector.js").EchoChamberDetector;
  journalGenerator?: import("../growth-journal/generator.js").JournalGenerator;
  questManager?: import("../quests/manager.js").QuestManager;
  capsuleManager?: import("../capsules/manager.js").CapsuleManager;
  constellationMiner?: import("../constellations/miner.js").ConstellationMiner;
  socraticEngine?: import("../socratic/engine.js").SocraticEngine;

  // ─── Mem0-Inspired Memory Layer (Phase M1-M6) ────────────────
  factStore?: FactStore;
  factExtractor?: FactExtractor;
  memoryRetriever?: MemoryRetriever;
  memoryFeedback?: MemoryFeedback;
  memoryBus?: MemoryBus;

  // ─── Response Feedback (👍/👎) ────────────────────────────────
  feedbackStore?: FeedbackStore;

  // ─── Architecture Improvements ─────────────────────────────
  eventBus?: EventBus;
  taskQueue?: TaskQueue;
  costTracker?: CostTracker;
  agentRegistry?: AgentRegistry;
  rateLimiter?: RateLimiter;
  selfLearningCoordinator?: SelfLearningCoordinator;

  // ─── Plugin, Reload & ACP ─────────────────────────────────
  pluginRegistry?: PluginRegistry;
  serviceRegistry?: ServiceRegistry;
  hookPipeline?: HookPipeline;
  hotReloadManager?: HotReloadManager;
  acpRouter?: ACPRouter;

  // ─── Cognitive Loop (Self-Improvement) ──────────────────────
  cognitiveLoop?: import("../cognition/loop.js").CognitiveLoop;

  // ─── Conversational Ground State (Phase 4) ─────────────────
  groundState?: import("../cognition/ground-state.js").GroundStateView;

  // ─── Feature Modules (Phase 1-3) ──────────────────────────
  infraProfile?: import("../infra/profile.js").InfraProfileStore;
  infraDetector?: import("../infra/detector.js").InfraDetector;
  connectorResolver?: import("../connectors/resolver.js").ConnectorResolver;
  workflowStore?: import("../workflows/chain.js").WorkflowChainStore;
  healthChecker?: import("../monitoring/checker.js").HealthChecker;
  autoConfigDetector?: import("../infra/auto-config.js").AutoConfigDetector;
  runbookMiner?: import("../workflows/runbook-miner.js").RunbookMiner;
  crossAppPlanner?: import("../orchestrator/cross-app.js").CrossAppPlanner;
  knowledgeCouncil?: import("../parliament/knowledge-council.js").KnowledgeCouncil;
  intentStateMachine?: IntentStateMachine;
  commitmentTracker?: CommitmentTracker;
  preferenceModel?: UserPreferenceModel;
  workingContextManager?: WorkingContextManager;
  episodicMemory?: EpisodicMemory;
  goalGraph?: GoalGraph;
  proactiveLoop?: ProactiveIntentionLoop;

  // ─── L1 Working Memory (Conversation Digest) ────────────────
  digestManager?: ConversationDigestManager;

  // ─── SQLite Memory Database (replaces all JSON file stores) ──
  db?: MemoryDatabase;

  // ─── Message Compressor (Phase 2 — batch summarization) ──────
  compressor?: import("../memory/compressor.js").MessageCompressor;
}
