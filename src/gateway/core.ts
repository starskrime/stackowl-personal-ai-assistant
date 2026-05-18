/**
 * StackOwl — Owl Gateway (Core)
 *
 * The single point of entry for all incoming messages.
 * All business logic lives here:
 *   - Session management
 *   - Behavioral skill evaluation
 *   - ReAct engine execution
 *   - Capability gap detection + auto-synthesis
 *   - Post-processing: memory, learning, DNA evolution
 *
 * Channel adapters are pure transport — they call handle() and receive
 * a GatewayResponse. They don't know anything about owls, sessions, or tools.
 */

import { v4 as uuidv4 } from "uuid";
import type { ChatMessage } from "../providers/base.js";

import type { EngineContext, EngineResponse } from "../engine/runtime.js";
import { OwlEngine, EXHAUSTION_MARKER } from "../engine/runtime.js";
import { PromptOptimizer } from "../engine/prompt-optimizer.js";
import { IntelligenceRouter } from "../intelligence/router.js";
import { TierEscalationManager } from "../intelligence/escalation.js";
import { FactInvalidator } from "../intelligence/fact-invalidator.js";
import { SleepTimeConsolidator } from "../intelligence/sleep-time-consolidator.js";
import { AttemptLogRegistry } from "../memory/attempt-log.js";

import { SkillContextInjector } from "../skills/injector.js";
import { ClawHubClient } from "../skills/clawhub.js";
import { SkillInstallWizard, SkillsMenuWizard, type WizardSession } from "../skills/wizard.js";
import { SkillTracker } from "../skills/tracker.js";
import { log } from "../logger.js";
import { OutcomeVerifier } from "../verification/outcome-verifier.js";
import { FalseDoneDetector } from "../verification/false-done-detector.js";
import { CompletionTracker } from "../verification/completion-tracker.js";
import { EscalationHandler } from "../verification/escalation-handler.js";
// MemoryConsolidator, MemoryReflexionEngine, DomainExpertiseTracker, MicroLearner,
// ProactiveAnticipator retired — all learning/ modules removed.
import { PreferenceDetector } from "../preferences/detector.js";
import { classifyStrategy } from "../orchestrator/classifier.js";
import { TaskOrchestrator } from "../orchestrator/orchestrator.js";
import { SecretaryRouter } from "../routing/secretary.js";
import type {
  GatewayMessage,
  GatewayResponse,
  GatewayCallbacks,
  ChannelAdapter,
  GatewayContext,
} from "./types.js";
import { SessionManager } from "./session-manager.js";
import type { ISessionManager } from "./session-manager.js";
import type { Session } from "../memory/store.js";
import { LifecycleCoordinator } from "./lifecycle-coordinator.js";
import type { ILifecycleCoordinator } from "./lifecycle-coordinator.js";
import { FeatureCommandRouter } from "./feature-command-router.js";
import type { IFeatureCommandRouter } from "./feature-command-router.js";
import { TrustTimelineCommandHandler } from "./commands/trust-timeline-handler.js";
import { CollabSessionCommandHandler } from "./commands/collab-session-handler.js";
import { KnowledgeCommandHandler } from "./commands/knowledge-handler.js";
import { MiscCommandHandler } from "./commands/misc-handler.js";
import type { GatewayMiddleware, MiddlewareContext } from "./middleware.js";
import { RateLimitMiddleware, LoggingMiddleware } from "./middleware.js";
import { getReadyMessages } from "../tools/utils/timer.js";
import { PostProcessor } from "./handlers/post-processor.js";
import { ContextBuilder } from "./handlers/context-builder.js";
import { GapLearner } from "../agent/gap-learner.js";
import { InnerLifeDNABridge } from "../owls/inner-bridge.js";
import { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import { TaskQueue } from "../queue/task-queue.js";
import { llmTaskQueue } from "../queue/llm-task-queue.js";
import {
  computeTemporalContext,
  loadPreviousSession,
} from "../cognition/temporal-context.js";
import {
  classifyContinuity,
  type ContinuityResult,
} from "../cognition/continuity-engine.js";
import { UserMentalModel } from "../cognition/user-mental-model.js";
import { CognitivePipeline } from "../cognition/cognitive-pipeline.js";
import { MemoryDatabase } from "../memory/db.js";
import { FeedbackStore } from "../feedback/store.js";
import { OutputFilter, resolveOutputMode } from "./output-filter.js";
import { SessionBriefGenerator } from "../cognition/session-brief.js";
import { LoopDetector } from "../cognition/loop-detector.js";
import { IntentClarifier } from "../clarification/intent-clarifier.js";
import { ClarificationCoordinator } from "../clarification/coordinator.js";
import { SessionAutonomyBias } from "../clarification/session-autonomy-bias.js";
import { join } from "node:path";
import { ToolMastery } from "../tools/tool-mastery.js";
import { FallbackSequencer } from "../tools/fallback-sequencer.js";
import { DomainToolMap } from "../delegation/domain-tool-map.js";
import { TaskDecomposer } from "../delegation/decomposer.js";
import { ResultSynthesizer } from "../delegation/result-synthesizer.js";
import { ParliamentAutoTrigger } from "../parliament/auto-trigger.js";
import { TopicWorthinessEvaluator } from "../parliament/topic-worthiness.js";
import { MultiRoundDebateManager } from "../parliament/multi-round-debate.js";
import { DebatePelletGenerator } from "../parliament/debate-pellet-generator.js";
import { RoutingWirer } from "../parliament/routing-wirer.js";
// ParliamentSession import removed — now handled by ParliamentSubsystem
import { InstinctRegistry } from "../instincts/registry.js";
import { InstinctEngine, InstinctEngineV2 } from "../instincts/engine.js";

import { ChannelRegistry } from "./channel-registry.js";
import { GatewayEventBus } from "./event-bus.js";
import { DeliveryRouter } from "./delivery-router.js";
import { ChannelAdapterV1Shim, defaultCapsForV1 } from "./adapter-v1-shim.js";
import { SessionService } from "../session/service.js";
import { UserMemoryStore } from "../session/user-memory-store.js";
// migrateJsonSessionsToSQLite removed — sessionStore retired
import { OwlBrain, type OwlBrainResult } from "../routing/owl-brain.js";
import { UserProfileService } from "../routing/user-profile-service.js";
import { TaskOwnershipManager } from "../routing/task-ownership-manager.js";
import { RoutingStatusReporter } from "../routing/routing-status-reporter.js";
import { BackgroundJobRunner } from "../routing/background-job-runner.js";
import { RelationshipContext } from "../routing/relationship-context.js";
import { OpinionInjector } from "../owls/opinion-injector.js";
import { createContextPipeline } from "../context/index.js";
import { UserPersonaSynthesizer } from "../context/user-persona-synthesizer.js";
import { UnifiedMemoryRetriever } from "../context/unified-memory-retriever.js";
import { ContextCache } from "../context/cache.js";
import { OwlOrchestrator as OwlOrchestratorV2 } from "../engine/orchestrator.js";
import { ImprovementScheduler } from "../engine/improvement-scheduler.js";
import { OutcomeJournal as OutcomeJournalV2 } from "../engine/outcome-journal.js";
import { ReflexionEngine as IntelligenceReflexionEngine } from "../intelligence/reflexion-engine.js";
// ReflexionEngine from evolution/reflexion removed — no longer auto-initialized
// updatePelletGeneratorDNA removed — pelletStore deleted in memory refactor
import { GoalVerifier } from "../tools/goal-verifier.js";
// TaskLedgerStore import removed — now handled by ParliamentSubsystem
// SubGoal import removed — now handled by ParliamentSubsystem
import { createInvokeSkillTool } from "../tools/invoke-skill.js"
import { dispatchSkillCommand } from "./commands/skill-router.js";
import { SkillCreationWizard } from "./wizards/skill-creation.js";
import { buildDefaultIntelligenceConfig, saveConfig } from "../config/loader.js";
import { ProviderManager } from "../providers/manager.js";
import { ProgressManager } from "../progress/manager.js";
import { withSpan, attachToContext } from "../infra/observability/context.js";
import {
  registerCapability,
  snapshotLog,
  getDegradedCapabilities,
} from "../infra/capability-registry.js";
import { runPreDeliveryGate } from "./pre-delivery-gate.js";
import { BmadAgentLoader } from "../owls/bmad-agent-loader.js";
import { ParliamentSubsystem } from "./parliament-subsystem.js";
import type { IParliamentSubsystem } from "./parliament-subsystem.js";
import { ProactiveDeliveryService } from "./proactive-delivery-service.js";
import type { IProactiveDeliveryService } from "./proactive-delivery-service.js";

// ─── Utility functions ───────────────────────────────────────────

export function shuffleArray<T>(arr: T[]): T[] {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

/**
 * CI-3: NL skill install intent detection.
 * Uses IntelligenceRouter (classification tier) + cheap provider with a 200ms
 * timeout.  Returns false on timeout or any error (fail-open).
 */
async function isSkillInstallIntent(
  text: string,
  ctx: GatewayContext,
): Promise<boolean> {
  const provider = ctx.provider;
  if (!provider) return false;

  let model: string | undefined;
  try {
    const resolved = ctx.intelligence?.resolve("classification");
    model = resolved?.model;
  } catch (err) {
    log.engine.warn("isSkillInstallIntent: intelligence resolution failed, using provider default", err);
  }

  const prompt =
    `Does this message ask to create, make, add, or build a new skill or automation? ` +
    `Reply YES or NO only. Message: ${text}`;

  const timeout = new Promise<{ timedOut: true }>((resolve) =>
    setTimeout(() => resolve({ timedOut: true }), 200),
  );
  const call = provider.chat(
    [{ role: "user", content: prompt }],
    model,
    { temperature: 0 },
  );

  let raced: any;
  try {
    raced = await Promise.race([call, timeout]);
  } catch (err) {
    log.engine.warn("isSkillInstallIntent: provider call failed", err);
    return false;
  }

  if (raced && (raced as any).timedOut) return false;

  const answer: string = (raced as { content: string }).content ?? "";
  return answer.trimStart().toUpperCase().startsWith("YES");
}

// ─── Constants ───────────────────────────────────────────────────

const MAX_SESSION_HISTORY = 50;

// ─── Gateway ─────────────────────────────────────────────────────

export class OwlGateway {
  private engine: OwlEngine;
  private adapters: Map<string, ChannelAdapter> = new Map();
  readonly channelRegistry: ChannelRegistry = new ChannelRegistry();
  readonly gatewayEventBus: GatewayEventBus = new GatewayEventBus();
  readonly deliveryRouter: DeliveryRouter = new DeliveryRouter(this.channelRegistry);
  private sessionManager!: ISessionManager;
  private skillInjector: SkillContextInjector | null = null;
  /** Singleton PreferenceDetector — avoids re-constructing on every message */
  private preferenceDetector: PreferenceDetector | null = null;
  /** Lazy-initialized task orchestrator for multi-strategy execution */
  private taskOrchestrator: TaskOrchestrator | null = null;
  /** Agent Watch — supervises external coding agent sessions */
  agentWatch: import("../agent-watch/index.js").AgentWatchManager | null = null;
  /** Lifecycle coordinator — owns all process signals, timers, and shutdown callbacks */
  private readonly lifecycle: ILifecycleCoordinator;
  private readonly featureRouter: IFeatureCommandRouter;
  private readonly parliamentSubsystem: IParliamentSubsystem;
  /** Proactive delivery service — per-session activity tracking and scheduled message routing */
  private readonly proactiveSvc: IProactiveDeliveryService;

  // ─── Phase 3: Relational Intelligence ────────────────────────
  private sessionBriefGenerator: SessionBriefGenerator | null = null;
  private loopDetector: LoopDetector = new LoopDetector();

  // ─── Element 9: Clarification Modules ─────────────────────────────
  readonly intentClarifier: IntentClarifier;
  private readonly clarificationCoordinator: ClarificationCoordinator;

  // ─── Epic 4: Tool Mastery Modules ─────────────────────────────
  readonly toolMastery: ToolMastery;
  fallbackSequencer!: FallbackSequencer;
  readonly domainToolMap: DomainToolMap;
  taskDecomposer: TaskDecomposer | null = null;
  resultSynthesizer: ResultSynthesizer | null = null;

  // ─── OpinionInjector (G5) — stateless, one instance per gateway ─
  private readonly opinionInjector = new OpinionInjector();

  // ─── Extracted Handlers (Improvement #4) ───────────────────
  private postProcessor: PostProcessor;
  private contextBuilder: ContextBuilder;
  private taskQueue: TaskQueue;
  private gapLearner: GapLearner | null = null;
  private secretaryRouter: SecretaryRouter | null = null;
  private owlBrain: OwlBrain | null = null;

  // ─── OwlEngine v2 (Element 6a) ──────────────────────────────
  private owlOrchestratorV2: OwlOrchestratorV2 | null = null;
  private improvementScheduler: ImprovementScheduler | null = null;

  /**
   * Lane Queue — one active Promise per session key.
   * Guarantees serial execution: a second message from the same session
   * waits for the first to finish before starting.
   * This prevents race conditions on session history and memory state.
   */
  private lanes: Map<string, Promise<unknown>> = new Map();
  private middleware: GatewayMiddleware[] = [];

  /**
   * Stuck-task tracker — counts consecutive exhausted responses per session.
   * When a session returns EXHAUSTION_MARKER N times in a row, the gateway
   * replaces the response with a structured escalation asking the user to
   * clarify, pivot, or accept the task can't be done.
   */
  private stuckStreak: Map<string, number> = new Map();
  private static readonly STUCK_THRESHOLD = 3;

  /**
   * Per-session tier escalation managers.
   * Tracks low→mid→high floor with 15-min auto-reset.
   */
  private escalationManagers: Map<string, TierEscalationManager> = new Map();

  /**
   * Cross-turn attempt logs — one per active session.
   * Persists across handle() calls so the model always knows what was
   * already tried in previous messages of this conversation.
   */
  private attemptLogs = new AttemptLogRegistry();
  private wizardSessions = new Map<string, WizardSession>();
  private skillCreationWizard: SkillCreationWizard | null = null;

  /** User mental model — infers user state from behavioral signals */
  private userMentalModel: UserMentalModel | null = null;

  // Epic 5 memory modules (PriorContextRetriever, CrossSessionStore, PreferenceRecognizer) removed.

  // ─── Epic 6: Parliament Module Instances ──────────────────
  private parliamentAutoTrigger: ParliamentAutoTrigger | null = null;
  private topicWorthiness: TopicWorthinessEvaluator | null = null;
  private multiRoundDebate: MultiRoundDebateManager | null = null;
  private debatePelletGenerator: DebatePelletGenerator | null = null;
  private routingWirer: RoutingWirer | null = null;
  private goalVerifier: GoalVerifier | null = null;

  // ─── Instincts ────────────────────────────────────────────────
  private instinctRegistry: InstinctRegistry = new InstinctRegistry();
  private instinctEngine: InstinctEngine | null = null;
  private instinctEngineV2 = new InstinctEngineV2();

  // ─── Provider Manager (lazy singleton) ────────────────────────
  private _providerManager?: ProviderManager;

  // ─── Progress Manager (lazy singleton) ────────────────────────
  private _progressManager?: ProgressManager;

// domainExpertise removed — DomainExpertiseTracker deleted in learning/ refactor.

// ─── Epic 2: Verification Modules ──────────────────────────────
  private outcomeVerifier: OutcomeVerifier | null = null;
  private falseDoneDetector: FalseDoneDetector | null = null;
  private completionTracker: CompletionTracker | null = null;
  private escalationHandler: EscalationHandler | null = null;

  /**
   * Pending feedback contexts — keyed by feedbackId sent to channel adapters.
   * Adapters register a context after sending a response, then call recordFeedback()
   * when the user presses 👍/👎. Entries expire after 24 hours.
   */
  private pendingFeedback: Map<
    string,
    {
      sessionId: string;
      userId: string;
      userMessage: string;
      assistantSummary: string;
      toolsUsed: string[];
      createdAt: number;
    }
  > = new Map();

  constructor(public ctx: GatewayContext) {
    this.engine = new OwlEngine();
    this.sessionManager = ctx.sessionManager ?? new SessionManager(ctx);
    this.lifecycle = ctx.lifecycleCoordinator ?? new LifecycleCoordinator();

    // ─── Feature Command Router ─────────────────────────────────
    this.featureRouter = ctx.featureCommandRouter ?? (() => {
      const r = new FeatureCommandRouter();
      r.register(new TrustTimelineCommandHandler());
      r.register(new CollabSessionCommandHandler());
      r.register(new KnowledgeCommandHandler());
      r.register(new MiscCommandHandler());
      return r;
    })();

    this.parliamentSubsystem = ctx.parliamentSubsystem ?? new ParliamentSubsystem(ctx);

    this.proactiveSvc = ctx.proactiveDeliveryService
      ?? new ProactiveDeliveryService({ adapters: this.adapters, owl: ctx.owl });

    // Initialize task queue (Improvement #2)
    this.taskQueue = ctx.taskQueue ?? new TaskQueue(ctx.config.queue);

    // Gap learner disabled — pelletStore removed; GapLearner requires refactoring
    // this.gapLearner = null;

    // Ensure DNA is persisted on process exit.
    // Without this, any mutations from the current session are lost when the
    // process exits normally (ctrl-c, pm2 restart, etc.).
    this.lifecycle.register("dna-save", async () => {
      if (ctx.owlRegistry) {
        const owl = ctx.owlRegistry.getDefault?.() ?? ctx.owl;
        await ctx.owlRegistry.saveDNA(owl.persona.name).catch((err: Error) => {
          log.gateway.error("LifecycleCoordinator: saveDNA on exit failed", err, {});
        });
      }
    });

    // Preference detector — created once if preference store is configured
    if (ctx.preferenceStore) {
      this.preferenceDetector = new PreferenceDetector(ctx.provider);
    }

    // MicroLearner and ProactiveAnticipator retired — all learning/ modules removed.

    // SkillCreationWizard — channel-agnostic skill creation via ChannelAdapterV2.ask()
    this.skillCreationWizard = new SkillCreationWizard(
      ctx.cwd ?? process.cwd(),
      ctx.db,
    );

    // Phase 3: Session Brief Generator — lazy, only needs provider
    this.sessionBriefGenerator = new SessionBriefGenerator(ctx.provider);

    // User mental model — behavioral state inference
    this.userMentalModel = new UserMentalModel();

    // ─── Element 9: Initialize Clarification Modules ────────────
    const _clarificationRouter = ctx.intelligence ?? new IntelligenceRouter(
      { tiers: { high: { provider: ctx.config.defaultProvider, model: ctx.config.defaultModel }, mid: { provider: ctx.config.defaultProvider, model: ctx.config.defaultModel }, low: { provider: ctx.config.defaultProvider, model: ctx.config.defaultModel } }, defaults: {} },
      ctx.config.defaultProvider,
      ctx.config.defaultModel,
    );
    this.clarificationCoordinator = new ClarificationCoordinator();
    this.intentClarifier = new IntentClarifier(ctx.provider, _clarificationRouter, this.clarificationCoordinator);

    // ─── Epic 4: Initialize Tool Mastery Modules ──────────────
    this.toolMastery = new ToolMastery();
    // FallbackSequencer constructed below after ctx.db is guaranteed
    this.domainToolMap = new DomainToolMap();
    if (ctx.provider) {
      this.taskDecomposer = new TaskDecomposer(ctx.provider);
      this.resultSynthesizer = new ResultSynthesizer(ctx.provider);
    }

    log.engine.info("[Epic 3&4] Clarification and Tool Mastery modules initialized");

    // ─── Intelligence Router (tiered model routing) ────────────
    {
      const intelligenceConfig = ctx.config.intelligence
        ?? buildDefaultIntelligenceConfig(ctx.config.defaultProvider, ctx.config.defaultModel);
      ctx.intelligence = new IntelligenceRouter(
        intelligenceConfig,
        ctx.config.defaultProvider,
        ctx.config.defaultModel,
        () => {
          const check = ctx.costTracker?.checkBudget();
          return {
            dailyRemainingUsd: check?.dailyRemainingUsd ?? Infinity,
            maxDailyUsd: (ctx.config.costs?.budget as any)?.maxDailyUsd ?? 0,
          };
        },
      );
      log.engine.info(
        ctx.config.intelligence
          ? "[IntelligenceRouter] Tiered model routing active"
          : "[IntelligenceRouter] Using default pass-through config (no intelligence block in config)",
      );

      // Wire health policy into ProviderRegistry circuit breakers
      if (ctx.providerRegistry && ctx.config.intelligence?.healthPolicy) {
        ctx.providerRegistry.setHealthPolicy(ctx.config.intelligence.healthPolicy);
      }
    }

    // Initialize extracted handlers (Improvement #4)
    // ContextBuilder is initialized after skillInjector below
    // InnerLifeDNABridge — connects inner life state to DNA mutations
    const innerLifeBridge = ctx.owlRegistry
      ? new InnerLifeDNABridge(ctx.owlRegistry)
      : null;

    let intelligenceReflexion: IntelligenceReflexionEngine | undefined;
    if (ctx.db && ctx.provider) {
      const embedFn = async (text: string): Promise<number[]> => {
        try { return (await ctx.provider.embed(text)).embedding; } catch (err) { log.engine.warn("embedding failed", err); return []; }
      };
      intelligenceReflexion = new IntelligenceReflexionEngine(ctx.db, ctx.provider, embedFn);
    }

    // reflexionEngine no longer auto-created (pelletStore + sessionStore removed)

    this.postProcessor = new PostProcessor(
      ctx,
      this.taskQueue,
      ctx.eventBus ?? null,
      null,
      null,
      ctx.costTracker ?? null,
      innerLifeBridge,
      intelligenceReflexion,
    );
    this.contextBuilder = new ContextBuilder(
      ctx,
      null,
      null,
      this.userMentalModel,
    );

    // Built-in middleware
    this.middleware.push(new LoggingMiddleware());
    if (ctx.config.gateway?.rateLimit) {
      this.middleware.push(
        new RateLimitMiddleware(ctx.config.gateway.rateLimit),
      );
    }

    // Evict stale sessions from memory every 30 minutes.
    // Without this, a long-running Telegram bot accumulates one entry per user
    // in the sessions Map forever — a memory leak in production.
    setInterval(() => this.evictStaleSessions(), 30 * 60 * 1000).unref();

    // Initialize skill injector if skills are enabled
    if (ctx.skillsLoader) {
      const registry = ctx.skillsLoader.getRegistry();

      // Initialize skill tracker for usage analytics
      // Pass ctx.db if already provided; will be upgraded via setDb() after auto-init.
      const skillTracker = new SkillTracker(ctx.cwd ?? process.cwd(), ctx.db);
      if (!ctx.db) {
        skillTracker.load().catch((err) => { log.engine.warn("skillTracker load failed", err); }); // Non-blocking JSON load when no DB yet
      }

      // Use synthesis provider for skill routing LLM disambiguation
      let skillProvider = ctx.provider;
      if (ctx.providerRegistry) {
        try {
          skillProvider = ctx.providerRegistry.byRole("synthesizer");
        } catch {
          // No synthesizer role configured — silently use default provider
          skillProvider = ctx.provider;
        }
      }

      this.skillInjector = new SkillContextInjector(
        registry,
        {
          maxSkills: 5,
          autoSearchClawHub: true,
          clawHubTargetDir:
            ctx.config.skills?.directories?.[0] || join(ctx.cwd ?? process.cwd(), "skills"),
        },
        skillProvider,
        skillTracker,
        ctx.toolRegistry,
        ctx.cwd ?? process.cwd(),
      );

      // Optionally enable ClawHub search
      if (process.env.CLAWHUB_API_URL) {
        this.skillInjector.setClawHub(
          new ClawHubClient({
            registryUrl: process.env.CLAWHUB_API_URL,
          }),
        );
      }

      // Wire invoke_skill executor now that skillInjector is available (D5)
      if (ctx.toolRegistry && this.skillInjector) {
        ctx.toolRegistry.register(createInvokeSkillTool(this.skillInjector));
      }

      log.engine.info(
        `Skill injector initialized with ${registry.listEnabled().length} skills (BM25 + usage tracking)`,
      );
    }

    // Auto-initialize SQLite MemoryDatabase if not provided.
    // Opens workspace/memory/stackowl.db, creates all tables, imports existing JSON.
    if (!ctx.db) {
      const workspacePath = ctx.cwd ?? process.cwd();
      ctx.db = new MemoryDatabase(workspacePath);
      // One-time JSON migration (fire-and-forget, non-blocking)
      ctx.db
        .importFromJson(workspacePath)
        .catch((err) =>
          log.engine.warn(`[MemoryDatabase] JSON import failed: ${err}`),
        );
      log.engine.info("[memory] MemoryDatabase (SQLite) initialized");
    }
    this.deliveryRouter.setDb(ctx.db.rawDb);

    // Wire DB into SkillTracker now that ctx.db is guaranteed (upgrades from JSON fallback)
    if (this.skillInjector) {
      this.skillInjector.getTracker().setDb(ctx.db);
    }

    // FallbackSequencer needs MemoryDatabase — construct now that ctx.db exists
    this.fallbackSequencer = new FallbackSequencer(ctx.db);

    // MessageCompressor removed — pending MemoryManager wiring for batch summarization

    // Auto-initialize SessionService + UserMemoryStore (SQLite-backed session management)
    if (ctx.db && ctx.providerRegistry && !ctx.sessionService) {
      const userMemoryStore = new UserMemoryStore(ctx.db, this.gatewayEventBus);
      ctx.userMemoryStore = userMemoryStore;

      ctx.sessionService = new SessionService(
        ctx.db,
        userMemoryStore,
        ctx.intelligence,
        ctx.providerRegistry,
        ctx.config.defaultProvider ?? "openai",
        ctx.config.defaultModel ?? "gpt-4o-mini",
      );
      log.engine.info("[memory] SessionService initialized (SQLite-backed)");

      // JSON→SQLite migration skipped — sessionStore removed
    }

    // ─── OwlBrain (Element 4 — routing coordinator) ───────────────
    if (ctx.db) {
      const userProfileSvc = new UserProfileService(
        ctx.db,
        ctx.goalGraph ?? undefined,
        ctx.userMemoryStore ?? undefined,
      );
      ctx.userProfileService = userProfileSvc;
      ctx.taskOwnershipManager = new TaskOwnershipManager(ctx.db);
      ctx.routingStatusReporter = new RoutingStatusReporter(ctx.db);
      this.owlBrain = new OwlBrain(
        ctx.specializedRegistry,
        ctx.db,
        ctx.owl.persona.name,
        userProfileSvc,
        undefined, // digestManager removed from GatewayContext
      );
      this.owlBrain.setSecretaryRouterGetter(() => this.secretaryRouter);
      ctx.owlBrain = this.owlBrain;
      if (ctx.provider) {
        const intelligenceRouter = ctx.intelligence;
        const provider = ctx.provider;
        this.owlBrain.setClassifyFn(async (prompt: string) => {
          try {
            const resolvedModel = intelligenceRouter?.resolve("classification");
            const resp = await provider.chat(
              [{ role: "user", content: prompt }],
              resolvedModel?.model,
              { temperature: 0, maxTokens: 200 },
            );
            return resp.content;
          } catch (err) {
            log.engine.warn("OwlBrain: routing LLM call failed", err);
            return JSON.stringify({ targeted: null, confidence: 0 });
          }
        });
      }
      log.engine.info("[OwlBrain] Initialized");
    }

    // ─── Phase 2: Background jobs + relationship (Element 4) ──────
    if (ctx.db) {
      ctx.backgroundJobRunner = new BackgroundJobRunner(ctx.db, ctx.eventBus ?? null);
      ctx.backgroundJobRunner.start();
      ctx.relationshipContext = new RelationshipContext(
        ctx.db,
        ctx.goalGraph ?? undefined,
        undefined, // episodicMemory removed
        ctx.userMemoryStore ?? undefined,
      );
      log.engine.info("[BackgroundJobRunner + RelationshipContext] Initialized");
    }

    // ─── Element 5: ContextPipeline ───────────────────────────────
    if (!ctx.contextPipeline && ctx.db) {
      const userPersonaSynthesizer = new UserPersonaSynthesizer(ctx.provider, ctx.db);
      const unifiedMemoryRetriever = new UnifiedMemoryRetriever(ctx.memoryManager);
      const contextCache = new ContextCache();
      ctx.contextPipeline = createContextPipeline({ userPersonaSynthesizer, unifiedMemoryRetriever, contextCache, db: ctx.db });
      ctx.contextCache = contextCache;
      ctx.userPersonaSynthesizer = userPersonaSynthesizer;

      // Wire EventBus cache invalidation
      if (ctx.eventBus) {
        ctx.eventBus.on("pellet:written",    () => contextCache.invalidate("BehavioralPatchLayer"));
        ctx.eventBus.on("persona:refreshed", (e) => contextCache.invalidateUser(e.userId));
        ctx.eventBus.on("learning:recorded", () => contextCache.invalidate("OwlLearningsLayer"));
        ctx.eventBus.on("session:ended",     (e) => contextCache.invalidateUser((e as any).userId ?? e.sessionId));
      }

      // Wire FactInvalidator — marks contradicted facts invalid on fact:extracted
      if (ctx.db) {
        const factInvalidator = new FactInvalidator(ctx.db);
        this.gatewayEventBus.on("fact:extracted", (e) => {
          factInvalidator.check(e.factText, e.userId).catch((err) => { log.engine.warn("factInvalidator check failed", err); });
        });
        log.engine.debug("[FactInvalidator] Subscribed to fact:extracted");
      }

      // Wire SleepTimeConsolidator — surfaces cross-session insights on session:ended
      if (ctx.db && ctx.provider) {
        const sleepConsolidator = new SleepTimeConsolidator(ctx.db, ctx.provider);
        this.gatewayEventBus.on("session:ended", (e) => {
          sleepConsolidator.onSessionEnded(e.userId, e.sessionId).catch((err) => { log.engine.warn("sleepConsolidator onSessionEnded failed", err); });
        });
        log.engine.debug("[SleepTimeConsolidator] Subscribed to session:ended");
      }

      log.engine.info("[ContextPipeline] Element 5 pipeline initialized");
      registerCapability("contextPipeline", "FULL");
    } else if (!ctx.contextPipeline) {
      registerCapability("contextPipeline", "OFFLINE", "missing db");
    }

    // ─── Cognitive Pipeline (Dispatch → Execute → Consolidate) ───────
    if (!ctx.cognitivePipeline) {
      ctx.cognitivePipeline = new CognitivePipeline(ctx.provider);
      log.engine.info("[CognitivePipeline] Initialized — 3-call cognitive pipeline active");
    }
    // Wire persistent memory stores when all three are available
    if (ctx.db && ctx.memoryManager && ctx.preferenceStore) {
      ctx.cognitivePipeline.setStores({
        db: ctx.db,
        memoryManager: ctx.memoryManager,
        preferenceStore: ctx.preferenceStore,
        owlName: ctx.owl.persona.name,
        userId: "system",    // fallback only — per-request userId overrides in ConsolidateTurn
        channelId: "cli",    // fallback only — per-request channelId overrides in ConsolidateTurn
        dataDir: ctx.cwd ?? process.cwd(),
      });
      log.engine.info("[CognitivePipeline] Persistent stores wired — seeding + write-back active");
    } else {
      log.engine.info("[CognitivePipeline] Stores not available — in-memory Symbol Table only");
    }

    // ─── OwlEngine v2 (Element 6a): OwlOrchestrator + ImprovementScheduler ─
    // ctx.db is always available here (auto-initialized above if not provided)
    if (ctx.db) {
      this.owlOrchestratorV2 = new OwlOrchestratorV2({
        owl: ctx.owl,
        provider: ctx.provider,
        config: ctx.config,
        db: ctx.db,
        toolRegistry: ctx.toolRegistry,
      });
      this.improvementScheduler = new ImprovementScheduler(
        new OutcomeJournalV2(ctx.db),
        ctx.db,
        { quietHours: (ctx.config as any).heartbeat?.quietHours ?? [] },
      );
      this.improvementScheduler.start();
      ctx.orchestrator = this.owlOrchestratorV2;
      ctx.improvementScheduler = this.improvementScheduler;
      log.engine.info("[OwlEngine v2] OwlOrchestrator + ImprovementScheduler initialized");
    }

    // ConversationDigestManager removed — digestManager no longer in GatewayContext

    // Auto-initialize FeedbackStore using the DB (Phase 3 — no more feedback.json)
    if (!ctx.feedbackStore && ctx.db) {
      const workspacePath = ctx.cwd ?? process.cwd();
      ctx.feedbackStore = new FeedbackStore(
        workspacePath,
        ctx.db,
        ctx.owl.persona.name,
      );
      log.engine.info("[memory] FeedbackStore initialized (SQLite)");
    }

    // Auto-initialize SpecializedOwlRegistry for folder-based specialized owls
    const workspacePath = ctx.cwd ?? process.cwd();
    if (!ctx.specializedRegistry) {
      ctx.specializedRegistry = new SpecializedOwlRegistry();
    }
    // Initialize InstinctEngine lazily — needs provider which is available now
    this.instinctEngine = new InstinctEngine(
      ctx.provider,
      ctx.config.defaultModel,
      this.instinctRegistry,
    );
    ctx.specializedRegistry.loadAll(workspacePath).then(async () => {
      log.engine.info(
        `[registry] SpecializedOwlRegistry loaded ${ctx.specializedRegistry!.listAll().length} specialized owls`,
      );
      // Load BMAD agents dynamically from the installed bmad-method npm package
      try {
        const bmadLoader = new BmadAgentLoader();
        const bmadSpecs = await bmadLoader.loadAll();
        for (const spec of bmadSpecs) {
          ctx.specializedRegistry!.registerSpec(spec);
        }
        log.engine.info(`[registry] BmadAgentLoader registered ${bmadSpecs.length} BMAD agents`);
      } catch (err) {
        log.engine.warn("[registry] BmadAgentLoader failed (non-fatal)", { err: String(err) });
      }
      // Pre-load instincts for all known owls
      const owlsDir = join(workspacePath, "owls");
      await Promise.all(
        ctx.specializedRegistry!.listAll().map((spec) =>
          this.instinctRegistry.loadForOwl(owlsDir, spec.name),
        ),
      );
    }).catch((err) => { log.engine.warn("instinct registry load failed", err); });

    // learningOrchestrator removed — cognitive loop gap bridge retired

    // ─── Epic 5: Memory Modules ─────────────────────────────────────
    // PriorContextRetriever, CrossSessionStore, PreferenceRecognizer retired (modules deleted)

    // ─── Epic 6: Parliament Modules ─────────────────────────────────
    if (ctx.parliamentAutoTrigger) {
      this.parliamentAutoTrigger = ctx.parliamentAutoTrigger;
    } else {
      this.parliamentAutoTrigger = new ParliamentAutoTrigger(ctx.config);
    }
    if (this.parliamentAutoTrigger) {
      log.engine.info("[parliament] ParliamentAutoTrigger initialized");
    }

    if (ctx.topicWorthiness) {
      this.topicWorthiness = ctx.topicWorthiness;
    } else {
      this.topicWorthiness = new TopicWorthinessEvaluator(ctx.provider);
    }
    if (this.topicWorthiness) {
      log.engine.info("[parliament] TopicWorthiness initialized");
    }

    if (ctx.multiRoundDebate) {
      this.multiRoundDebate = ctx.multiRoundDebate;
    } else {
      this.multiRoundDebate = new MultiRoundDebateManager(ctx.provider, ctx.config);
    }
    if (this.multiRoundDebate) {
      log.engine.info("[parliament] MultiRoundDebate initialized");
    }

    if (ctx.debatePelletGenerator) {
      this.debatePelletGenerator = ctx.debatePelletGenerator;
    } else {
      this.debatePelletGenerator = new DebatePelletGenerator(ctx.provider);
    }
    if (this.debatePelletGenerator) {
      // factStore removed — fact pipeline not available
      log.engine.info("[parliament] DebatePelletGenerator initialized");
    }

    if (ctx.routingWirer) {
      this.routingWirer = ctx.routingWirer;
    } else {
      this.routingWirer = new RoutingWirer();
    }
    if (this.routingWirer) {
      log.engine.info("[parliament] RoutingWirer initialized");
    }

    // Wire GoalVerifier for Parliament post-session verification
    // NOTE: field is ctx.intelligence (not ctx.intelligenceRouter) — verified in src/gateway/types.ts
    const providerMap = new Map<string, import("../providers/base.js").ModelProvider>();
    if (ctx.provider) providerMap.set(ctx.config.defaultProvider ?? "default", ctx.provider);

    if (ctx.intelligence) {
      this.goalVerifier = GoalVerifier.create(ctx.intelligence, providerMap);
    }

    // ─── Epic 1: Learning Modules ─────────────────────────────────
    // DomainExpertiseTracker retired (learning/ modules removed)

    // ─── Epic 2: Verification Modules ──────────────────────────────
    this.outcomeVerifier = new OutcomeVerifier();
    this.falseDoneDetector = new FalseDoneDetector(this.ctx.provider);
    this.completionTracker = new CompletionTracker();
    this.escalationHandler = ctx.intelligence
      ? EscalationHandler.create(ctx.intelligence, providerMap)
      : new EscalationHandler();

    // Initialize new feature modules (all optional, fire-and-forget load)
    this.initFeatureModules();

    // ─── Capability snapshot at end of boot ──────────────────────
    const snap = snapshotLog();
    log.engine.info("capability.snapshot", snap);
    if (snap.degradedCount > 0) {
      log.engine.warn(
        `[CapabilityRegistry] ${snap.degradedCount} degraded subsystem(s) at boot`,
        { capabilities: getDegradedCapabilities() },
      );
    }

    this.validateContext();

    // Wire delivery bus → router (Phase 1 channel infrastructure)
    this.deliveryRouter.start(this.gatewayEventBus);
  }

  private validateContext(): void {
    if (!this.ctx.specializedRegistry)
      log.engine.warn("[Gateway] specializedRegistry is null — @mention and specialist routing disabled");
    if (!this.multiRoundDebate)
      log.engine.warn("[Gateway] multiRoundDebate is null — Parliament feature disabled");
    // pelletStore removed — no longer checked
    if (!this.ctx.owlRegistry)
      log.engine.warn("[Gateway] owlRegistry is null — Multi-owl features disabled");
  }

  // ─── Adapter Registry ────────────────────────────────────────

  register(adapter: ChannelAdapter): void {
    this.adapters.set(adapter.id, adapter);
    log.engine.info(`Channel registered: ${adapter.name} [${adapter.id}]`);
    // Also register with ChannelRegistry via V1Shim for Phase 1 event bus routing
    const shim = new ChannelAdapterV1Shim(adapter, defaultCapsForV1(adapter.id));
    this.channelRegistry.register(shim);
  }

  /** Return all registered channel adapters (used by the server for cross-adapter delegation). */
  getAdapters(): ChannelAdapter[] {
    return Array.from(this.adapters.values());
  }

  /** Add a middleware to the pipeline. */
  use(mw: GatewayMiddleware): void {
    this.middleware.push(mw);
    log.engine.info(`Middleware registered: ${mw.name}`);
  }

  /** Lazy-init the task orchestrator (reuses existing registries). */
  private getOrchestrator(): TaskOrchestrator {
    if (!this.taskOrchestrator) {
      this.taskOrchestrator = new TaskOrchestrator(
        this.ctx.owlRegistry,
        this.ctx.provider,
        this.ctx.config,
        this.ctx.toolRegistry,
        this.ctx.planLedger,
      );
    }
    return this.taskOrchestrator;
  }

  // ─── Main Entry Point ────────────────────────────────────────

  /**
   * Process an incoming message from any channel.
   *
   * Execution is serialized per session via the Lane Queue — if a message
   * arrives while the previous one is still processing, it waits in line.
   * This makes session history mutations safe and fully deterministic.
   */
  handle(
    message: GatewayMessage,
    callbacks: GatewayCallbacks = {},
  ): Promise<GatewayResponse> {
    if (!message.sessionId || message.sessionId.length > 256) {
      throw new Error("Invalid session ID");
    }
    if (!message.channelId || message.channelId.length > 64) {
      throw new Error("Invalid channel ID");
    }
    // Apply output mode filter — normal mode suppresses onProgress and non-text stream events
    const outputMode = resolveOutputMode(this.ctx.config.gateway);
    const filteredCallbacks = new OutputFilter(outputMode).apply(callbacks);

    const laneKey = `${message.channelId}:${message.sessionId}`;
    const prev = this.lanes.get(laneKey) ?? Promise.resolve();
    const next = prev.then(async () => {
      const response = await this.handleInLane(message, filteredCallbacks);
      return this.applyStuckTaskCheck(laneKey, response);
    });
    // Store only the tail; GC cleans up resolved promises automatically
    this.lanes.set(
      laneKey,
      next.catch((err) => { log.engine.warn("lane execution failed", err); }),
    );
    return next;
  }

  /**
   * Track consecutive exhausted responses per session.
   * After STUCK_THRESHOLD exhaustions in a row, replace the response with
   * a structured escalation that gives the user 3 concrete options:
   *   (a) Provide more information / clarify
   *   (b) Try a completely different approach
   *   (c) Accept the task cannot be completed right now
   *
   * On any non-exhausted response the streak resets to zero.
   */
  private applyStuckTaskCheck(
    sessionKey: string,
    response: GatewayResponse,
  ): GatewayResponse {
    const isExhausted = response.content.includes(EXHAUSTION_MARKER);

    if (isExhausted) {
      const streak = (this.stuckStreak.get(sessionKey) ?? 0) + 1;
      this.stuckStreak.set(sessionKey, streak);

      // Strip the internal marker from what the user sees
      const cleanContent = response.content
        .replace(EXHAUSTION_MARKER, "")
        .trimEnd();

      if (streak >= OwlGateway.STUCK_THRESHOLD) {
        // Escalate — give the user agency
        log.engine.warn(
          `[stuck-task] Session "${sessionKey}" has been stuck for ${streak} consecutive responses. Escalating.`,
        );
        const escalation =
          `⚠️ **I've tried ${streak} different strategies on this task and still haven't completed it.**\n\n` +
          `Here's where I got to:\n${cleanContent}\n\n` +
          `To move forward, I need your help choosing a direction:\n\n` +
          `**A) Add more context or clarify the goal** — even a small detail often unlocks a new approach.\n\n` +
          `**B) Describe a specific approach you'd like me to try** — if you have a method in mind, tell me and I'll execute it.\n\n` +
          `**C) Acknowledge this is a current limitation** — I'll note it and you can revisit it later.\n\n` +
          `_Reply with A, B, or C (or just describe what you want)._`;

        return {
          ...response,
          content: escalation,
        };
      }

      // Streak below threshold — return cleaned response but don't escalate yet
      return { ...response, content: cleanContent };
    }

    // Non-exhausted response — reset streak
    this.stuckStreak.set(sessionKey, 0);
    return response;
  }

  private async handleInLane(
    message: GatewayMessage,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse> {
    if (this.ctx.eventBus && message.sessionId) {
      this.ctx.eventBus.emit("agent:state_change", {
        sessionId: message.sessionId,
        state: "EXECUTING",
      });
    }

    try {
      // Run middleware before hooks
      const mwCtx: MiddlewareContext = {
        sessionId: message.sessionId,
        channelId: message.channelId,
        userId: message.userId,
      };
      for (const mw of this.middleware) {
        if (mw.before) {
          const shortCircuit = await mw.before(message, mwCtx);
          if (shortCircuit) return shortCircuit;
        }
      }

      const response = await this.handleCore(message, callbacks);

      // Run middleware after hooks
      let finalResponse = response;
      for (const mw of this.middleware) {
        if (mw.after) {
          finalResponse = await mw.after(message, finalResponse, mwCtx);
        }
      }
      return finalResponse;
      
    } finally {
      if (this.ctx.eventBus && message.sessionId) {
        this.ctx.eventBus.emit("agent:state_change", {
          sessionId: message.sessionId,
          state: "IDLE",
        });
      }
    }
  }

  private async handleCore(
    message: GatewayMessage,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse> {
    // Drain CRITICAL (high-priority) PostProcessor jobs from the previous turn
    // before building context so digest-update is guaranteed to have completed.
    await this.taskQueue.drainCritical();

    // Greeting-reset check: if user sends a lone greeting and session has history,
    // end and evict the old session so they start fresh.
    if (this.ctx.sessionService && SessionService.isGreetingPattern(message.text)) {
      const greetingSession = await this.sessionManager.getOrCreate(message);
      if (greetingSession.messages.length >= 4) {
        await this.endSession(message.sessionId);
        this.sessionManager.invalidate(message.sessionId);
        this.ctx.sessionService.evictFromCache(message.sessionId);
      }
    }

    const session = await this.sessionManager.getOrCreate(message);

    // absorbGapFeedback removed — pelletStore retired

    // Check if this message is a YES/NO reply to a pending agent-watch question
    // Must be checked before the feature command handler and before the engine
    if (this.agentWatch) {
      const consumed = this.agentWatch.handleTelegramReply(
        message.userId,
        message.text,
      );
      if (consumed) {
        return {
          content: "✅ Decision sent to agent.",
          owlName: this.ctx.owl.persona.name,
          owlEmoji: this.ctx.owl.persona.emoji,
          toolsUsed: [],
        };
      }
    }

    // ─── Wizard routing ──────────────────────────────────────────
    const activeWizard = this.wizardSessions.get(message.sessionId);
    if (activeWizard) {
      const wizResp = await activeWizard.step(message.text);
      if (wizResp.done) this.wizardSessions.delete(message.sessionId);
      return {
        content: wizResp.text,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
        inlineKeyboard: wizResp.inlineKeyboard,
      };
    }

    // ─── /skills — top-level menu or direct install ───────────────
    const skillsCmd = message.text.trim().toLowerCase();
    if (skillsCmd === "/skills" || skillsCmd.startsWith("/skills install") || skillsCmd.startsWith("/skills list")) {
      const { resolve, join } = await import("node:path");
      const skillsDir = resolve(
        this.ctx.config.skills?.directories?.[0] ??
          join(this.ctx.cwd ?? process.cwd(), "skills"),
      );
      const registry = this.ctx.skillsLoader?.getRegistry();
      let wizard: WizardSession;
      if (skillsCmd.startsWith("/skills install")) {
        wizard = new SkillInstallWizard(skillsDir, new ClawHubClient(), registry);
      } else {
        wizard = new SkillsMenuWizard(skillsDir, new ClawHubClient(), registry);
      }
      // /skills list — resolve immediately without storing a session
      if (skillsCmd.startsWith("/skills list")) {
        const resp = await (wizard as SkillsMenuWizard).step("list");
        return {
          content: resp.text,
          owlName: this.ctx.owl.persona.name,
          owlEmoji: this.ctx.owl.persona.emoji,
          toolsUsed: [],
        };
      }
      this.wizardSessions.set(message.sessionId, wizard);
      const startResp =
        wizard instanceof SkillInstallWizard
          ? wizard.start()
          : (wizard as SkillsMenuWizard).start();
      return {
        content: startResp.text,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
        inlineKeyboard: startResp.inlineKeyboard,
      };
    }

    // Check for /reset command - clear session history
    if (message.text.trim().toLowerCase() === "/reset") {
      this.wizardSessions.delete(message.sessionId);
      session.messages = [];
      this.attemptLogs.delete(message.sessionId);
      if (this.ctx.sessionService && this.ctx.db) {
        this.ctx.db.messages.deleteSession(message.sessionId);
        this.ctx.sessionService.evictFromCache(message.sessionId);
      }
      log.engine.info(`Session reset for ${message.sessionId}`);
      return {
        content:
          "🧹 Context cleared! Starting fresh. What would you like to work on?",
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
    }

    // ─── Status query interception ──────────────────────────────
    if (this.ctx.routingStatusReporter && RoutingStatusReporter.isStatusQuery(message.text)) {
      const report = this.ctx.routingStatusReporter.getStatusReport(message.userId);
      const content = this.ctx.routingStatusReporter.formatForChannel(report, message.channelId);
      return {
        content,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
    }

    // ─── /skill management commands (channel-parity router) ──────
    // Intercept management verbs before falling through to skill execution.
    const skillCmdMatch = message.text
      .trim()
      .match(/^\/skill\s+(\S+)(?:\s+(.+))?$/i);
    const SKILL_MGMT_VERBS = new Set(["list", "show", "install", "create", "enable", "disable", "remove", "run", "metrics"])
    if (skillCmdMatch && SKILL_MGMT_VERBS.has(skillCmdMatch[1].toLowerCase())) {
      const [, verb, rest] = skillCmdMatch
      const args = rest ? rest.trim().split(/\s+/) : []
      const registry = this.ctx.skillsLoader?.getRegistry()
      const content = await dispatchSkillCommand(verb, args, {
        registry: registry as any,
        wizard: this.skillCreationWizard!,
        installer: (this.ctx as any).skillInstaller,
        userId: message.userId,
        channelAdapter: undefined,
        workspacePath: this.ctx.cwd ?? process.cwd(),
        db: this.ctx.db,
      })
      return {
        content,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      }
    }

    // Check for explicit skill invocation: /skill <name> [args...]
    const skillMatch = message.text
      .trim()
      .match(/^\/skill\s+(\S+)(?:\s+(.+))?$/i);
    if (skillMatch && this.ctx.skillsLoader) {
      const skillName = skillMatch[1];
      const skillArgs = skillMatch[2] ?? "";
      const registry = this.ctx.skillsLoader.getRegistry();
      const skill = registry.get(skillName);
      if (skill) {
        log.engine.info(`Explicit skill invocation: ${skill.name}`);

        // Structured skills — execute directly via executor
        if (this.skillInjector?.canExecuteStructured(skill)) {
          const emoji = skill.metadata.stackowl?.emoji || "⚡";
          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `${emoji} **Executing skill:** \`${skill.name}\``,
            );
          }
          const result = await this.skillInjector.executeStructuredSkill(
            skill,
            skillArgs || skill.description,
            callbacks.onProgress,
          );
          await this.saveSession(
            session,
            message.text,
            [],
            false,
            result.finalOutput,
          );
          this.postProcess(session.messages, session.id);
          return {
            content: result.finalOutput,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: result.stepResults
              .filter((s) => s.status === "success")
              .map((s) => s.stepId),
          };
        }

        // Unstructured skills — inject as prompt directive (existing path)
        const skillDirective =
          `[SKILL INVOKED: ${skill.name}]\n` +
          `The user has explicitly requested this skill. Follow its instructions exactly.\n\n` +
          `<skill name="${skill.name}">\n${skill.instructions}\n</skill>\n\n` +
          (skillArgs ? `User arguments: ${skillArgs}` : "");
        const engineCtx = await this.buildEngineContext(
          session,
          callbacks,
          skillDirective,
          false,
          this.attemptLogs.get(message.sessionId),
        );
        const response = await this.engine.run(
          skillArgs || skill.description,
          engineCtx,
        );
        await this.saveSession(
          session,
          message.text,
          response.newMessages,
          false,
          response.content,
        );
        this.postProcess(session.messages, session.id);
        // Deliver any files queued during skill run
        await this.deliverPendingFiles(
          response.pendingFiles ?? [],
          message.channelId,
          message.userId,
        );
        return toGatewayResponse(response);
      } else {
        // Unknown skill — list available ones
        const allSkills = registry.listEnabled();
        const list =
          allSkills.length > 0
            ? allSkills.map((s) => `• ${s.name}: ${s.description}`).join("\n")
            : "(no skills loaded)";
        return {
          content: `❓ Skill "${skillName}" not found. Available skills:\n${list}`,
          owlName: this.ctx.owl.persona.name,
          owlEmoji: this.ctx.owl.persona.emoji,
          toolsUsed: [],
        };
      }
    }

    // ─── Natural-language watch commands ──────────────────────────
    // These are not slash commands and will never be caught by the feature router's
    // slash-only extractCommand(). Handle them here before router dispatch.
    const natLangWatchResult = await this.handleNaturalLanguageWatchCommands(message);
    if (natLangWatchResult) return natLangWatchResult;

    // ─── New Feature Commands ──────────────────────────────────
    const featureCmdCtx: import("./feature-command-router.js").FeatureCommandContext = {
      message,
      session,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      gatewayCtx: this.ctx,
      sessionManager: this.sessionManager,
      agentWatch: this.agentWatch,
      skillInjector: this.skillInjector,
      callbacks,
    };
    const featureResult = await this.featureRouter.dispatch(message.text, featureCmdCtx);
    if (featureResult) return featureResult;

    // ─── Explicit learning request ──────────────────────────────
    // Matches: /learn <topic>, "learn how to <topic>", "can you learn <topic>",
    // "study <topic>", "research <topic> for me"
    const learnResult = await this.handleLearnRequest(message, callbacks);
    if (learnResult) return learnResult;

    // ─── Continuity Engine (Phase 0+1+2) ───────────────────────
    // 3-layer classification: temporal → linguistic → semantic (LLM only when ambiguous)
    let continuityResult: ContinuityResult | null = null;
    let freshStartDirective: string | null = null;

    try {
      const timezone =
        (this.ctx.config as any).timezone ??
        Intl.DateTimeFormat().resolvedOptions().timeZone;
      const previousSession = await loadPreviousSession(
        null as any, // sessionStore removed
        message.sessionId,
      );
      const temporalSnapshot = computeTemporalContext(
        session,
        previousSession,
        timezone,
      );

      // Use fastest available provider for Layer 3 (semantic disambiguation)
      let fastProvider:
        | import("../providers/base.js").ModelProvider
        | undefined;
      if (this.ctx.providerRegistry) {
        try {
          fastProvider = this.ctx.providerRegistry.byRole("semantic-disambiguator");
        } catch (err) {
          log.engine.warn("continuity: no semantic-disambiguator provider, using default", err);
          fastProvider = this.ctx.provider;
        }
      }

      continuityResult = await classifyContinuity(
        message.text,
        session,
        temporalSnapshot,
        fastProvider,
      );

      log.engine.info(
        `[Continuity] ${continuityResult.classification} (conf=${continuityResult.confidence.toFixed(2)}, layer=${continuityResult.layerUsed}): ${continuityResult.reason}`,
      );

      // Update user mental model with this message's behavioral signals
      this.userMentalModel?.update(message.text);

      // Act on classification
      const wc = (this.ctx as any).workingContextManager?.getOrCreate(message.sessionId);
      const intentSM = this.ctx.intentStateMachine;
      const activeIntent = intentSM?.getActiveForSession(message.sessionId);

      switch (continuityResult.classification) {
        case "CONTINUATION":
          // Touch active intent to keep it alive
          if (activeIntent) {
            intentSM!.touch(activeIntent.id);
          } else if (intentSM) {
            // No active intent — check for narrative thread match
            const matchedThread = intentSM.getThreadForTopic(message.text);
            if (matchedThread) {
              intentSM.resumeThread(matchedThread.id, message.sessionId);
              if (wc) {
                wc.set(
                  "threadResumed",
                  `Resuming thread: ${matchedThread.summary ?? matchedThread.description}`,
                );
              }
              // Fire-and-forget: refresh thread summary from recent messages
              this.refreshThreadSummary(matchedThread.id, session.messages);
            }
          }
          break;

        case "FOLLOW_UP":
          // Touch active intent + add context hint
          if (activeIntent) {
            intentSM!.touch(activeIntent.id);
          } else if (intentSM) {
            // Check for thread match on follow-up too
            const matchedThread = intentSM.getThreadForTopic(message.text);
            if (matchedThread) {
              intentSM.resumeThread(matchedThread.id, message.sessionId);
              this.refreshThreadSummary(matchedThread.id, session.messages);
            }
          }
          if (wc) {
            const topic = wc.getCurrentTopic();
            if (topic) {
              wc.set("continuityHint", `Building on: ${topic}`);
            }
          }
          break;

        case "TOPIC_SWITCH":
          // Record topic switch for user mental model
          this.userMentalModel?.recordTopicSwitch();
          // Pause active intent, clear working context topic
          if (activeIntent) {
            intentSM!.transition(
              activeIntent.id,
              "abandoned",
              "Topic switch detected",
            );
          }
          if (wc) {
            const prevTopic = wc.getCurrentTopic();
            wc.clear();
            if (prevTopic) {
              wc.set(
                "continuityHint",
                `Previous topic was: ${prevTopic}. User has switched to a new topic.`,
              );
            }
          }
          // Archive ground state — expire open questions, keep decisions
          if (this.ctx.groundState) {
            const uid = message.sessionId.split(":")[1] || message.sessionId;
            this.ctx.groundState.archive(uid).catch((err) => { log.engine.warn("groundState archive failed", err); });
          }
          break;

        case "FRESH_START":
          // Soft context boundary — do NOT wipe session.messages.
          // The model needs history to answer back-references ("what was your last response?").
          // We inject a directive so it knows this is a fresh task, but history stays readable.
          if (activeIntent) {
            intentSM!.transition(
              activeIntent.id,
              "abandoned",
              "Fresh start — user returning or resetting",
            );
          }
          this.attemptLogs.delete(message.sessionId);
          if (wc) wc.clear();

          // ── Plan Resume Check ──────────────────────────────────
          // If PlanLedger has interrupted plans for this user, check whether
          // the current message is asking about them. If so, offer to resume.
          if (this.ctx.planLedger) {
            const runningPlans = this.ctx.planLedger.getRunningPlans(message.userId);
            if (runningPlans.length > 0) {
              const plan = runningPlans[0]; // most recent interrupted plan
              const summary = this.ctx.planLedger.buildResumeSummary(plan);
              // Check if the user is asking about the previous task
              const isAskingAboutPrior =
                /\b(finish|done|complete|continue|resume|status|progress|still|that task|previous|earlier|what happened)\b/i.test(
                  message.text,
                );
              if (isAskingAboutPrior) {
                // Resume the plan directly
                log.engine.info(
                  `[Gateway] Resuming interrupted plan ${plan.planId} for user ${message.userId}`,
                );
                const engineCtx = await this.buildEngineContext(
                  session, callbacks, "", true,
                  this.attemptLogs.get(message.sessionId),
                  message.channelId, message.userId, null,
                );
                const orchestrator = this.getOrchestrator();
                const orchResult = await orchestrator.resumePlanned(plan, engineCtx, callbacks);
                await this.saveSession(session, message.text, [], false, orchResult.content);
                return {
                  content: orchResult.content,
                  owlName: orchResult.owlName,
                  owlEmoji: orchResult.owlEmoji,
                  toolsUsed: orchResult.toolsUsed,
                };
              } else {
                // Mention it passively in the fresh start directive
                freshStartDirective =
                  `[SYSTEM DIRECTIVE: You are starting a new task. Prior conversation history is available for reference.]\n` +
                  `[INTERRUPTED TASK: The user had an in-progress task from a previous session. If relevant, mention it:\n${summary}]`;
              }
            }
          }

          if (!freshStartDirective) {
            // Build fresh start directive (no interrupted plan)
            const prevTopic =
              session.messages
                .filter((m) => m.role === "user")
                .slice(-1)[0]
                ?.content?.slice(0, 100) ??
              previousSession?.messages
                .filter((m) => m.role === "user")
                .slice(-1)[0]
                ?.content?.slice(0, 100);
            freshStartDirective = `[SYSTEM DIRECTIVE: You are starting a new task. Prior conversation history is available for reference but treat this as a fresh request.]`;
            if (prevTopic) {
              freshStartDirective += `\n[Previous context: User was last discussing "${prevTopic}"]`;
            }
          }
          break;
      }
    } catch (err) {
      log.engine.warn(
        `[Continuity] Engine failed, falling back to keyword detection: ${err instanceof Error ? err.message : err}`,
      );
      // Fallback to legacy keyword detection
      freshStartDirective = this.detectTopicSwitch(
        message.text,
        session.messages,
      );
      if (freshStartDirective) {
        // Do NOT wipe session.messages — preserve history for back-references.
        // Only clear working context and attempt logs.
        this.attemptLogs.delete(message.sessionId);
        if ((this.ctx as any).workingContextManager) {
          (this.ctx as any).workingContextManager.getOrCreate(message.sessionId).clear();
        }
      }
    }

    // ─── Episodic Memory: Segment Extraction (Phase 3) ──────────
    // Episodic memory extraction — removed (episodicMemory deleted in memory refactor).
    // Sessions are persisted via SessionService (SQLite); episodic extraction is no-op.

    // Abandon stale intents (no activity in 30+ minutes)
    if (this.ctx.intentStateMachine) {
      for (const stale of this.ctx.intentStateMachine.getStale()) {
        this.ctx.intentStateMachine.transition(
          stale.id,
          "abandoned",
          "Stale — no activity for 30+ minutes",
        );
      }
    }

    // Populate working context with current message
    if ((this.ctx as any).workingContextManager) {
      const wc = (this.ctx as any).workingContextManager.getOrCreate(message.sessionId);
      wc.setLastUserMessage(message.text);
    }

    let text = message.text;

    // Track per-session activity for proactive message routing
    this.proactiveSvc.recordActivity(message.sessionId, message.channelId, message.userId);
    this.channelRegistry.markActive(message.channelId, message.userId);

    log.engine.incoming(message.channelId, message.text);

    // ─── Tier Escalation — detect correction signal + auto-reset ──────────
    const escalationKey = message.sessionId ?? "default";
    if (!this.escalationManagers.has(escalationKey)) {
      this.escalationManagers.set(escalationKey, new TierEscalationManager());
    }
    const escalationManager = this.escalationManagers.get(escalationKey)!;
    escalationManager.checkAutoReset();
    if (escalationManager.detectCorrectionSignal(message.text)) {
      const newFloor = escalationManager.escalate();
      log.engine.info(
        `[TierEscalation] User correction detected — escalating tier floor to ${newFloor}`,
      );
    }

    // Dynamic skill injection — uses BM25 + usage-weighted semantic routing
    let memoryContextPrefix = "";
    let dynamicSkillsContext = "";
    let injectedSkillNames: string[] = [];

    // Epic 5 memory modules (PriorContextRetriever, PreferenceRecognizer, CrossSessionStore)
    // removed in memory refactor — pre-engine injection is handled by ContextPipeline.

    // Skip skill routing on short or conversational messages.
    // The IntentRouter's 5-tier pipeline (BM25 + semantic re-rank + LLM call)
    // adds 1–3 seconds of latency and is wasted on conversational messages.
    // IntentRouter's Tier-5 LLM naturally filters further based on intent.
    const isConversational =
      text.trim().length < 15 ||
      /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i.test(
        text.trim(),
      );

    // CI-3: NL skill install intent detection
    if (!isConversational && !text.trim().startsWith('/')) {
      const installIntent = await isSkillInstallIntent(text, this.ctx)
      if (installIntent) {
        const registry = this.ctx.skillsLoader?.getRegistry()
        const content = await dispatchSkillCommand('create', [], {
          registry: registry as any,
          wizard: this.skillCreationWizard!,
          userId: message.userId,
          channelAdapter: undefined,
          workspacePath: this.ctx.cwd ?? process.cwd(),
          db: this.ctx.db,
        })
        return {
          content,
          owlName: this.ctx.owl.persona.name,
          owlEmoji: this.ctx.owl.persona.emoji,
          toolsUsed: [],
        }
      }
    }

    if (this.skillInjector && !isConversational) {
      const relevantMatches = await this.skillInjector.getRelevantMatches(text);
      const relevantSkills = relevantMatches.map((m) => m.skill);
      if (relevantMatches.length > 0) {
        // Auto-execution is DISABLED: BM25 scores are unnormalized (can be 5-15+),
        // so keyword-based confidence thresholds cannot reliably distinguish
        // "find a laptop" from "find duplicate files". Instead, skills are always
        // injected as context hints and the LLM decides whether to use them.
        // Explicit invocation via /skill_name still triggers direct execution (above).
        const topMatch = relevantMatches[0];
        const topSkill = topMatch.skill;
        if (topMatch.method === "llm" && this.skillInjector!.canExecuteStructured(topSkill)) {
          log.engine.info(`Structured skill execution: ${topSkill.name}`);
          const emoji = topSkill.metadata.stackowl?.emoji || "⚡";
          if (callbacks.onProgress) {
            await callbacks.onProgress!(
              `${emoji} **Executing skill:** \`${topSkill.name}\` — ${topSkill.description}`,
            );
          }

          const result = await this.skillInjector!.executeStructuredSkill(
            topSkill,
            message.text,
            callbacks.onProgress,
          );

          // Save to session and return directly — no ReAct loop needed
          await this.saveSession(
            session,
            message.text,
            [],
            false,
            result.finalOutput,
          );
          this.postProcess(session.messages, session.id);

          return {
            content: result.finalOutput,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: result.stepResults
              .filter((s) => s.status === "success")
              .map((s) => s.stepId),
          };
        }

        // Unstructured skills — inject as context XML (existing path)
        dynamicSkillsContext = await this.skillInjector.injectIntoContext(text);
        const skillNames = relevantSkills.map((s) => s.name);
        injectedSkillNames = skillNames;
        log.engine.info(`Dynamic skill injection: ${skillNames.join(", ")}`);

        // Notify user about skill usage (like tool history)
        if (callbacks.onProgress) {
          for (const s of relevantSkills) {
            const emoji = s.metadata.stackowl?.emoji || "📋";
            await callbacks.onProgress(
              `${emoji} **Using skill:** \`${s.name}\` — ${s.description}`,
            );
          }
        }
      } else {
        log.engine.info(
          `[Skills] No skills matched for: "${text.slice(0, 60)}"`,
        );
      }

      // Optionally search ClawHub if no local skills match
      if (relevantSkills.length === 0) {
        const result = await this.skillInjector.ensureRelevantSkills(text);
        if (result.newSkillsInstalled.length > 0) {
          log.engine.info(
            `ClawHub suggested: ${result.newSkillsInstalled.join(", ")}`,
          );
        }
      }
    }

    // Add fresh start directive if topic switch detected
    const isIsolatedTask = !!freshStartDirective;
    if (freshStartDirective) {
      text = `${freshStartDirective}\n\nUser request: ${text}`;
    }

    // ─── Phase 3: Session Opening Brief ──────────────────────────
    // On FRESH_START, generate and prepend a personalised context brief so
    // the user arrives oriented rather than re-explaining themselves.
    if (
      continuityResult?.classification === "FRESH_START" &&
      this.sessionBriefGenerator
    ) {
      this.runBackground("session-brief", (async () => {
        try {
          const brief = await this.sessionBriefGenerator!.generate({
            owlName: this.ctx.owl.persona.name,
            episodicMemory: undefined,
            groundState: this.ctx.groundState,
            innerLife: this.ctx.innerLife,
            userId: message.userId,
          });
          if (brief && callbacks.onProgress) {
            await callbacks.onProgress(`\n${brief.formatted}\n`);
          }
        } catch (err) {
          log.engine.warn("session brief generation failed", err);
        }
      })());
    }

    // ─── Intent Clarification (Element 9) — before engine execution ─────────
    const sessionKey = message.sessionId ?? 'default';

    let clarificationInput = message.text;
    let clarificationHistory = [...(session.messages ?? [])];

    // Fix 3: initialize clarificationBias BEFORE the pendingExecution block so
    // recordDismissal() can be called inside that block.
    const clarificationBias: SessionAutonomyBias = (session as any).clarificationBias ?? new SessionAutonomyBias();
    (session as any).clarificationBias = clarificationBias;

    // Fix 2: track whether there was a pending execution before nulling it.
    const hadPendingExecution = !!(session as any).pendingExecution;

    if ((session as any).pendingExecution) {
      const pending = (session as any).pendingExecution as { originalMessage: string };
      clarificationInput = pending.originalMessage;
      clarificationHistory = [...clarificationHistory, { role: 'user' as const, content: message.text }];
      (session as any).pendingExecution = null;
      // Fix 3: user answered our clarification question — record the dismissal
      // so the autonomy-bias feedback loop has accurate data.
      clarificationBias.recordDismissal();
    }

    const intentResult = await this.intentClarifier.evaluate(
      clarificationInput,
      clarificationHistory.slice(-3),
      this.ctx.owl.dna,
      clarificationBias,
      sessionKey,
    );

    if (intentResult.verdict === 'USER_CONFUSED') {
      // Fix 2: if a pendingExecution was active when confusion was detected,
      // restore it so the next user message can resume the original intent.
      if (hadPendingExecution) {
        (session as any).pendingExecution = { originalMessage: clarificationInput };
      }
      return {
        content: `Let me help you think through this. ${intentResult.reasoning}`,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
    }

    if (intentResult.verdict === 'CLARIFY') {
      if ((session as any)._clarifyRetry) {
        delete (session as any)._clarifyRetry;
      } else {
        (session as any).pendingExecution = { originalMessage: clarificationInput };
        (session as any)._clarifyRetry = true;

        const trajectoryId = (session as any)._currentTrajectoryId;
        if (trajectoryId && (this.ctx as any).db) {
          (this.ctx as any).db.trajectories.markClarificationAsked(trajectoryId);
        }

        return {
          content: intentResult.question!,
          owlName: this.ctx.owl.persona.name,
          owlEmoji: this.ctx.owl.persona.emoji,
          toolsUsed: [],
        };
      }
    }

    // Fix 1: clean up stale _clarifyRetry flag on any non-CLARIFY verdict so
    // it doesn't silently swallow the next legitimate CLARIFY on a new message.
    if ((session as any)._clarifyRetry && intentResult.verdict !== 'CLARIFY') {
      delete (session as any)._clarifyRetry;
    }

    // ─── Phase 3: Loop Detection ──────────────────────────────────
    // Detect if the user is stuck in a recurring question pattern.
    // If so, inject a root-cause-finding directive into the message text.
    const loopResult = await this.loopDetector.detect(
      message.text,
      undefined,
      message.userId,
    );
    if (loopResult.isLoop && loopResult.systemPromptHint) {
      text = `${loopResult.systemPromptHint}\n\nUser request: ${text}`;
      log.engine.info(
        `[LoopDetector] Loop injected for topic: "${loopResult.cluster?.topic}"`,
      );
    }

    // ─── Strategy Classification & Orchestrated Execution ─────
    // Single LLM call replaces both parliament detection and planner routing
    // ─── Epic 6: RoutingWirer — inject Parliament trigger logic into classifier ──
    let strategy: import("../orchestrator/types.js").TaskStrategy;
    if (this.routingWirer) {
      strategy = await this.routingWirer.classifyWithParliament(
        message.text,
        () =>
          classifyStrategy(
            message.text,
            this.ctx.owlRegistry.listOwls(),
            this.ctx.toolRegistry?.getAllDefinitions().map((t) => t.name) ?? [],
            session.messages.slice(-6),
            this.ctx.provider,
          ),
        this.ctx.provider,
      );
    } else {
      strategy = await classifyStrategy(
        message.text,
        this.ctx.owlRegistry.listOwls(),
        this.ctx.toolRegistry?.getAllDefinitions().map((t) => t.name) ?? [],
        session.messages.slice(-6),
        this.ctx.provider,
      );
    }

    // ── Confidence Gate ───────────────────────────────────────────
    // Expensive strategies (PARLIAMENT, SWARM) should not run when the classifier
    // is uncertain (confidence < 0.5). Low confidence means the model is guessing —
    // running a 5-owl debate on a misunderstood request wastes tokens and time.
    //
    //   < 0.5  → downgrade PARLIAMENT → STANDARD; ask for clarification on SWARM
    //   < 0.65 → downgrade PARLIAMENT → SPECIALIST (single best-matched owl)
    if (strategy.confidence != null && strategy.confidence < 0.5) {
      if (strategy.strategy === "PARLIAMENT") {
        log.engine.info(
          `[ConfidenceGate] Downgrading PARLIAMENT → STANDARD (confidence: ${strategy.confidence.toFixed(2)})`,
        );
        strategy = { ...strategy, strategy: "STANDARD" };
      } else if (strategy.strategy === "SWARM") {
        // Return a clarification question — SWARM on an ambiguous request is expensive
        log.engine.info(
          `[ConfidenceGate] Blocking SWARM, requesting clarification (confidence: ${strategy.confidence.toFixed(2)})`,
        );
        return {
          content:
            `I want to make sure I understand before I send multiple agents on this. ` +
            `Are you asking me to: _"${strategy.reasoning}"_?\n\nSay yes to proceed, or clarify what you mean.`,
          owlName: this.ctx.owl.persona.name,
          owlEmoji: this.ctx.owl.persona.emoji,
          toolsUsed: [],
        };
      }
    } else if (strategy.confidence != null && strategy.confidence < 0.65 && strategy.strategy === "PARLIAMENT") {
      log.engine.info(
        `[ConfidenceGate] Downgrading PARLIAMENT → SPECIALIST (confidence: ${strategy.confidence.toFixed(2)})`,
      );
      strategy = { ...strategy, strategy: "SPECIALIST" };
    }

    if (
      callbacks.onProgress &&
      !["DIRECT", "STANDARD"].includes(strategy.strategy)
    ) {
      await callbacks.onProgress(
        `🎯 **Strategy: ${strategy.strategy}** — ${strategy.reasoning}`,
      );
    }

    // ─── Epic 6: Parliament Routing Check ────────────────────────
    // Check if this topic warrants Parliament before standard execution.
    // If autoTrigger fires, route to multi-round debate instead.
    if (this.ctx.parliamentAutoTrigger) {
      const shouldTrigger = await this.parliamentSubsystem.shouldAutoTrigger(message.text);
      if (shouldTrigger) {
        log.gateway.debug("handleCore: parliament auto-triggered", { sessionId: message.sessionId });
        const parliamentResp = await this.parliamentSubsystem.run(message, this.ctx, callbacks, session);
        if (parliamentResp) return parliamentResp;
      }
    }

    const engineCtx = await this.buildEngineContext(
      session,
      callbacks,
      memoryContextPrefix + dynamicSkillsContext,
      isIsolatedTask,
      this.attemptLogs.get(message.sessionId),
      message.channelId,
      message.userId,
      continuityResult ?? null,
    );

    // ─── Cognitive Dispatch — classify intent + narrow toolHints ─────────
    // Awaited so toolHints reach the engine before runtime.ts loads tools.
    // Cold-start (warmth < 2) returns null — engine falls back to full set.
    if (this.ctx.cognitivePipeline) {
      const dispatchResult = await this.ctx.cognitivePipeline.runDispatch(
        message.sessionId,
        message.text,
      ).catch((err) => {
        log.engine.warn("[CognitivePipeline] dispatch failed — continuing without hints", err);
        return null;
      });
      if (dispatchResult?.toolHints?.length) {
        engineCtx.toolHints = dispatchResult.toolHints;
        log.engine.debug("[CognitivePipeline] toolHints injected", {
          sessionId: message.sessionId,
          intent: dispatchResult.intent,
          toolCount: dispatchResult.toolHints.length,
          tools: dispatchResult.toolHints,
        });
      }
    }

    // ─── Element 9: Wire narrationPrefix from intent classification ──────
    if (intentResult.verdict === 'NARRATE' && intentResult.interpretation) {
      engineCtx.narrationPrefix = intentResult.interpretation;
    }

    // ─── Tier Escalation floor — injected into EngineContext ─────────────
    engineCtx.escalationFloor = escalationManager.currentFloor;

    // ─── G5: Opinion injection — surface relevant owl opinion (≥ 0.65 conf) ──
    if (this.ctx.innerLife) {
      const opinions = this.ctx.innerLife.getState()?.opinions ?? [];
      const match = this.opinionInjector.findRelevant(text, opinions);
      if (match) {
        engineCtx.additionalSystemPrompt =
          (engineCtx.additionalSystemPrompt ?? "") +
          this.opinionInjector.formatForSystemPrompt(match);
      }
      this.opinionInjector.formOpinionAsync(text, this.ctx.innerLife).catch((err) => { log.engine.warn("opinion formation failed", err); });
    }

    // ─── Routing — @mention + SecretaryRouter ────────────────────
    let activeOwlName = this.ctx.owl.persona.name;
    if (!this.secretaryRouter && this.ctx.specializedRegistry) {
      this.secretaryRouter = new SecretaryRouter(this.ctx.specializedRegistry);
      if (this.ctx.db) {
        const db = this.ctx.db;
        this.secretaryRouter.setQualityLookup((owlName: string, userId: string) => {
          return db.owlQualityMetrics.get(owlName, userId)?.ewmaReward ?? 0.7;
        });
      }
    }
    let routingResult: OwlBrainResult | null = null;
    if (this.owlBrain) {
      const brainResult = await this.owlBrain.resolve(text, message, engineCtx, callbacks, session);
      text = brainResult.text;
      activeOwlName = brainResult.activeOwlName;
      routingResult = { text: brainResult.text, activeOwlName: brainResult.activeOwlName, parliamentHandled: brainResult.parliamentHandled };
      attachToContext({ owl: activeOwlName });
    }

    if (routingResult?.parliamentHandled) {
      // ─── Secretary Router triggered Parliament ───────────────────
      // owlBrain signals intent; the subsystem runs the actual debate and returns.
      log.gateway.info(`[Gateway] SecretaryRouter convened Parliament for: "${text.slice(0, 50)}..."`, { sessionId: message.sessionId });
      const parliamentRespB = await this.parliamentSubsystem.run(message, this.ctx, callbacks, session);
      if (parliamentRespB) return parliamentRespB;
      log.engine.warn("[Gateway] Parliament triggered but run() returned null — falling back to direct", { sessionId: message.sessionId });
    }

    // ─── Instinct injection ──────────────────────────────────────
    if (this.instinctEngine && activeOwlName !== this.ctx.owl.persona.name) {
      await withSpan("instinct.evaluate", async () => {
        const instinctStart = Date.now();
        log.engine.debug("[Instincts] instinct.evaluate: entry", {
          owl: activeOwlName,
          textLen: text.length,
        });

        // Step 1: Check LRU cache first — 0ms if hit
        const cached = this.instinctEngineV2.getCached(text);
        if (cached !== null) {
          log.engine.debug("[Instincts] instinct.evaluate: cache hit — skipping heuristic and LLM", {
            owl: activeOwlName,
            matchCount: cached.length,
            durationMs: Date.now() - instinctStart,
          });
          if (cached.length > 0) {
            const block = InstinctEngine.buildConstraintBlock(cached);
            const base = engineCtx.specialistPrompt ?? "";
            engineCtx.specialistPrompt = base + block;
            engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
            log.engine.info(
              `[Instincts] Injected ${cached.length} constraint(s) for owl "${activeOwlName}" (cache)`,
            );
          }
          return;
        }

        // Step 2: Get candidates from registry
        const candidates = this.instinctRegistry.get(activeOwlName);
        if (candidates.length === 0) {
          log.engine.debug("[Instincts] instinct.evaluate: no candidates — skipping", {
            owl: activeOwlName,
            durationMs: Date.now() - instinctStart,
          });
          return;
        }

        // Step 3: Run heuristic keyword check (0ms)
        const heuristicMatched = this.instinctEngineV2.evaluateHeuristic(candidates, text);
        const allHaveKeywords = candidates.every(c => c.keywords && c.keywords.length > 0);

        log.engine.debug("[Instincts] instinct.evaluate: heuristic done", {
          owl: activeOwlName,
          candidates: candidates.length,
          heuristicMatched: heuristicMatched.length,
          allHaveKeywords,
          durationMs: Date.now() - instinctStart,
        });

        // Step 4: Use heuristic result if it matched something OR if every candidate
        // has keywords (meaning heuristic gave a complete, authoritative answer)
        if (heuristicMatched.length > 0 || allHaveKeywords) {
          log.engine.debug("[Instincts] instinct.evaluate: using heuristic result — skipping LLM", {
            owl: activeOwlName,
            reason: heuristicMatched.length > 0 ? "heuristic matched" : "all candidates have keywords",
            matchCount: heuristicMatched.length,
            durationMs: Date.now() - instinctStart,
          });
          if (heuristicMatched.length > 0) {
            const block = InstinctEngine.buildConstraintBlock(heuristicMatched);
            const base = engineCtx.specialistPrompt ?? "";
            engineCtx.specialistPrompt = base + block;
            engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
            log.engine.info(
              `[Instincts] Injected ${heuristicMatched.length} constraint(s) for owl "${activeOwlName}" (heuristic)`,
            );
          }
          return;
        }

        // Step 5: Fall back to LLM — some candidates have no keywords so heuristic
        // cannot give a complete answer
        log.engine.debug("[Instincts] instinct.evaluate: falling back to LLM — candidates without keywords present", {
          owl: activeOwlName,
          candidatesWithoutKeywords: candidates.filter(c => !c.keywords || c.keywords.length === 0).length,
        });
        const matchedInstincts = await this.instinctEngine!.evaluate(activeOwlName, text);
        // Populate the V2 cache with the LLM result so subsequent identical messages hit cache
        this.instinctEngineV2.getCached(text); // no-op read to avoid double-set; cache was set by evaluateHeuristic above
        log.engine.debug("[Instincts] instinct.evaluate: LLM evaluation complete", {
          owl: activeOwlName,
          matchCount: matchedInstincts.length,
          durationMs: Date.now() - instinctStart,
        });
        if (matchedInstincts.length > 0) {
          const block = InstinctEngine.buildConstraintBlock(matchedInstincts);
          const base = engineCtx.specialistPrompt ?? "";
          engineCtx.specialistPrompt = base + block;
          engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
          log.engine.info(
            `[Instincts] Injected ${matchedInstincts.length} constraint(s) for owl "${activeOwlName}" (LLM)`,
          );
        }
      }, { owl: activeOwlName });
    }

    const orchestrator = this.getOrchestrator();

    // ─── Tier escalation retry loop — low → mid → high on engine failure ──
    // Attempts the current floor tier first. On failure escalates one step and
    // retries (up to 3 attempts total). Each escalation persists for subsequent
    // messages until the 15-min idle reset fires.
    // eslint-disable-next-line prefer-const
    let orchResult!: Awaited<ReturnType<typeof orchestrator.executeWithFallback>>;
    let lastOrchErr: unknown;
    const { TIER_ORDER: tierOrder } = await import("../intelligence/escalation.js");
    for (let attempt = 0; attempt < tierOrder.length; attempt++) {
      try {
        orchResult = await withSpan("orchestrator.execute", async () => {
          return orchestrator.executeWithFallback(strategy, text, engineCtx, callbacks);
        }, { strategy: strategy.strategy, tier: engineCtx.escalationFloor });
        lastOrchErr = undefined;
        break;
      } catch (err) {
        lastOrchErr = err;
        // User-cancelled turns must not be retried at a higher tier.
        if (err instanceof DOMException && err.name === "AbortError") break;
        const currentFloor = engineCtx.escalationFloor ?? "low";
        const currentIdx = tierOrder.indexOf(currentFloor);
        if (currentIdx >= tierOrder.length - 1) {
          log.engine.warn("[TierEscalation] Already at highest tier — propagating error");
          break;
        }
        const nextFloor = escalationManager.escalate();
        log.engine.warn(
          `[TierEscalation] Engine failed at tier ${currentFloor} — retrying with ${nextFloor}`,
        );
        engineCtx.escalationFloor = nextFloor;
      }
    }
    if (lastOrchErr !== undefined) throw lastOrchErr;

    // Convert OrchestrationResult to EngineResponse for standard post-processing
    const response: EngineResponse = {
      content: orchResult.content,
      owlName: orchResult.owlName,
      owlEmoji: orchResult.owlEmoji,
      challenged: false,
      toolsUsed: orchResult.toolsUsed,
      modelUsed: "",
      newMessages: [],
      usage: orchResult.usage,
      pendingFiles: engineCtx.pendingFiles ?? [],
    };

    // ─── Tag response with #OwlName when specialist responded ───────────
    if (activeOwlName !== this.ctx.owl.persona.name && !response.content.endsWith("#Parliament")) {
      response.content = `${response.content.trim()}\n\n#${activeOwlName}`;
      const activeSpec = this.ctx.specializedRegistry?.get(activeOwlName);
      response.owlName = activeOwlName;
      response.owlEmoji = activeSpec?.emoji || "🦉";
    }

    // Capability gap detected — try to synthesize the missing tool and retry
    if (response.pendingCapabilityGap && this.ctx.evolution) {
      // Also queue the gap for background synthesis in case the real-time
      // handler fails — the cognitive loop will pick it up on the next idle tick.
      this.ctx.cognitiveLoop?.enqueueSynthesisTarget(
        response.pendingCapabilityGap.userRequest,
        response.pendingCapabilityGap.description,
        "conversation",
      );
      return await this.handleCapabilityGap(
        message,
        response,
        session,
        engineCtx,
        callbacks,
      );
    }

    await this.saveSession(
      session,
      message.text,
      response.newMessages,
      false,
      response.content,
    );
    this.postProcess(session.messages, session.id, {
      toolsUsed: response.toolsUsed ?? [],
      userId: message.userId,
      channelId: message.channelId,
    });

    // Update working context with owl's response
    if ((this.ctx as any).workingContextManager) {
      const wc = (this.ctx as any).workingContextManager.getOrCreate(message.sessionId);
      wc.setLastOwlResponse(response.content.slice(0, 200));
    }

    // Track unstructured skill outcomes — skills injected as prompt context
    // don't go through the executor, so we infer success/failure from the response.
    if (injectedSkillNames.length > 0 && this.skillInjector) {
      const tracker = this.skillInjector.getTracker();
      const hasGap = !!response.pendingCapabilityGap;
      const responseText = response.content.toLowerCase();
      const looksLikeRefusal =
        hasGap ||
        /\b(?:i (?:can't|cannot|don't have|am unable|don't know how)|not (?:able|possible)|outside my|beyond my)\b/.test(
          responseText,
        );
      const usedTools = (response.toolsUsed ?? []).length > 0;

      for (const name of injectedSkillNames) {
        if (looksLikeRefusal && !usedTools) {
          tracker.recordFailure(name, 0);
          // Queue for re-synthesis — skill matched but couldn't deliver
          this.ctx.cognitiveLoop?.enqueueSynthesisTarget(
            name.replace(/_/g, " "),
            `Skill "${name}" matched but response indicates failure`,
            "conversation",
          );
        } else {
          tracker.recordSuccess(name, 0);
        }
      }
    }

    // PreferenceEnforcer removed in memory refactor — preference enforcement is a no-op.

    // Notify background orchestrator of user activity (delivers digest if returning after absence)
    if (this.ctx.backgroundOrchestrator) {
      const deliverDigest = !!(callbacks.onProgress);
      await this.ctx.backgroundOrchestrator.recordUserActivity(deliverDigest);

      // Register current session for debrief after inactivity threshold
      if (session.messages.length >= 6) {
        this.ctx.backgroundOrchestrator.registerSessionForDebrief(
          session.messages,
          async (formatted) => {
            if (callbacks.onProgress) {
              await callbacks.onProgress(formatted);
            }
          },
        );
      }
    }

    // Track intent state based on this exchange (fire-and-forget)
    this.trackIntent(message.sessionId, message.text, response.content);

    // preferenceRecognizer removed in memory refactor — no-op here.

    // Persist intent state (fire-and-forget)
    this.ctx.intentStateMachine?.save().catch((err) => { log.engine.warn("intentStateMachine save failed", err); });

    // ─── Cognitive Consolidate — replaces detectPreferences + analyzeBehavior ──
    // + groundState.refresh in a single async structured extraction.
    if (this.ctx.cognitivePipeline) {
      this.ctx.cognitivePipeline.postProcess({
        sessionId: message.sessionId,
        userMessage: message.text,
        assistantResponse: response.content,
        toolsUsed: response.toolsUsed ?? [],
        dispatch: null, // CognitivePipeline reads from its own lastDispatch store
        userId: message.userId,
        channelId: message.channelId,
      });
    } else {
      // Legacy path: still run per-message detectors if no cognitive pipeline
      this.detectPreferences(message.text, message.channelId);
      this.analyzeBehavior(message.text, message.channelId);
      if (this.ctx.groundState) {
        const shouldRefresh = this.ctx.groundState.recordTurn();
        if (shouldRefresh && session.messages.length >= 4) {
          const userId = message.sessionId.split(":")[1] || message.sessionId;
          this.runBackground(
            "ground-state-refresh",
            this.ctx.groundState.refresh(session.messages, userId, message.sessionId),
          );
        }
      }
    }

    // Deliver any files queued by send_file tool calls during this run
    await this.deliverPendingFiles(
      engineCtx.pendingFiles ?? [],
      message.channelId,
      message.userId,
    );

    // ─── Epic 2: Verification Wire ─────────────────────────────────
    // After engine response, BEFORE returning to user
    const taskId = message.sessionId;
    const userIntent = message.text;

    // Only verify tool-using turns — recall/question turns have nothing to "complete".
    const toolsUsedCount = (response.toolsUsed ?? []).length;
    if (this.outcomeVerifier && this.falseDoneDetector && this.completionTracker && toolsUsedCount > 0) {
      llmTaskQueue.enqueue("verification", async () => {
        try {
          const verification = await this.outcomeVerifier!.verify(
            taskId,
            response.content,
            userIntent,
            this.ctx.provider,
          );

          if (verification.status === "failed" && this.escalationHandler) {
            const shouldEscalate = this.escalationHandler.shouldEscalate(verification.confidence);
            if (shouldEscalate) {
              log.engine.info(`[Verification] Escalation triggered for taskId=${taskId}`);
            }
          }

          const falseDoneResult = await this.falseDoneDetector!.detect(
            taskId,
            response.content,
            userIntent,
            this.ctx.provider,
          );

          if (falseDoneResult.isFalseDone) {
            log.engine.warn(`[FalseDoneDetector] False [DONE] claim detected for taskId=${taskId}`);
          }

          this.completionTracker!.recordOutcome(
            taskId,
            verification.status === "passed" && !falseDoneResult.isFalseDone,
            "standard",
          );
        } catch (err) {
          log.engine.warn(`[Verification] Wire failed: ${err instanceof Error ? err.message : err}`);
        }
      }, "low");
    } else if (toolsUsedCount === 0) {
      log.engine.debug("[Verification] Skipping — no tools used (recall/question turn)", { taskId });
    }

    // domainExpertise.recordToolExecution removed (DomainExpertiseTracker deleted in refactor).

    // ─── Task commitment detection ──────────────────────────────
    // sync — detectAndCreate return value intentionally discarded
    if (this.ctx.taskOwnershipManager && response.content) {
      this.ctx.taskOwnershipManager.detectAndCreate(
        message.userId,
        activeOwlName,
        session.id,
        response.content,
      );
    }

    // Pellet flywheel hooks removed — pelletStore deleted in memory refactor.
    // Goal verification still runs if goalVerifier is present.
    if (this.goalVerifier && engineCtx.activeSubGoal) {
      const _pelletIds = this.ctx.contextPipeline?.lastRetrievedPelletIds ?? [];
      if (_pelletIds.length > 0) {
        llmTaskQueue.enqueue("goal-verify", async () => {
          try {
            await this.goalVerifier!.verify({
              toolName: "context_retrieval",
              toolArgs: {},
              toolResult: response.content.slice(0, 500),
              subGoal: engineCtx.activeSubGoal!,
              userMessage: message.text,
            });
          } catch (err) {
            log.engine.warn("pellet goal verification failed", err);
          }
        }, "low");
      }
    }

    // ─── Pre-delivery gate (Defect 2 fix) ──────────────────────────────
    const gatedResponse = await runPreDeliveryGate(response, {
      provider: this.ctx.provider,
      userIntent: message.text,
      owlName: activeOwlName,
      owlEmoji: (this.ctx.owl.persona as any).emoji ?? "🦉",
      sessionId: message.sessionId,
      correctionRun: async (correctionPrompt) => {
        const corrResult = await this.getOrchestrator().executeWithFallback(
          strategy,
          correctionPrompt,
          { ...engineCtx },
          callbacks,
        );
        return {
          content: corrResult.content,
          owlName: corrResult.owlName,
          owlEmoji: corrResult.owlEmoji,
          challenged: false,
          toolsUsed: corrResult.toolsUsed,
          modelUsed: "",
          newMessages: [],
          usage: corrResult.usage,
          pendingFiles: [],
        };
      },
    });

    return toGatewayResponse(gatedResponse);
  }

  // ─── Session Lifecycle ───────────────────────────────────────

  /**
   * Gracefully end a session: run memory consolidation + DNA evolution.
   * Call this when a user explicitly ends their session (/quit in CLI).
   */
  async endSession(sessionId: string): Promise<void> {
    const cache = this.sessionManager.getCached(sessionId);
    if (!cache) return;

    const messages = cache.session.messages;

    // digestManager and episodicMemory removed in memory refactor — no-op here.

    // Async fact extraction → facts table (fire-and-forget)
    if (this.ctx.sessionService && messages.length >= 4) {
      const userId = this.ctx.sessionService.getUserId(sessionId)
        ?? (sessionId.split(":").slice(1).join(":") || sessionId);
      this.ctx.sessionService.extractAndStoreFacts(
        sessionId,
        userId,
        (cache?.session.metadata as any)?.owlName ?? this.ctx.owl.persona.name,
        messages,
      ).catch((err) => {
        log.engine.warn(`[endSession:facts] Fact extraction failed: ${err instanceof Error ? err.message : err}`);
      });
    }

    // Legacy memory.md append-only consolidation — retired.
    // Replaced by FactStore (structured, searchable, semantic) + ConversationDigest (L1).
    // The MemoryConsolidator wrote raw text to memory.md which was injected unsearchably.
    // FactStore.add() + PostProcessor "victory lap" cover the same ground with structure.

    // learningOrchestrator removed in learning/ refactor — no-op here.

    // ReflexionEngine consolidation — retired (Phase 3 L3 consolidation).
    // MemoryReflexionEngine is a duplicate of FactStore: structured entries, keyword search,
    // written post-session. FactStore now owns this job with embedding-based search.
    // Behavioral patches (failure signals) still flow through ReflexionEngine in PostProcessor
    // only when ctx.reflexionEngine is explicitly configured — not created ad-hoc here.

    // Inner Life reflection — the owl thinks about its session
    if (this.ctx.innerLife) {
      try {
        await this.ctx.innerLife.reflect();
        log.engine.info("[endSession:innerLife] ✓ reflection completed");
      } catch (err) {
        log.engine.warn(
          `[endSession:innerLife] ✗ reflection failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // DNA evolution
    if (this.ctx.evolutionEngine) {
      try {
        await this.ctx.evolutionEngine.evolve(this.ctx.owl.persona.name);
        log.engine.info(
          `[endSession:dna-evolve(${this.ctx.owl.persona.name})] ✓ completed`,
        );
      } catch (err) {
        log.engine.warn(
          `[endSession:dna-evolve] ✗ failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // Timeline — final snapshot on session end
    if (this.ctx.timelineManager && messages.length > 0) {
      this.ctx.timelineManager.createSnapshot(
        sessionId,
        messages,
        this.ctx.owl.persona.name,
        "Session end snapshot",
      );
      await this.ctx.timelineManager.save().catch((err) => { log.engine.warn("timelineManager save failed", err); });
    }

    // Knowledge extraction — harvest knowledge from full session
    if (this.ctx.knowledgeReasoner && messages.length > 4) {
      try {
        await this.ctx.knowledgeReasoner.extractFromConversation(messages);
        await this.ctx.knowledgeGraph?.save();
        log.engine.info("[endSession:knowledge] ✓ extracted");
      } catch (err) {
        log.engine.warn(
          `[endSession:knowledge] ✗ failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // Pattern analysis — sessionStore removed; skip session listing here.

    // Update user mental model baseline on session end
    if (this.userMentalModel) {
      this.userMentalModel.endSession();
    }

    // microLearner removed in learning/ refactor — no-op here.

    // Persist all feature module state
    const saveResults = await Promise.allSettled([
      this.ctx.trustChain?.save?.(),
      this.ctx.predictiveQueue?.save?.(),
      this.ctx.skillArena?.save?.(),
    ]);
    for (const [i, result] of saveResults.entries()) {
      if (result.status === "rejected") {
        const names = ["trustChain", "predictiveQueue", "skillArena"];
        log.engine.warn(
          `[endSession] ${names[i]} save failed: ${result.reason instanceof Error ? result.reason.message : result.reason}`,
        );
      }
    }
  }

  // ─── Response Feedback (👍/👎) ────────────────────────────────

  /**
   * Register a pending feedback context after sending a response.
   * Called by channel adapters after they deliver the response and the
   * feedback keyboard. feedbackId is the callback_data key sent with buttons.
   */
  registerFeedback(
    feedbackId: string,
    context: {
      sessionId: string;
      userId: string;
      userMessage: string;
      assistantSummary: string;
      toolsUsed: string[];
    },
  ): void {
    this.pendingFeedback.set(feedbackId, { ...context, createdAt: Date.now() });
  }

  /**
   * Record a 👍/👎 signal from the user.
   * Called by channel adapters when the user presses a feedback button.
   *
   * Like → confirm the success recipe with boosted confidence (source: confirmed)
   * Dislike → record negative fact + queue user request for re-synthesis
   */
  async recordFeedback(
    feedbackId: string,
    signal: "like" | "dislike",
  ): Promise<void> {
    const ctx = this.pendingFeedback.get(feedbackId);
    if (!ctx) return; // expired or unknown
    this.pendingFeedback.delete(feedbackId); // one-shot

    const { sessionId, userId, userMessage, assistantSummary, toolsUsed } = ctx;

    // Persist to FeedbackStore
    if (this.ctx.feedbackStore) {
      await this.ctx.feedbackStore
        .record({
          id: feedbackId,
          sessionId,
          userId,
          signal,
          userMessage,
          assistantSummary,
          toolsUsed,
          timestamp: new Date().toISOString(),
        })
        .catch((err) => {
          log.engine.warn(
            `[Feedback] FeedbackStore.record failed: ${err instanceof Error ? err.message : err}`,
          );
        });
    }

    // Record owl performance metric (Phase 4 — data-driven DNA evolution)
    if (this.ctx.db) {
      const owlName = this.ctx.owl.persona.name;
      const metric = signal === "like" ? "feedback_like" : "feedback_dislike";
      const topic = userMessage.slice(0, 80);
      this.ctx.db.owlPerf.record(owlName, sessionId, userId, metric, topic);

      // Phase B — update trajectory reward with the feedback signal
      try {
        this.ctx.db.trajectories.applyFeedback(sessionId, signal);
      } catch (err) {
        log.engine.warn("trajectory feedback update failed", err);
      }

      // Phase E2 — delayed Parliament verdict validation
      // When we know the outcome (like = correct, dislike = wrong), validate
      // any unvalidated Parliament verdicts from this session.
      try {
        const pendingVerdicts =
          this.ctx.db.parliamentVerdicts.getPendingValidation(5);
        const sessionVerdicts = pendingVerdicts.filter(
          (v) => v.sessionId === sessionId,
        );
        const validationSignal =
          signal === "like" ? ("correct" as const) : ("wrong" as const);
        const rewardDelta = signal === "like" ? 0.5 : -0.5;
        for (const v of sessionVerdicts) {
          this.ctx.db.parliamentVerdicts.validate(
            v.id,
            validationSignal,
            rewardDelta,
          );
        }
      } catch (err) {
        log.engine.warn("parliament verdict validation failed", err);
      }
    }

    if (signal === "like") {
      // factStore removed in memory refactor — like signal recorded in log only.
      log.engine.info(`[Feedback] 👍 confirmed for session ${sessionId}`);
    } else {
      // factStore removed in memory refactor — dislike signal queued for re-synthesis only.

      // Queue the user's request for background synthesis — find a better approach
      this.ctx.cognitiveLoop?.enqueueSynthesisTarget(
        userMessage.slice(0, 100),
        `User disliked response to: "${userMessage.slice(0, 80)}"`,
        "conversation",
      );
      log.engine.info(
        `[Feedback] 👎 dislike for session ${sessionId}, queued for re-synthesis`,
      );
    }
  }

  // ─── Feature Module Initialization ──────────────────────────

  private initFeatureModules(): void {
    // Trust Chain — load trust scores from disk
    if (this.ctx.trustChain) {
      this.ctx.trustChain
        .load()
        .catch((err) =>
          log.engine.warn(`[feature] Trust Chain load failed: ${err}`),
        );
      log.engine.info("[feature] Trust Chain initialized");
    }

    // Knowledge Graph — load graph from disk
    if (this.ctx.knowledgeGraph) {
      this.ctx.knowledgeGraph
        .load()
        .catch((err) =>
          log.engine.warn(`[feature] Knowledge Graph load failed: ${err}`),
        );
      log.engine.info("[feature] Knowledge Graph initialized");
    }

    // Timeline Manager — load snapshots
    if (this.ctx.timelineManager) {
      this.ctx.timelineManager
        .load()
        .catch((err) =>
          log.engine.warn(`[feature] Timeline load failed: ${err}`),
        );
      log.engine.info("[feature] Timeline Manager initialized");
    }

    // Collab Sessions — load persisted sessions
    if (this.ctx.collabManager) {
      this.ctx.collabManager.loadAll();
      log.engine.info("[feature] Collab Session Manager initialized");
    }

    // Pattern Analyzer — load patterns
    if (this.ctx.patternAnalyzer) {
      this.ctx.patternAnalyzer
        .load()
        .catch((err) =>
          log.engine.warn(`[feature] Pattern Analyzer load failed: ${err}`),
        );
      log.engine.info("[feature] Pattern Analyzer initialized");
    }

    // Predictive Queue — load queue
    if (this.ctx.predictiveQueue) {
      this.ctx.predictiveQueue
        .load()
        .catch((err) =>
          log.engine.warn(`[feature] Predictive Queue load failed: ${err}`),
        );
      log.engine.info("[feature] Predictive Queue initialized");
    }

    // Skill Arena — load tournament data
    if (this.ctx.skillArena) {
      this.ctx.skillArena
        .load()
        .catch((err) =>
          log.engine.warn(`[feature] Skill Arena load failed: ${err}`),
        );
      log.engine.info("[feature] Skill Arena initialized");
    }

    // Scheduled message delivery tick — polls every 5 seconds for due timers
    this.lifecycle.startTimer("scheduled-delivery", 5_000, async () => {
      await this.proactiveSvc.deliverScheduled(getReadyMessages);
    });
    log.gateway.info("LifecycleCoordinator.startTimer: scheduled-delivery tick started");

    // Persist new modules on process exit
    this.lifecycle.register("feature-modules-shutdown", async () => {
      await this.ctx.trustChain?.save?.().catch((err: Error) => { log.gateway.error("trustChain save failed", err, {}); });
      await this.ctx.knowledgeGraph?.save?.().catch((err: Error) => { log.gateway.error("knowledgeGraph save failed", err, {}); });
      await this.ctx.timelineManager?.save?.().catch((err: Error) => { log.gateway.error("timelineManager save failed", err, {}); });
      await this.ctx.patternAnalyzer?.save?.().catch((err: Error) => { log.gateway.error("patternAnalyzer save failed", err, {}); });
      await this.ctx.predictiveQueue?.save?.().catch((err: Error) => { log.gateway.error("predictiveQueue save failed", err, {}); });
      await this.ctx.skillArena?.save?.().catch((err: Error) => { log.gateway.error("skillArena save failed", err, {}); });
      this.ctx.backgroundJobRunner?.stop();
      this.ctx.backgroundOrchestrator?.stop();
    });
  }

  // ─── Proactive Messaging ─────────────────────────────────────

  /**
   * Send a proactive message to a specific user on a specific channel.
   */
  async sendProactive(
    channelId: string,
    userId: string,
    text: string,
    preformatted = false,
  ): Promise<void> {
    await this.proactiveSvc.deliver(channelId, userId, text, preformatted);
  }

  /**
   * Broadcast a proactive message to all active users across all channels.
   */
  async broadcastProactive(text: string): Promise<void> {
    const response: GatewayResponse = {
      content: text,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
    };
    for (const adapter of this.adapters.values()) {
      await adapter
        .broadcast(response)
        .catch((err) =>
          log.engine.warn(
            `Broadcast failed on ${adapter.id}: ${err instanceof Error ? err.message : err}`,
          ),
        );
    }
  }

  // ─── Status Queries ──────────────────────────────────────────

  getOwl() {
    return this.ctx.owl;
  }

  getDb() {
    return this.ctx.db;
  }
  getPelletStore() {
    return undefined;
  }
  getEventBus() {
    return this.ctx.eventBus;
  }
  getProvider() {
    return this.ctx.provider;
  }
  getConfig() {
    return this.ctx.config;
  }
  getOwlRegistry() {
    return this.ctx.owlRegistry;
  }
  getToolRegistry() {
    return this.ctx.toolRegistry;
  }
  getMcpManager() {
    return this.ctx.mcpManager;
  }
  getMemoryRepo() {
    return undefined;
  }
  getUnifiedMemory() {
    return undefined;
  }
  getMemoryWriter() {
    return undefined;
  }
  getEvolution() {
    return this.ctx.evolution;
  }
  getSkillsLoader() {
    return this.ctx.skillsLoader;
  }
  getSpecializedRegistry() {
    return this.ctx.specializedRegistry;
  }
  async reloadSpecializedRegistry(): Promise<void> {
    if (!this.ctx.specializedRegistry) return;
    const workspacePath = this.ctx.cwd ?? process.cwd();
    await this.ctx.specializedRegistry.loadAll(workspacePath);
  }

  getWorkspacePath(): string {
    return this.ctx.cwd ?? process.cwd();
  }

  getProviderRegistry() {
    return this.ctx.providerRegistry;
  }

  getProviderManager(): ProviderManager {
    if (!this._providerManager) {
      const registry = this.ctx.providerRegistry;
      if (!registry) throw new Error("[OwlGateway] ProviderRegistry not initialized.");
      const workspacePath = this.getWorkspacePath();
      log.engine.debug("owl-gateway.getProviderManager: initialized", { workspacePath });
      // ProviderManager receives a reference to ctx.config and mutates providers in place.
      // All reads of ctx.config throughout OwlGateway will see provider changes immediately.
      this._providerManager = new ProviderManager(
        registry,
        this.ctx.config,
        workspacePath,
        (cfg) => saveConfig(workspacePath, cfg),
      );
    }
    return this._providerManager;
  }

  getProgressManager(): ProgressManager {
    if (!this._progressManager) {
      log.engine.debug("owl-gateway.getProgressManager: initialized");
      this._progressManager = new ProgressManager(this.gatewayEventBus);
    }
    return this._progressManager;
  }

  getLearningOrchestrator() {
    return undefined;
  }
  getCapabilityLedger() {
    return this.ctx.capabilityLedger;
  }
  getPreferenceStore() {
    return this.ctx.preferenceStore;
  }
  getReflexionEngine() {
    return this.ctx.reflexionEngine;
  }
  getIntentStateMachine() {
    return this.ctx.intentStateMachine;
  }
  getCommitmentTracker() {
    return this.ctx.commitmentTracker;
  }
  getGoalGraph() {
    return this.ctx.goalGraph;
  }
  getProactiveLoop() {
    return this.ctx.proactiveLoop;
  }
  getSessionStore() {
    return undefined;
  }
  getEpisodicMemory() {
    return undefined;
  }
  getKnowledgeCouncil() {
    return this.ctx.knowledgeCouncil;
  }
  getCwd() {
    return this.ctx.cwd;
  }
  getCognitiveLoop() {
    return this.ctx.cognitiveLoop;
  }

  /**
   * Get pending commitments that are due for follow-up.
   * Called by ProactivePinger to send commitment follow-ups.
   */
  getDueCommitments(): Array<{
    id: string;
    message: string;
    intentId: string;
  }> {
    const ct = this.ctx.commitmentTracker;
    if (!ct) return [];
    return ct.getDue().map((c) => ({
      id: c.id,
      message: c.followUpMessage,
      intentId: c.intentId,
    }));
  }

  /**
   * Mark a commitment as acknowledged by the user.
   */
  acknowledgeCommitment(commitmentId: string): void {
    this.ctx.commitmentTracker?.markAcknowledged(commitmentId);
  }

  // ─── Private: Auto-Parliament ────────────────────────────────

  // ─── Private: Natural-Language Watch Commands ─────────────────

  /**
   * Handle natural-language watch/unwatch commands that do not start with `/`
   * and therefore cannot be dispatched by the slash-only FeatureCommandRouter.
   *
   * Patterns handled:
   *   "watch my claude code"  / "watch"  / "watch my opencode [port N]"
   *   "unwatch" / "stop watching" / "stop watch"
   *   "watch status" / "agent status"
   */
  private async handleNaturalLanguageWatchCommands(
    message: GatewayMessage,
  ): Promise<GatewayResponse | null> {
    const text = message.text.trim();
    // Only handle inputs that do NOT start with `/` — slash versions are dispatched via MiscCommandHandler
    if (text.startsWith("/")) return null;

    const owl = this.ctx.owl;
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });
    const mkHtml = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
      preformatted: true,
    });

    // "watch my claude code" / "watch my opencode [port N]" / "watch" → register
    if (/^watch(\s+(my\s+)?(claude[\s-]*(code)?|opencode|agent|coding\s+agent))?(\s+port\s+\d+)?$/i.test(text)) {
      log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: watch register", { text });
      if (!this.agentWatch) {
        log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: exit — agent watch not enabled");
        return mkResp("Agent Watch is not enabled. Start StackOwl with agent watch support.");
      }
      const isOpenCode = /opencode/i.test(text);
      const agentType = isOpenCode ? "opencode" : "claude-code";
      const portMatch = text.match(/port\s+(\d+)/i);
      const port = portMatch ? parseInt(portMatch[1]!, 10) : undefined;
      const reg = await this.agentWatch.registerUser(
        message.userId,
        message.channelId,
        agentType as import("../agent-watch/formatters/telegram.js").AgentType,
        port,
      );
      log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: exit — watch registered");
      return mkHtml(reg.telegramMessage);
    }

    // "unwatch" / "stop watching" / "stop watch" → unwatch all sessions for this user
    if (/^(unwatch|stop watching|stop watch)$/i.test(text)) {
      log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: unwatch", { text });
      if (!this.agentWatch) {
        log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: exit — agent watch not enabled");
        return mkResp("Agent Watch is not enabled.");
      }
      const count = await this.agentWatch.unwatchUser(message.userId);
      log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: exit — unwatched", { count });
      return mkResp(
        count > 0
          ? `👁 Stopped watching ${count} session(s).`
          : "No active watch sessions for you.",
      );
    }

    // "watch status" / "agent status" → show watch status
    if (/^(watch\s+status|agent\s+status)$/i.test(text)) {
      log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: watch status", { text });
      if (!this.agentWatch) {
        log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: exit — agent watch not enabled");
        return mkResp("Agent Watch is not enabled.");
      }
      const st = this.agentWatch.getStatus();
      log.gateway.debug("GatewayCore.handleNaturalLanguageWatchCommands: exit — status returned");
      return mkHtml(
        [
          `👁 <b>Agent Watch</b>`,
          `Active sessions: ${st.activeSessions}`,
          `Pending decisions: ${st.pendingQuestions}`,
        ].join("\n"),
      );
    }

    return null;
  }

  // ─── Private: Explicit Learning Request ──────────────────────

  /**
   * Detect and handle explicit learning requests from the user.
   *
   * Matches patterns like:
   *   /learn send email
   *   Can you learn how to send email?
   *   Learn to track flights
   *   Study cryptocurrency pricing
   *   Research how to control the browser for me
   *
   * When matched, triggers the learning engine's researcher directly
   * instead of letting the model answer the "how to" question.
   */
  private async handleLearnRequest(
    _message: GatewayMessage,
    _callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse | null> {
    // learningOrchestrator and learning/ modules removed in refactor — /learn is a no-op.
    return null;
  }

  // ─── Private: Capability Gap ─────────────────────────────────

  private async handleCapabilityGap(
    message: GatewayMessage,
    response: EngineResponse,
    session: Session,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse> {
    const gap = response.pendingCapabilityGap!;
    log.evolution.evolve(`Capability gap: "${gap.description.slice(0, 80)}"`);

    // ── Phase 1: Learn first ──────────────────────────────────────
    // Before synthesizing a new capability, try to fill the knowledge gap
    // by researching the topic with available tools. Save findings as a pellet
    // and inject the learning into the retry context.
    let gapLearning: import("../agent/gap-learner.js").GapLearningResult | null = null;
    if (this.gapLearner) {
      gapLearning = await this.gapLearner.learn(gap, callbacks.onProgress);
      if (gapLearning.learned && gapLearning.enrichedContext) {
        engineCtx = {
          ...engineCtx,
          memoryContext: [engineCtx.memoryContext, gapLearning.enrichedContext]
            .filter(Boolean)
            .join("\n\n"),
        };
      }
    }

    if (!gapLearning?.learned) {
      await callbacks.onProgress?.(
        `🧠 I don't have that capability yet — building it now...`,
      );
    }

    try {
      const proposal = await this.ctx.evolution!.designSpec(gap, engineCtx);

      if (proposal.existingTool) {
        log.evolution.evolve(`Reusing existing tool: ${proposal.toolName}`);
        await callbacks.onProgress?.(
          `♻️ Found ${proposal.toolName} — retrying...`,
        );
      } else {
        log.evolution.evolve(`Synthesizing: ${proposal.toolName}`);
        await callbacks.onProgress?.(`⚡ Synthesizing ${proposal.toolName}...`);
      }

      const askInstall =
        callbacks.askInstall ?? (async (_deps: string[]) => true);
      const onProgress = callbacks.onProgress ?? (async (_msg: string) => {});

      const { response: retryResponse, filePath } =
        await this.ctx.evolution!.buildAndRetry(
          proposal,
          message.text,
          engineCtx,
          this.engine,
          askInstall,
          onProgress,
        );

      // After skill synthesis, reload the new skill into the registry
      // so it can be matched by the IntentRouter on future requests.
      if (filePath && this.ctx.skillsLoader) {
        const registry = this.ctx.skillsLoader.getRegistry();
        const skillDir = filePath.replace(/\/SKILL\.md$/, "");
        const parentDir = skillDir.replace(/\/[^/]+$/, "");
        try {
          await registry.loadFromDirectory(parentDir);
          if (this.skillInjector) {
            this.skillInjector.reindex();
          }
          log.evolution.info(
            `[Skill] Reindexed after synthesis: ${proposal.toolName}`,
          );
        } catch (err) {
          log.engine.warn("skill reindex after synthesis failed, will retry on next restart", err);
        }
      }

      // userAlreadySaved=true: the user message was saved in the normal path before gap was detected
      await this.saveSession(
        session,
        message.text,
        retryResponse.newMessages,
        true,
        retryResponse.content,
      );
      this.postProcess(session.messages, session.id, {
        toolsUsed: retryResponse.toolsUsed ?? [],
        userId: message.userId,
        channelId: message.channelId,
      });
      // Deliver any files queued during the retry run
      await this.deliverPendingFiles(
        retryResponse.pendingFiles ?? [],
        message.channelId,
        message.userId,
      );

      // Prepend gap learning note so the user knows what happened
      const finalResponse = toGatewayResponse(retryResponse);
      if (gapLearning?.userFacingNote) {
        finalResponse.content = `${gapLearning.userFacingNote}\n\n---\n\n${finalResponse.content}`;
      }

      // pelletId tracking removed (pelletStore deleted in memory refactor).

      return finalResponse;
    } catch (err) {
      log.evolution.error(
        `Gap handling failed: ${err instanceof Error ? err.message : err}`,
      );
      // Fallback: return original apologetic response (user message already saved)
      await this.saveSession(
        session,
        message.text,
        response.newMessages,
        true,
        response.content,
      );
      this.postProcess(session.messages, session.id);
      return toGatewayResponse(response);
    }
  }

  private async saveSession(
    session: Session,
    userText: string,
    newMessages: ChatMessage[],
    userAlreadySaved = false,
    finalContent?: string,
  ): Promise<void> {
    const snapshot = session.messages.slice();
    try {
      // Build the new messages for this turn
      const addedMessages: ChatMessage[] = [];
      if (!userAlreadySaved) {
        addedMessages.push({ role: "user", content: userText });
      }
      for (const msg of newMessages) {
        addedMessages.push(msg);
      }
      if (finalContent?.trim()) {
        addedMessages.push({ role: "assistant", content: finalContent });
      }

      // Apply to in-memory session
      for (const msg of addedMessages) {
        session.messages.push(msg);
      }
      if (session.messages.length > MAX_SESSION_HISTORY) {
        session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
      }

      // Delegate persistence to SessionService (SQLite) when available
      if (this.ctx.sessionService && addedMessages.length > 0) {
        await this.ctx.sessionService.addMessages(session.id, addedMessages);
        return;
      }

      // Fallback: persist to JSON session store via SessionManager
      await this.sessionManager.save(session);
    } catch (err) {
      session.messages = snapshot;
      log.engine.error(
        `[Session] Save failed, rolled back: ${err instanceof Error ? err.message : err}`,
      );
      throw err;
    }
  }

  // ─── Private: Session Cache Eviction ─────────────────────────

  /**
   * Remove stale sessions: delegates cache eviction to SessionManager, but
   * first fires endSession so episodic memory extraction and DNA evolution run
   * for sessions that end without an explicit /quit.
   * Also prunes attempt logs so we don't accumulate memory for dead sessions.
   */
  private evictStaleSessions(): void {
    const now = Date.now();
    const staleIds = this.sessionManager.getStaleIds(now);
    const activeIds = new Set<string>();

    // Collect active ids for attemptLogs pruning
    for (const [key] of this.sessionManager.entries()) {
      if (!staleIds.includes(key)) activeIds.add(key);
    }

    for (const key of staleIds) {
      const cached = this.sessionManager.getCached(key);
      if (cached && cached.session.messages.length >= 2) {
        this.endSession(key).catch((err) => {
          log.engine.warn(
            `[session-evict] endSession failed for "${key}": ${err instanceof Error ? err.message : err}`,
          );
        });
      }
      this.ctx.sessionService?.evictFromCache(key);
      this.stuckStreak.delete(key);
      this.attemptLogs.delete(key);
      log.engine.info(
        `[session-evict] Evicted stale session "${key}" (endSession triggered)`,
      );
    }

    this.attemptLogs.pruneStale(activeIds);

    // Evict pending feedback older than 24 hours
    const FEEDBACK_TTL = 24 * 60 * 60 * 1000;
    for (const [id, fb] of this.pendingFeedback) {
      if (now - fb.createdAt > FEEDBACK_TTL) this.pendingFeedback.delete(id);
    }

    // Delete stale entries from the session cache
    this.sessionManager.evictStale();
  }

  // ─── Private: File Delivery ──────────────────────────────────

  /**
   * Deliver any files queued in pendingFiles by the send_file tool during a run.
   * Uses the adapter's deliverFile method if available — gracefully skips if not.
   * On delivery failure, notifies the user directly so the failure is visible.
   */
  private async deliverPendingFiles(
    files: import("../engine/runtime.js").PendingFile[],
    channelId: string,
    userId: string,
  ): Promise<void> {
    log.engine.debug("[deliverPendingFiles] entry", { fileCount: files.length, channelId, userId });
    if (!files.length) {
      log.engine.debug("[deliverPendingFiles] no files queued — skip");
      return;
    }
    const adapter = this.adapters.get(channelId);
    if (!adapter?.deliverFile) {
      log.engine.warn("[deliverPendingFiles] adapter missing or no deliverFile support", { channelId, fileCount: files.length });
      return;
    }
    const owl = this.getOwl();
    for (const file of files) {
      log.engine.info("[deliverPendingFiles] delivering file", { userId, channelId, path: file.path.slice(0, 200), hasCaption: !!file.caption });
      try {
        await adapter.deliverFile(userId, file.path, file.caption);
        log.engine.info("[deliverPendingFiles] file delivered", { userId, channelId, path: file.path.slice(0, 200) });
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        log.engine.error(
          "[deliverPendingFiles] file delivery failed",
          err as Error,
          { channelId, userId, path: file.path.slice(0, 200) },
        );
        if (adapter.sendToUser) {
          await adapter.sendToUser(userId, {
            content: `⚠️ File delivery failed: ${errMsg}`,
            owlName: owl.persona.name,
            owlEmoji: owl.persona.emoji,
            toolsUsed: [],
          }).catch((notifyErr) => {
            log.engine.warn("[deliverPendingFiles] user notify also failed", { err: (notifyErr as Error).message });
          });
        }
      }
    }
    log.engine.debug("[deliverPendingFiles] exit", { delivered: files.length });
  }

  // ─── Private: Post-processing ────────────────────────────────

  /**
   * Run a named background task via the task queue.
   * Legacy method — new code should use taskQueue.enqueue() directly.
   */
  private runBackground(name: string, task: Promise<unknown>): void {
    this.taskQueue.enqueue(name, () => task);
  }

  /**
   * Fire-and-forget tasks that run after every response.
   * Delegates to the extracted PostProcessor (Improvement #4).
   * Also triggers async inner monologue computation for the NEXT request
   * so the hot path doesn't block on an extra LLM call.
   */
  private postProcess(
    messages: ChatMessage[],
    sessionId?: string,
    metadata?: {
      toolsUsed?: string[];
      userId?: string;
      channelId?: string;
      loopExhausted?: boolean;
      toolFailureCount?: number;
    },
  ): void {
    this.postProcessor.process(messages, sessionId, metadata);

    // ── Phase C: PromptOptimizer trigger check ────────────────────
    // Fire-and-forget — runs after enough bad trajectories accumulate.
    // Never blocks the user response.
    if (this.ctx.db && this.ctx.owlRegistry && this.ctx.provider) {
      const owlName = this.ctx.owl.persona.name;
      const defaultModel =
        (this.ctx.config as any).providers?.anthropic?.defaultModel ??
        "claude-sonnet-4-6";
      setImmediate(() => {
        try {
          const optimizer = new PromptOptimizer(
            this.ctx.db!,
            this.ctx.owlRegistry!,
            this.ctx.provider!,
            defaultModel,
          );
          if (optimizer.shouldRun(owlName)) {
            optimizer.run(owlName).catch((err) => {
              log.engine.warn(
                `[PromptOptimizer] Background run failed: ${err}`,
              );
            });
          }
        } catch (err) {
          log.engine.warn("prompt optimizer setup failed", err);
        }
      });
    }

    // Async inner monologue — compute for the last user message so it's
    // ready for injection in the NEXT request (see runtime.ts getLastMonologue)
    if (this.ctx.innerLife) {
      const lastUserMsg = [...messages]
        .reverse()
        .find((m) => m.role === "user");
      if (lastUserMsg?.content) {
        const innerLifeRef = this.ctx.innerLife;
        const userMsgText =
          typeof lastUserMsg.content === "string" ? lastUserMsg.content : "";
        innerLifeRef.thinkInBackground(userMsgText, messages)
          .catch(() => { /* non-critical */ });
      }
    }
  }

  // ─── Private: Engine Context ─────────────────────────────────

  /**
   * Detect an explicit topic switch using keyword heuristics only — no LLM call.
   * Returns a flush directive string when detected; caller must clear session.messages.
   *
   * Does NOT mutate the history array — caller owns the mutation.
   */
  private detectTopicSwitch(
    text: string,
    history: ChatMessage[],
  ): string | null {
    if (history.length === 0) return null;

    const trimmed = text.trim().toLowerCase();

    const RESET_PHRASES = [
      "new topic",
      "start over",
      "forget that",
      "forget everything",
      "fresh start",
      "reset",
      "clear",
      "/new",
      "new task",
    ];
    const LONE_GREETINGS = ["hi", "hello", "hey", "yo", "sup"];

    const isReset = RESET_PHRASES.some(
      (p) => trimmed === p || trimmed.startsWith(p + " "),
    );
    const isLoneGreeting = LONE_GREETINGS.includes(trimmed);

    if (isReset || isLoneGreeting) {
      log.engine.info(
        `Topic switch detected (keyword: "${trimmed}"). Context will be flushed.`,
      );
      return `[SYSTEM DIRECTIVE: Context has been flushed. You are starting a fresh task.]`;
    }

    return null;
  }

  /**
   * Build the EngineContext for a ReAct loop invocation.
   * Delegates to the extracted ContextBuilder (Improvement #4).
   */
  private async buildEngineContext(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: import("../memory/attempt-log.js").AttemptLog,
    channelId?: string,
    userId?: string,
    continuityResult?:
      | import("../cognition/continuity-engine.js").ContinuityResult
      | null,
  ): Promise<EngineContext> {
    return this.contextBuilder.build(
      session,
      callbacks,
      dynamicSkillsContext,
      isolatedTask,
      attemptLog,
      channelId,
      userId,
      continuityResult,
    );
  }

  /** Fire-and-forget: detect preference statements and persist them. */
  private detectPreferences(userMessage: string, channelId: string): void {
    if (!this.ctx.preferenceStore || !this.preferenceDetector) return;
    this.runBackground(
      "preference-detect",
      this.preferenceDetector.detect(
        userMessage,
        this.ctx.preferenceStore,
        channelId,
      ),
    );
  }

  /** Fire-and-forget: record behavioral signals for inferred preferences. */
  private analyzeBehavior(userMessage: string, channelId: string): void {
    const pm = this.ctx.preferenceModel;
    if (!pm) return;
    this.runBackground(
      "preference-infer",
      (async () => {
        try {
          pm.analyzeMessage(userMessage, channelId);
          await pm.save();
        } catch (err) {
          log.engine.warn(
            `[PreferenceModel] analyzeBehavior failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      })(),
    );
  }

  /** Fire-and-forget: track intent state based on user message and owl response. */
  private trackIntent(
    sessionId: string,
    userMessage: string,
    owlResponse: string,
  ): void {
    const intentSM = this.ctx.intentStateMachine;
    if (!intentSM) return;

    this.runBackground(
      "intent-track",
      (async () => {
        try {
          const existingIntent = intentSM.getActiveForSession(sessionId);

          // Classify what the user is asking for
          const intentType = this.classifyIntentType(userMessage);

          // If this looks like a task-type message and we don't have an active intent, create one
          if (!existingIntent && intentType === "task") {
            const intent = intentSM.create({
              rawQuery: userMessage,
              description: this.summarizeIntent(userMessage),
              type: intentType,
              sessionId,
            });
            intentSM.transition(intent.id, "in_progress");
            log.engine.info(
              `[IntentSM] Created new intent from task: "${intent.description}"`,
            );
            return;
          }

          // If there's an active intent, update it based on the owl's response
          if (existingIntent) {
            intentSM.touch(existingIntent.id);

            // Detect completion signals
            const completionSignals = [
              "完成了",
              "done",
              "finished",
              "completed",
              "success",
              "已经",
              "办好",
              "搞定",
              "没问题了",
            ];
            const looksLikeCompletion = completionSignals.some((s) =>
              owlResponse.toLowerCase().includes(s),
            );
            if (
              looksLikeCompletion &&
              existingIntent.status === "in_progress"
            ) {
              intentSM.transition(existingIntent.id, "completed");
              log.engine.info(
                `[IntentSM] Intent auto-completed: "${existingIntent.description}"`,
              );
              return;
            }

            // Detect commitment language in owl's response
            const commitmentPatterns = [
              {
                pattern:
                  /(?:I'll|I will|I'm going to|let me)\s+(?:remind|check|look into|get back|follow up|send|prepare|update|notify|monitor|track|find out|investigate)/i,
                type: "deadline" as const,
              },
              {
                pattern:
                  /(?:我会|我会帮|我会|I'll|I will).{0,30}(?:提醒|告诉|检查|给你|给你看|通知|发给你)/i,
                type: "deadline" as const,
              },
              {
                pattern:
                  /(?:later|soon|afterwards|tomorrow|next time|in a (?:bit|moment|few))/i,
                type: "time_delay" as const,
              },
              {
                pattern: /(?:稍后|等一下|回头|晚点).{0,20}(?:再说|再|再说)/i,
                type: "time_delay" as const,
              },
              {
                pattern:
                  /(?:when|if|once)\s+.{0,30}(?:available|ready|done|finished|back)/i,
                type: "context_change" as const,
              },
            ];

            for (const { pattern, type } of commitmentPatterns) {
              if (pattern.test(owlResponse)) {
                // Extract a rough deadline (default: 24 hours from now for time-based commitments)
                const deadline =
                  type === "deadline"
                    ? Date.now() + 24 * 60 * 60 * 1000
                    : undefined;

                const statement = this.extractCommitmentStatement(owlResponse);
                const followUpMsg = `Hey, just checking — did ${this.extractCommitmentContext(owlResponse)}?`;

                // Track in intent state machine
                intentSM.addCommitment(existingIntent.id, {
                  statement,
                  madeAt: Date.now(),
                  deadline,
                  followUpMessage: followUpMsg,
                  triggerType: type,
                });

                // Also track in commitment tracker for deadline monitoring
                const ct = this.ctx.commitmentTracker;
                if (ct && deadline) {
                  ct.track({
                    intentId: existingIntent.id,
                    sessionId,
                    statement,
                    deadline,
                    followUpMessage: followUpMsg,
                    context: existingIntent.description,
                  });
                }

                log.engine.info(
                  `[IntentSM] Commitment detected and tracked for follow-up: "${statement.slice(0, 50)}"`,
                );
                break;
              }
            }

            // ─── Thread Promotion Check ──────────────────────────
            // Promote intent to narrative thread if it meets criteria:
            //   - 3+ completed checkpoints
            //   - OR linked to a goal
            if (!existingIntent.isThread) {
              const completedCheckpoints = existingIntent.checkpoints.filter(
                (c) => c.completedAt,
              ).length;
              const shouldPromote =
                completedCheckpoints >= 3 || !!existingIntent.linkedGoalId;

              if (shouldPromote) {
                intentSM.promoteToThread(
                  existingIntent.id,
                  existingIntent.description,
                );
              }
            }
          }
        } catch (err) {
          log.engine.warn(
            `[IntentSM] trackIntent failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      })(),
    );
  }

  private classifyIntentType(
    message: string,
  ): import("../intent/types.js").IntentType {
    const lower = message.toLowerCase();

    // Task patterns — user wants something done
    const TASK_PATTERNS = [
      // English imperative/request patterns
      /^(?:can you|could you|would you|please|help me|i (?:want|need) (?:you )?to)\b/,
      /^(?:set up|create|build|fix|install|configure|deploy|update|add|remove|delete|send|fetch|write|make|run|start|stop)\b/,
      /^(?:book|order|schedule|find|search|check|look up|download|upload)\b/,
      // Chinese patterns
      /^(?:帮我|帮我做|帮我安排|帮我预订|帮我订|帮我查|帮我问一下)/,
      // "I want to..." / "I need to..."
      /\b(?:i want to|i need to|i'd like to|let's|lets)\b/,
    ];

    const isTask = TASK_PATTERNS.some((p) => p.test(lower));
    if (isTask) return "task";

    // Question patterns
    if (
      /[吗？?]|^(?:what|how|why|when|where|who|which|is there|are there|do you|does|can i|should)\b/.test(
        lower,
      )
    )
      return "question";

    // Information patterns
    if (/^(?:tell me|explain|describe|what is|what are)\b/.test(lower))
      return "information";

    return "exploration";
  }

  private summarizeIntent(message: string): string {
    // Simple truncation for now — could use LLM for better summarization
    return message.length > 80 ? message.slice(0, 77) + "..." : message;
  }

  private extractCommitmentStatement(response: string): string {
    // Extract the relevant sentence containing commitment language
    const sentences = response.split(/[。！？.!?]/);
    for (const s of sentences) {
      if (
        /我会|I'll|I will|I'm going to|let me|我会帮|我会检查|我会提醒/.test(s)
      ) {
        return s.trim().slice(0, 200);
      }
    }
    return response.slice(0, 200);
  }

  private extractCommitmentContext(response: string): string {
    // Try to extract what the commitment was about
    const match = response.match(
      /(?:关于|关于|regarding|about|something|that).{0,50}/i,
    );
    if (match) return match[0].trim();
    return "that thing I said I'd follow up on";
  }

  /**
   * Fire-and-forget: refresh a narrative thread's summary and progress
   * using a lightweight LLM call. Called when a thread is resumed.
   */
  private refreshThreadSummary(
    intentId: string,
    recentMessages: ChatMessage[],
  ): void {
    const intentSM = this.ctx.intentStateMachine;
    if (!intentSM) return;

    this.runBackground(
      "thread-refresh",
      (async () => {
        try {
          const msgs = recentMessages
            .slice(-8)
            .map((m) => `${m.role}: ${m.content.slice(0, 150)}`)
            .join("\n");

          const intent = intentSM.getActive().find((i) => i.id === intentId);
          if (!intent?.isThread) return;

          const prompt = `Given thread "${intent.summary}" and recent messages, update the thread state.

Messages:
${msgs}

Return JSON:
{
  "summary": "1-sentence summary of what this thread is about",
  "progress": "1-sentence of what's been done so far",
  "nextSteps": ["what should happen next"]
}

Return ONLY valid JSON.`;

          const response = await Promise.race([
            this.ctx.provider.chat(
              [{ role: "user", content: prompt }],
              undefined,
              { temperature: 0, maxTokens: 200 },
            ),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error("timeout")), 3000),
            ),
          ]);

          const match = response.content.trim().match(/\{[\s\S]*\}/);
          if (!match) return;

          const parsed = JSON.parse(match[0]) as {
            summary?: string;
            progress?: string;
            nextSteps?: string[];
          };

          if (parsed.summary) intent.summary = parsed.summary;
          if (parsed.progress) intent.progress = parsed.progress;
          if (parsed.nextSteps) intent.nextSteps = parsed.nextSteps;
          intent.updatedAt = Date.now();

          log.engine.info(
            `[Thread] Refreshed: "${(intent.summary ?? "").slice(0, 50)}"`,
          );
        } catch (err) {
          log.engine.warn(
            `[Thread] Refresh failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      })(),
    );
  }
}

// ─── Helpers ─────────────────────────────────────────────────────

function toGatewayResponse(r: EngineResponse): GatewayResponse {
  return {
    content: r.content,
    owlName: r.owlName,
    owlEmoji: r.owlEmoji,
    toolsUsed: r.toolsUsed,
    usage: r.usage,
  };
}

export function makeSessionId(channelId: string, userId: string): string {
  return `${channelId}:${userId}`;
}

export function makeMessageId(): string {
  return uuidv4();
}

const MAX_MESSAGE_TEXT = 32_000;

/** Normalizes user-supplied text into a GatewayMessage. Returns null for empty/whitespace input. */
export function makeMessage(
  channelId: string,
  userId: string,
  text: string,
  sessionId?: string
): GatewayMessage | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const safe =
    trimmed.length > MAX_MESSAGE_TEXT
      ? trimmed.slice(0, MAX_MESSAGE_TEXT) + "\n[…message truncated]"
      : trimmed;
  return {
    id: makeMessageId(),
    channelId,
    userId,
    sessionId: sessionId ?? makeSessionId(channelId, userId),
    text: safe,
  };
}
