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
import type { Session } from "../memory/store.js";
import type { EngineContext, EngineResponse } from "../engine/runtime.js";
import { OwlEngine, EXHAUSTION_MARKER } from "../engine/runtime.js";
import { PromptOptimizer } from "../engine/prompt-optimizer.js";
import { AttemptLogRegistry } from "../memory/attempt-log.js";
import { SkillContextInjector } from "../skills/injector.js";
import { ClawHubClient } from "../skills/clawhub.js";
import { SkillTracker } from "../skills/tracker.js";
import { log } from "../logger.js";
// MemoryConsolidator and MemoryReflexionEngine retired (Phase 3 L3 consolidation).
// Replaced by FactStore + ConversationDigest. Imports removed to prevent dead-code warnings.
import { PreferenceDetector } from "../preferences/detector.js";
import { MicroLearner } from "../learning/micro-learner.js";
import { ProactiveAnticipator } from "../learning/anticipator.js";
import { classifyStrategy } from "../orchestrator/classifier.js";
import { TaskOrchestrator } from "../orchestrator/orchestrator.js";
import type {
  GatewayMessage,
  GatewayResponse,
  GatewayCallbacks,
  ChannelAdapter,
  GatewayContext,
} from "./types.js";
import type { GatewayMiddleware, MiddlewareContext } from "./middleware.js";
import { RateLimitMiddleware, LoggingMiddleware } from "./middleware.js";
import { getReadyMessages } from "../tools/utils/timer.js";
import { PostProcessor } from "./handlers/post-processor.js";
import { ContextBuilder } from "./handlers/context-builder.js";
import { SessionManager } from "./handlers/session-manager.js";
import { GapLearner } from "../agent/gap-learner.js";
import { InnerLifeDNABridge } from "../owls/inner-bridge.js";
import { TaskQueue } from "../queue/task-queue.js";
import {
  computeTemporalContext,
  loadPreviousSession,
} from "../cognition/temporal-context.js";
import {
  classifyContinuity,
  type ContinuityResult,
} from "../cognition/continuity-engine.js";
import {
  getUnextractedSegments,
  getSegmentMessages,
} from "../memory/session-segmenter.js";
import { UserMentalModel } from "../cognition/user-mental-model.js";
import { ConversationDigestManager } from "../memory/conversation-digest.js";
import { MemoryDatabase } from "../memory/db.js";
import { MessageCompressor } from "../memory/compressor.js";
import { FeedbackStore } from "../feedback/store.js";
import { OutputFilter, resolveOutputMode } from "./output-filter.js";
import { SessionBriefGenerator } from "../cognition/session-brief.js";
import { LoopDetector } from "../cognition/loop-detector.js";

// ─── Constants ───────────────────────────────────────────────────

const MAX_SESSION_HISTORY = 50;
const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours

interface SessionCache {
  session: Session;
  lastActivity: number;
}

// ─── Gateway ─────────────────────────────────────────────────────

export class OwlGateway {
  private engine: OwlEngine;
  private adapters: Map<string, ChannelAdapter> = new Map();
  private sessions: Map<string, SessionCache> = new Map();
  private skillInjector: SkillContextInjector | null = null;
  /** Singleton PreferenceDetector — avoids re-constructing on every message */
  private preferenceDetector: PreferenceDetector | null = null;
  /** Per-message micro-learner for lightweight signal extraction */
  private microLearner: MicroLearner | null = null;
  /** Proactive anticipator for cross-system predictions */
  private anticipator: ProactiveAnticipator | null = null;
  /** Lazy-initialized task orchestrator for multi-strategy execution */
  private taskOrchestrator: TaskOrchestrator | null = null;
  /** Track last active channel + user for scheduled message delivery */
  private lastActiveChannel: string | null = null;
  private lastActiveUserId: string | null = null;

  /** Agent Watch — supervises external coding agent sessions */
  agentWatch: import("../agent-watch/index.js").AgentWatchManager | null = null;
  /** Timer tick interval for scheduled message delivery */
  private timerTickInterval: NodeJS.Timeout | null = null;

  // ─── Phase 3: Relational Intelligence ────────────────────────
  private sessionBriefGenerator: SessionBriefGenerator | null = null;
  private loopDetector: LoopDetector = new LoopDetector();

  // ─── Extracted Handlers (Improvement #4) ───────────────────
  private postProcessor: PostProcessor;
  private contextBuilder: ContextBuilder;
  /** Extracted session manager — used by new code paths, old inline code migrating incrementally */
  sessionManager: SessionManager;
  private taskQueue: TaskQueue;
  private gapLearner: GapLearner | null = null;

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
   * Cross-turn attempt logs — one per active session.
   * Persists across handle() calls so the model always knows what was
   * already tried in previous messages of this conversation.
   */
  private attemptLogs = new AttemptLogRegistry();

  /** User mental model — infers user state from behavioral signals */
  private userMentalModel: UserMentalModel | null = null;

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

    // Initialize task queue (Improvement #2)
    this.taskQueue = ctx.taskQueue ?? new TaskQueue(ctx.config.queue);

    // Initialize gap learner — runs when capability gaps are detected
    if (ctx.pelletStore && ctx.toolRegistry) {
      this.gapLearner = new GapLearner(
        ctx.provider,
        ctx.owl,
        ctx.config,
        ctx.toolRegistry,
        ctx.pelletStore,
      );
    }

    // Initialize session manager (Improvement #4)
    this.sessionManager = new SessionManager(
      ctx.sessionStore,
      ctx.owl.persona.name,
      ctx.eventBus ?? null,
    );

    // Ensure DNA is persisted on process exit.
    // Without this, any mutations from the current session are lost when the
    // process exits normally (ctrl-c, pm2 restart, etc.).
    const saveDNAOnExit = () => {
      if (ctx.owlRegistry) {
        const owl = ctx.owlRegistry.getDefault?.() ?? ctx.owl;
        ctx.owlRegistry.saveDNA(owl.persona.name).catch(() => {});
      }
    };
    process.once("exit", saveDNAOnExit);
    process.once("SIGINT", () => {
      saveDNAOnExit();
      process.exit(0);
    });
    process.once("SIGTERM", () => {
      saveDNAOnExit();
      process.exit(0);
    });

    // Preference detector — created once if preference store is configured
    if (ctx.preferenceStore) {
      this.preferenceDetector = new PreferenceDetector(ctx.provider);
    }

    // Micro-learner — lightweight per-message signal extraction
    // Uses the provided instance or creates one automatically
    if (ctx.microLearner) {
      this.microLearner = ctx.microLearner;
    } else {
      const workspacePath = ctx.cwd ?? process.cwd();
      this.microLearner = new MicroLearner(workspacePath);
      this.microLearner.load().catch(() => {});
    }

    // Proactive anticipator — cross-system prediction engine
    if (ctx.anticipator) {
      this.anticipator = ctx.anticipator;
    } else if (this.microLearner) {
      this.anticipator = new ProactiveAnticipator(
        this.microLearner,
        ctx.patternAnalyzer ?? null,
        ctx.provider,
      );
    }

    // Phase 3: Session Brief Generator — lazy, only needs provider
    this.sessionBriefGenerator = new SessionBriefGenerator(ctx.provider);

    // User mental model — behavioral state inference
    this.userMentalModel = new UserMentalModel();

    // Initialize extracted handlers (Improvement #4)
    // ContextBuilder is initialized after skillInjector below
    // InnerLifeDNABridge — connects inner life state to DNA mutations
    const innerLifeBridge = ctx.owlRegistry
      ? new InnerLifeDNABridge(ctx.owlRegistry)
      : null;

    this.postProcessor = new PostProcessor(
      ctx,
      this.taskQueue,
      ctx.eventBus ?? null,
      ctx.selfLearningCoordinator ?? null,
      this.anticipator,
      ctx.costTracker ?? null,
      innerLifeBridge,
    );
    this.contextBuilder = new ContextBuilder(
      ctx,
      this.microLearner,
      null,
      this.userMentalModel,
    );

    // Attach GoalExtractor to PostProcessor (Phase 1)
    if (ctx.db && ctx.provider) {
      import("../agent/goal-extractor.js").then(({ GoalExtractor }) => {
        const extractor = new GoalExtractor(ctx.provider, ctx.db!);
        this.postProcessor.setGoalExtractor(extractor);
      }).catch(() => {});
    }

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
      const skillTracker = new SkillTracker(ctx.cwd ?? process.cwd());
      skillTracker.load().catch(() => {}); // Non-blocking load

      // Use synthesis provider (Anthropic) for skill routing LLM disambiguation
      const synthesisProviderName =
        ctx.config.synthesis?.provider ?? "anthropic";
      let skillProvider = ctx.provider;
      if (ctx.providerRegistry) {
        try {
          skillProvider = ctx.providerRegistry.get(synthesisProviderName);
        } catch {
          // Fallback to default provider if synthesis provider not registered
        }
      }

      this.skillInjector = new SkillContextInjector(
        registry,
        {
          maxSkills: 5,
          autoSearchClawHub: true,
          clawHubTargetDir:
            ctx.config.skills?.directories?.[0] || "./workspace/skills",
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

    // Auto-initialize MessageCompressor (Phase 2 — batch summarization every 20 msgs)
    if (!ctx.compressor && ctx.db) {
      ctx.compressor = new MessageCompressor(ctx.db, ctx.provider);
      log.engine.info(
        "[memory] MessageCompressor initialized (batch size: 20)",
      );
    }

    // Auto-initialize ConversationDigestManager (L1 working memory) if not provided.
    // Always enabled — requires no config, no external deps, writes to workspace/memory/digests/.
    if (!ctx.digestManager) {
      const workspacePath = ctx.cwd ?? process.cwd();
      ctx.digestManager = new ConversationDigestManager(workspacePath);
      log.engine.info(
        "[memory] ConversationDigestManager initialized (L1 working memory)",
      );
    }

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

    // Wire learning orchestrator → cognitive loop gap bridge.
    // When the orchestrator discovers knowledge gaps from conversations,
    // forward them to the cognitive loop's synthesis queue so skills get built.
    if (ctx.learningOrchestrator && ctx.cognitiveLoop) {
      ctx.learningOrchestrator.setCapabilityGapCallback((gap, description) => {
        ctx.cognitiveLoop!.enqueueSynthesisTarget(
          gap,
          description,
          "conversation",
        );
      });
    }

    // Initialize new feature modules (all optional, fire-and-forget load)
    this.initFeatureModules();
  }

  // ─── Adapter Registry ────────────────────────────────────────

  register(adapter: ChannelAdapter): void {
    this.adapters.set(adapter.id, adapter);
    log.engine.info(`Channel registered: ${adapter.name} [${adapter.id}]`);
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
        this.ctx.pelletStore!,
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
      next.catch(() => {}),
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
    const session = await this.getOrCreateSession(message);

    // Check if user is giving feedback on a recent gap-learning response
    this.postProcessor.absorbGapFeedback(session.messages, session.id);

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

    // Check for /reset command - clear session history
    if (message.text.trim().toLowerCase() === "/reset") {
      session.messages = [];
      this.attemptLogs.delete(message.sessionId);
      await this.ctx.sessionStore.saveSession(session);
      log.engine.info(`Session reset for ${message.sessionId}`);
      return {
        content:
          "🧹 Context cleared! Starting fresh. What would you like to work on?",
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
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
          const emoji = skill.metadata.openclaw?.emoji || "⚡";
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

    // ─── New Feature Commands ──────────────────────────────────
    const featureResult = await this.handleFeatureCommand(message, callbacks);
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
        this.ctx.sessionStore,
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
          fastProvider = this.ctx.providerRegistry.get("anthropic");
        } catch {
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
      const wc = this.ctx.workingContextManager?.getOrCreate(message.sessionId);
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
            this.ctx.groundState.archive(uid).catch(() => {});
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
        if (this.ctx.workingContextManager) {
          this.ctx.workingContextManager.getOrCreate(message.sessionId).clear();
        }
      }
    }

    // ─── Episodic Memory: Segment Extraction (Phase 3) ──────────
    // When a gap or topic switch is detected, extract episodes from completed segments.
    // Also trigger when session has grown significantly since last extraction (Step 4).
    // Fire-and-forget — don't block the response.
    const lastExtractedAt =
      (session.metadata as any).episodicLastExtractedAt ?? 0;
    const messagesSinceLastExtraction =
      lastExtractedAt === 0
        ? session.messages.length
        : session.messages.length -
          ((session.metadata as any).episodicExtractedUpTo ?? 0);

    const shouldExtract =
      this.ctx.episodicMemory &&
      session.messages.length >= 4 &&
      ((continuityResult &&
        ["TOPIC_SWITCH", "FRESH_START"].includes(
          continuityResult.classification,
        )) ||
        messagesSinceLastExtraction >= 6);

    if (shouldExtract) {
      this.runBackground(
        "episode-extract",
        (async () => {
          try {
            const extractedUpTo =
              (session.metadata as any).episodicExtractedUpTo ?? 0;
            const segments = getUnextractedSegments(session, extractedUpTo);

            // For short sessions with no completed segments, extract from recent messages
            const doShortSessionExtract =
              segments.length === 0 &&
              lastExtractedAt === 0 &&
              session.messages.length >= 4;

            if (doShortSessionExtract) {
              const recentMessages = session.messages.slice(
                Math.max(0, session.messages.length - 8),
              );
              await this.ctx.episodicMemory!.extractFromMessages(
                recentMessages,
                session.id,
                this.ctx.owl.persona.name,
                this.ctx.provider,
              );
              (session.metadata as any).episodicLastExtractedAt = Date.now();
            } else {
              for (const segment of segments.slice(0, 2)) {
                // Max 2 segments per trigger
                const segMessages = getSegmentMessages(session, segment);
                if (segMessages.length < 3) continue;

                await this.ctx.episodicMemory!.extractFromMessages(
                  segMessages,
                  session.id,
                  this.ctx.owl.persona.name,
                  this.ctx.provider,
                );

                // Track extraction progress in session metadata
                (session.metadata as any).episodicExtractedUpTo =
                  segment.endIndex + 1;
              }
            }

            if (segments.length > 0 || doShortSessionExtract) {
              await this.ctx.sessionStore.saveSession(session);
            }
          } catch (err) {
            log.engine.warn(
              `[EpisodicMemory] Segment extraction failed: ${err instanceof Error ? err.message : err}`,
            );
          }
        })(),
      );
    }

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
    if (this.ctx.workingContextManager) {
      const wc = this.ctx.workingContextManager.getOrCreate(message.sessionId);
      wc.setLastUserMessage(message.text);
    }

    // Evaluate behavioral skills — may inject reactive constraints
    let text = message.text;
    if (this.ctx.skillsEngine && this.ctx.skillsRegistry) {
      const behavioralSkills = this.ctx.skillsRegistry.getBehavioral(
        this.ctx.owl.persona.name,
      );
      const triggered = await this.ctx.skillsEngine.evaluate(
        text,
        behavioralSkills,
        {
          provider: this.ctx.provider,
          owl: this.ctx.owl,
          config: this.ctx.config,
        },
      );
      if (triggered) {
        log.engine.info(`Skill triggered: ${triggered.name}`);
        text = `User Input: ${text}\n\n[SYSTEM OVERRIDE - SKILL TRIGGERED]\n${triggered.instructions}`;
      }
    }

    // Track last active channel/user for scheduled message delivery
    this.lastActiveChannel = message.channelId;
    this.lastActiveUserId = message.userId;

    log.engine.incoming(message.channelId, message.text);

    // Dynamic skill injection — uses BM25 + usage-weighted semantic routing
    let dynamicSkillsContext = "";
    let injectedSkillNames: string[] = [];
    // Skip skill routing unless the message looks like an action request.
    // The IntentRouter's 5-tier pipeline (BM25 + semantic re-rank + LLM call)
    // adds 1–3 seconds of latency and is wasted on conversational messages.
    //
    // Pre-filter: require at least one action verb keyword AND a non-trivial message.
    // Conversational messages ("hi", "thanks", "what do you think?") skip entirely.
    const SKILL_ACTION_KEYWORDS =
      /\b(find|search|create|write|generate|check|analyze|run|scan|fix|build|compare|convert|code|script|calculate|translate|download|fetch|get|show|list|send|open|launch|install|deploy|test|debug|monitor|schedule|remind|automate|summarize|extract|format|parse|execute|compile|scan|audit|review|design)\b/i;
    const isConversational =
      text.trim().length < 15 ||
      /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i.test(
        text.trim(),
      ) ||
      !SKILL_ACTION_KEYWORDS.test(text);
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
        if (false && this.skillInjector!.canExecuteStructured(topSkill)) {
          log.engine.info(`Structured skill execution: ${topSkill.name}`);
          const emoji = topSkill.metadata.openclaw?.emoji || "⚡";
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
            const emoji = s.metadata.openclaw?.emoji || "📋";
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
            episodicMemory: this.ctx.episodicMemory,
            groundState: this.ctx.groundState,
            innerLife: this.ctx.innerLife,
            userId: message.userId,
          });
          if (brief && callbacks.onProgress) {
            await callbacks.onProgress(`\n${brief.formatted}\n`);
          }
        } catch {
          // Non-fatal
        }
      })());
    }

    // ─── Phase 3: Loop Detection ──────────────────────────────────
    // Detect if the user is stuck in a recurring question pattern.
    // If so, inject a root-cause-finding directive into the message text.
    const loopResult = await this.loopDetector.detect(
      message.text,
      this.ctx.episodicMemory,
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
    let strategy = await classifyStrategy(
      message.text,
      this.ctx.owlRegistry.listOwls(),
      this.ctx.toolRegistry?.getAllDefinitions().map((t) => t.name) ?? [],
      session.messages.slice(-6),
      this.ctx.provider,
    );

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

    const engineCtx = await this.buildEngineContext(
      session,
      callbacks,
      dynamicSkillsContext,
      isIsolatedTask,
      this.attemptLogs.get(message.sessionId),
      message.channelId,
      message.userId,
      continuityResult ?? null,
    );

    const orchestrator = this.getOrchestrator();
    const orchResult = await orchestrator.executeWithFallback(
      strategy,
      text,
      engineCtx,
      callbacks,
    );

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
    if (this.ctx.workingContextManager) {
      const wc = this.ctx.workingContextManager.getOrCreate(message.sessionId);
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

    // ─── Phase 3: PreferenceEnforcer ──────────────────────────────
    // 1. Capture explicit preference declarations from this user message.
    // 2. Infer implicit signals (message length, code questions, etc.).
    // 3. Enforce preferences on the response (e.g. trim if conciseness high-confidence).
    if (this.ctx.preferenceEnforcer && this.ctx.preferenceModel) {
      this.runBackground(
        "preference-capture",
        this.ctx.preferenceEnforcer.captureExplicitPreferences(
          message.text,
          this.ctx.preferenceModel,
        ),
      );
      this.runBackground(
        "preference-infer",
        this.ctx.preferenceEnforcer.inferImplicitSignals(
          message.text,
          response.content,
          this.ctx.preferenceModel,
        ),
      );
      try {
        response.content = await this.ctx.preferenceEnforcer.enforceOnResponse(
          response.content,
          message.text,
          this.ctx.preferenceModel,
        );
      } catch {
        // Non-fatal — use original response
      }
    }

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

    // Detect and persist preferences expressed in this message (fire-and-forget)
    this.detectPreferences(message.text, message.channelId);

    // Track intent state based on this exchange (fire-and-forget)
    this.trackIntent(message.sessionId, message.text, response.content);

    // Persist intent state (fire-and-forget)
    this.ctx.intentStateMachine?.save().catch(() => {});

    // Record behavioral signals for preference inference (fire-and-forget)
    this.analyzeBehavior(message.text, message.channelId);

    // Ground state refresh — every N turns, extract facts/decisions/questions (fire-and-forget)
    if (this.ctx.groundState) {
      const shouldRefresh = this.ctx.groundState.recordTurn();
      if (shouldRefresh && session.messages.length >= 4) {
        const userId = message.sessionId.split(":")[1] || message.sessionId;
        this.runBackground(
          "ground-state-refresh",
          this.ctx.groundState.refresh(
            session.messages,
            userId,
            message.sessionId,
          ),
        );
      }
    }

    // Deliver any files queued by send_file tool calls during this run
    await this.deliverPendingFiles(
      engineCtx.pendingFiles ?? [],
      message.channelId,
      message.userId,
    );

    return toGatewayResponse(response);
  }

  // ─── Session Lifecycle ───────────────────────────────────────

  /**
   * Gracefully end a session: run memory consolidation + DNA evolution.
   * Call this when a user explicitly ends their session (/quit in CLI).
   */
  async endSession(sessionId: string): Promise<void> {
    const cache = this.sessions.get(sessionId);
    if (!cache) return;

    const messages = cache.session.messages;

    // Clear L1 digest — session is ending, next session starts fresh
    this.ctx.digestManager?.delete(sessionId).catch(() => {});

    // Episodic memory extraction — extract episode from full session on explicit end
    if (this.ctx.episodicMemory && messages.length >= 4) {
      try {
        await this.ctx.episodicMemory.extractFromMessages(
          messages,
          sessionId,
          this.ctx.owl.persona.name,
          this.ctx.provider,
        );
        log.engine.info("[endSession:episodic] Episode extracted");
      } catch (err) {
        log.engine.warn(
          `[endSession:episodic] Extraction failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // Legacy memory.md append-only consolidation — retired.
    // Replaced by FactStore (structured, searchable, semantic) + ConversationDigest (L1).
    // The MemoryConsolidator wrote raw text to memory.md which was injected unsearchably.
    // FactStore.add() + PostProcessor "victory lap" cover the same ground with structure.

    // Reactive learning (new orchestrator if available, fallback to legacy)
    if (this.ctx.learningOrchestrator) {
      try {
        const cycle =
          await this.ctx.learningOrchestrator.processConversation(messages);
        log.engine.info(
          `[endSession:learning] ✓ orchestrator completed — ${cycle.topicsPrioritized} topics, ` +
            `${cycle.synthesisReport?.pelletsCreated ?? 0} pellets`,
        );
      } catch (err) {
        log.engine.warn(
          `[endSession:learning] ✗ orchestrator failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    } else if (this.ctx.learningEngine) {
      try {
        await this.ctx.learningEngine.processConversation(messages);
        log.engine.info("[endSession:learning] ✓ completed");
      } catch (err) {
        log.engine.warn(
          `[endSession:learning] ✗ failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

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
      await this.ctx.timelineManager.save().catch(() => {});
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

    // Pattern analysis — analyze full session for behavioral patterns
    if (this.ctx.patternAnalyzer) {
      try {
        const sessions = await this.ctx.sessionStore.listSessions();
        if (sessions.length > 0) {
          await this.ctx.patternAnalyzer.analyzeHistory(sessions as any[]);
          await this.ctx.patternAnalyzer.save();
          log.engine.info("[endSession:patterns] ✓ analyzed");
        }
      } catch (err) {
        log.engine.warn(
          `[endSession:patterns] ✗ failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // Update user mental model baseline on session end
    if (this.userMentalModel) {
      this.userMentalModel.endSession();
    }

    // Save micro-learner profile on session end
    if (this.microLearner) {
      await this.microLearner.save().catch((err) => {
        log.engine.warn(
          `[endSession] Micro-learner save failed: ${err instanceof Error ? err.message : err}`,
        );
      });
    }

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
      } catch {
        /* non-fatal */
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
      } catch {
        /* non-fatal */
      }
    }

    if (signal === "like") {
      // User confirmed this worked — write a high-confidence skill fact
      if (this.ctx.factStore && toolsUsed.length > 0) {
        await this.ctx.factStore
          .add({
            userId,
            fact: `User confirmed: I successfully handled "${userMessage}" using [${toolsUsed.join(", ")}]`,
            entity: toolsUsed[0],
            category: "skill",
            confidence: 0.95,
            source: "confirmed",
            expiresAt: new Date(
              Date.now() + 180 * 24 * 60 * 60 * 1000,
            ).toISOString(), // 180 days
          })
          .catch(() => {});
      }
      log.engine.info(`[Feedback] 👍 confirmed for session ${sessionId}`);
    } else {
      // User rejected this response — record it and queue for re-synthesis
      if (this.ctx.factStore) {
        await this.ctx.factStore
          .add({
            userId,
            fact: `User rejected my response to: "${userMessage}". My approach did not satisfy them.`,
            entity: toolsUsed[0] ?? "unknown",
            category: "skill",
            confidence: 0.9,
            source: "confirmed",
            expiresAt: new Date(
              Date.now() + 90 * 24 * 60 * 60 * 1000,
            ).toISOString(),
          })
          .catch(() => {});
      }

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
    // Context Mesh — start ambient signal collectors
    if (this.ctx.contextMesh) {
      this.ctx.contextMesh.start();
      log.engine.info("[feature] Context Mesh started");
    }

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
    this.timerTickInterval = setInterval(() => {
      this.deliverScheduledMessages();
    }, 5_000);
    log.engine.info("[feature] Scheduled message delivery tick started (5s)");

    // Persist new modules on process exit
    const saveOnExit = () => {
      if (this.timerTickInterval) clearInterval(this.timerTickInterval);
      this.ctx.trustChain?.save?.().catch(() => {});
      this.ctx.knowledgeGraph?.save?.().catch(() => {});
      this.ctx.timelineManager?.save?.().catch(() => {});
      this.ctx.patternAnalyzer?.save?.().catch(() => {});
      this.ctx.predictiveQueue?.save?.().catch(() => {});
      this.ctx.skillArena?.save?.().catch(() => {});
      this.ctx.contextMesh?.stop?.();
    };
    process.once("beforeExit", saveOnExit);
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
    const adapter = this.adapters.get(channelId);
    if (!adapter) return;
    const response: GatewayResponse = {
      content: text,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
      preformatted,
    };
    await adapter.sendToUser(userId, response);
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

  // ─── Scheduled Message Delivery ─────────────────────────────

  /**
   * Poll for scheduled messages (from set_timer tool) and deliver them
   * through the last active channel. Runs every 5 seconds.
   */
  private deliverScheduledMessages(): void {
    const ready = getReadyMessages();
    if (ready.length === 0) return;

    for (const msg of ready) {
      const channelId = msg.channelId || this.lastActiveChannel;
      const userId = msg.userId || this.lastActiveUserId;

      if (channelId && userId) {
        this.sendProactive(channelId, userId, msg.message).catch((err) =>
          log.engine.warn(
            `[Timer] Failed to deliver scheduled message "${msg.id}": ${err instanceof Error ? err.message : err}`,
          ),
        );
        log.engine.info(
          `[Timer] Delivered "${msg.id}" to ${channelId}:${userId}`,
        );
      } else {
        // No channel info — broadcast to all
        this.broadcastProactive(msg.message).catch((err) =>
          log.engine.warn(
            `[Timer] Failed to broadcast scheduled message "${msg.id}": ${err instanceof Error ? err.message : err}`,
          ),
        );
        log.engine.info(`[Timer] Broadcast "${msg.id}" to all channels`);
      }
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
    return this.ctx.pelletStore;
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
  getEvolution() {
    return this.ctx.evolution;
  }
  getSkillsLoader() {
    return this.ctx.skillsLoader;
  }
  getLearningEngine() {
    return this.ctx.learningEngine;
  }
  getLearningOrchestrator() {
    return this.ctx.learningOrchestrator;
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
    return this.ctx.sessionStore;
  }
  getEpisodicMemory() {
    return this.ctx.episodicMemory;
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

  // ─── Private: Feature Commands ──────────────────────────────

  private async handleFeatureCommand(
    message: GatewayMessage,
    _callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse | null> {
    const text = message.text.trim();
    const owl = this.ctx.owl;
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });

    // /trust — show trust chain status
    if (text.toLowerCase() === "/trust" && this.ctx.trustChain) {
      return mkResp(this.ctx.trustChain.formatStatus());
    }

    // /timeline — show conversation timeline
    if (text.toLowerCase() === "/timeline" && this.ctx.timelineManager) {
      const timeline = this.ctx.timelineManager.getTimeline(message.sessionId);
      if (!timeline) return mkResp("No timeline data for this session yet.");
      const snapshotList = timeline.snapshots
        .map(
          (s) =>
            `  • [${s.id.slice(0, 8)}] ${s.metadata.snapshotAt} — ${s.messageIndex} messages${s.metadata.description ? ` (${s.metadata.description})` : ""}`,
        )
        .join("\n");
      const forkList =
        timeline.forks.length > 0
          ? "\n\n**Forks:**\n" +
            timeline.forks
              .map(
                (f) =>
                  `  • ${f.createdAt} — forked at message ${f.forkIndex}${f.forkReason ? ` (${f.forkReason})` : ""}`,
              )
              .join("\n")
          : "";
      return mkResp(
        `**Timeline** (${timeline.totalMessages} messages)\n\n**Snapshots:**\n${snapshotList}${forkList}`,
      );
    }

    // /fork [reason] — fork conversation from current point
    if (text.toLowerCase().startsWith("/fork") && this.ctx.timelineManager) {
      const reason = text.slice(5).trim() || undefined;
      const session = await this.getOrCreateSession(message);
      const snapshot = this.ctx.timelineManager.createSnapshot(
        message.sessionId,
        session.messages,
        owl.persona.name,
        "Pre-fork snapshot",
      );
      const newSessionId = `${message.sessionId}:fork:${Date.now()}`;
      const fork = this.ctx.timelineManager.fork(
        snapshot.id,
        newSessionId,
        reason,
      );
      await this.ctx.timelineManager.save();
      return mkResp(
        `🔀 **Conversation forked!**\n\n` +
          `Fork ID: \`${fork.id.slice(0, 8)}\`\n` +
          `Forked at message: ${fork.forkIndex}\n` +
          (reason ? `Reason: ${reason}\n` : "") +
          `New session: \`${newSessionId}\`\n\n` +
          `You can continue here or switch to the fork.`,
      );
    }

    // /collab create <name> — create a collaborative session
    const collabCreate = text.match(/^\/collab\s+create\s+(.+)$/i);
    if (collabCreate && this.ctx.collabManager) {
      try {
        const session = this.ctx.collabManager.createSession(
          collabCreate[1],
          owl.persona.name,
          {
            userId: message.userId,
            displayName: message.userId,
            channelId: message.channelId,
          },
        );
        return mkResp(
          `👥 **Collaborative session created!**\n\n` +
            `Name: **${session.name}**\n` +
            `Session ID: \`${session.id.slice(0, 8)}\`\n` +
            `Others can join with: \`/collab join ${session.id.slice(0, 8)}\``,
        );
      } catch (err) {
        return mkResp(
          `Failed to create session: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /collab list — list active collab sessions
    if (text.toLowerCase() === "/collab list" && this.ctx.collabManager) {
      const sessions = this.ctx.collabManager.listSessions();
      if (sessions.length === 0)
        return mkResp("No active collaborative sessions.");
      const list = sessions
        .map(
          (s) =>
            `  • **${s.name}** (\`${s.id.slice(0, 8)}\`) — ${s.participants.length} participants, ${s.messages.length} messages`,
        )
        .join("\n");
      return mkResp(`**Active Collaborative Sessions:**\n${list}`);
    }

    // /forge start <name> — start recording a demonstration
    const forgeStart = text.match(/^\/forge\s+start\s+(.+)$/i);
    if (forgeStart && this.ctx.demoRecorder) {
      const id = this.ctx.demoRecorder.startRecording(
        forgeStart[1],
        forgeStart[1],
        this.ctx.cwd ?? process.cwd(),
      );
      return mkResp(
        `🔨 **Skill Forge recording started!**\n\n` +
          `Name: **${forgeStart[1]}**\n` +
          `Recording ID: \`${id.slice(0, 8)}\`\n\n` +
          `I'm now watching your actions. When done, use \`/forge stop\` to generate a skill.`,
      );
    }

    // /forge stop — stop recording and generate skill
    if (
      text.toLowerCase() === "/forge stop" &&
      this.ctx.demoRecorder &&
      this.ctx.forgeSynthesizer
    ) {
      // Get the last active recording
      const activeIds = [
        ...((this.ctx.demoRecorder as any).activeRecordings?.keys?.() ?? []),
      ];
      if (activeIds.length === 0) return mkResp("No active recording to stop.");

      const recording = this.ctx.demoRecorder.endRecording(
        activeIds[activeIds.length - 1],
      );
      try {
        const skillMd = await this.ctx.forgeSynthesizer.synthesize(recording);
        const skillDir =
          this.ctx.config.skills?.directories?.[0] || "./workspace/skills";
        const filePath = await this.ctx.forgeSynthesizer.saveSkill(
          skillMd,
          skillDir,
        );

        // Reindex skills after new skill added
        if (this.skillInjector) {
          this.skillInjector.reindex();
        }

        return mkResp(
          `✅ **Skill generated from demonstration!**\n\n` +
            `Steps recorded: ${recording.steps.length}\n` +
            `Skill saved to: \`${filePath}\`\n\n` +
            `The skill is now available for use.`,
        );
      } catch (err) {
        return mkResp(
          `Skill generation failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /swarm — show swarm status
    if (text.toLowerCase() === "/swarm" && this.ctx.swarmCoordinator) {
      const status = this.ctx.swarmCoordinator.getSwarmStatus();
      const nodeList = status.nodes
        .map(
          (n) =>
            `  • **${n.name}** (${n.platform}) — ${n.status}, load: ${(n.currentLoad * 100).toFixed(0)}%, capabilities: ${n.capabilities.join(", ")}`,
        )
        .join("\n");
      return mkResp(
        `**🐝 Swarm Status**\n\n` +
          `Nodes: ${status.nodes.length}\n` +
          `Active tasks: ${status.activeTasks.length}\n` +
          `Total completed: ${status.totalCompleted}\n\n` +
          `**Nodes:**\n${nodeList}`,
      );
    }

    // /tournament <category> — run a skill tournament
    const tournMatch = text.match(/^\/tournament\s+(.+)$/i);
    if (tournMatch && this.ctx.skillArena) {
      const category = tournMatch[1].trim();
      return mkResp(
        `🏆 Tournament for category "${category}" queued.\n` +
          `Use during quiet hours or run manually with the skill arena.`,
      );
    }

    // /voice [on|off] — toggle voice output
    const voiceMatch = text.match(/^\/voice\s*(on|off)?$/i);
    if (voiceMatch && this.ctx.voiceAdapter) {
      const toggle = voiceMatch[1]?.toLowerCase();
      if (toggle === "on") {
        return mkResp(
          "🔊 Voice output enabled. Responses will be spoken aloud.",
        );
      } else if (toggle === "off") {
        return mkResp("🔇 Voice output disabled.");
      }
      const available = this.ctx.voiceAdapter.isAvailable();
      return mkResp(
        `🎤 Voice status: ${available ? "Available" : "Not configured"}`,
      );
    }

    // /knowledge — show knowledge graph stats
    if (text.toLowerCase() === "/knowledge" && this.ctx.knowledgeGraph) {
      const stats = this.ctx.knowledgeGraph.getStats();
      const topNodes = stats.topNodes
        .slice(0, 5)
        .map((n) => `  • **${n.title}** (accessed ${n.accessCount}x)`)
        .join("\n");
      return mkResp(
        `**🧠 Knowledge Graph**\n\n` +
          `Nodes: ${stats.totalNodes}\n` +
          `Edges: ${stats.totalEdges}\n` +
          `Domains: ${stats.domains.join(", ") || "none"}\n` +
          `Avg confidence: ${(stats.avgConfidence * 100).toFixed(0)}%\n\n` +
          `**Most accessed:**\n${topNodes || "  (none yet)"}`,
      );
    }

    // /predict — show predicted tasks
    if (text.toLowerCase() === "/predict" && this.ctx.predictiveQueue) {
      const presentation = this.ctx.predictiveQueue.formatForPresentation();
      return mkResp(
        presentation ||
          "No predictions ready yet. I need more interaction history to identify patterns.",
      );
    }

    // /echo-check — run echo chamber analysis
    if (text.toLowerCase() === "/echo-check" && this.ctx.echoChamberDetector) {
      const analysis = await this.ctx.echoChamberDetector.analyze();
      if (analysis.detections.length === 0) {
        return mkResp(
          `**Echo Chamber Check** (${analysis.sessionCount} sessions)\n\n${analysis.overallAssessment}`,
        );
      }
      const detectionList = analysis.detections
        .map(
          (d) =>
            `  - **${d.bias.replace(/_/g, " ")}** (${(d.confidence * 100).toFixed(0)}%): ${d.evidence}`,
        )
        .join("\n");
      return mkResp(
        `**Echo Chamber Check** (${analysis.sessionCount} sessions)\n\n` +
          `${analysis.overallAssessment}\n\n**Patterns:**\n${detectionList}`,
      );
    }

    // /journal [weekly|monthly] — generate or view growth journal
    const journalMatch = text.match(/^\/journal(?:\s+(weekly|monthly))?$/i);
    if (journalMatch && this.ctx.journalGenerator) {
      const period = (journalMatch[1] as "weekly" | "monthly") || "weekly";
      const entry = await this.ctx.journalGenerator.generate(period);
      return mkResp(entry.narrative);
    }

    // /quests — list active quests
    if (text.toLowerCase() === "/quests" && this.ctx.questManager) {
      const quests = await this.ctx.questManager.list();
      if (quests.length === 0)
        return mkResp("No active quests. Ask me to create one on any topic!");
      const list = quests
        .map((q) => {
          const done = q.milestones.filter((m) => m.completed).length;
          return `  - **${q.title}** [${q.status}] — ${done}/${q.milestones.length} milestones`;
        })
        .join("\n");
      return mkResp(`**Your Quests:**\n${list}`);
    }

    // /capsules — list time capsules
    if (text.toLowerCase() === "/capsules" && this.ctx.capsuleManager) {
      const capsules = await this.ctx.capsuleManager.list();
      if (capsules.length === 0)
        return mkResp("No time capsules. Ask me to create one!");
      const list = capsules
        .map((c) => {
          const icon = c.status === "sealed" ? "\uD83D\uDD12" : "\uD83D\uDCEC";
          return `  ${icon} **${c.id}** [${c.status}] — created ${new Date(c.createdAt).toLocaleDateString()}`;
        })
        .join("\n");
      return mkResp(`**Time Capsules:**\n${list}`);
    }

    // /constellations — show discovered patterns
    if (
      text.toLowerCase() === "/constellations" &&
      this.ctx.constellationMiner
    ) {
      const constellations = await this.ctx.constellationMiner.list();
      if (constellations.length === 0)
        return mkResp(
          "No constellations discovered yet. I need more pellets to find patterns.",
        );
      const list = constellations
        .slice(0, 5)
        .map((c) => this.ctx.constellationMiner!.format(c))
        .join("\n\n---\n\n");
      return mkResp(`**Discovered Constellations:**\n\n${list}`);
    }

    // /socratic [mode|off] — toggle Socratic mode
    const socraticMatch = text.match(
      /^\/socratic(?:\s+(pure|guided|reflective|devils_advocate|off))?$/i,
    );
    if (socraticMatch && this.ctx.socraticEngine) {
      const mode = socraticMatch[1]?.toLowerCase();
      if (mode === "off") {
        const ended = this.ctx.socraticEngine.deactivate(message.sessionId);
        if (ended) {
          return mkResp(
            `Socratic mode **deactivated** after ${ended.exchangeCount} exchanges.`,
          );
        }
        return mkResp("Socratic mode was not active.");
      }
      const subMode = (mode as any) || "guided";
      this.ctx.socraticEngine.activate(message.sessionId, subMode);
      return mkResp(
        `Socratic mode **activated** (${subMode}).\n\n` +
          `I will now respond primarily with questions to help you think deeper.\n` +
          `Use \`/socratic off\` to return to normal mode.`,
      );
    }

    // /council [topic1, topic2, ...] — convene a Knowledge Council
    if (
      text.toLowerCase().startsWith("/council") &&
      this.ctx.knowledgeCouncil
    ) {
      const topicsArg = text.slice(8).trim();
      const topics = topicsArg
        ? topicsArg
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean)
        : undefined;

      try {
        const session = await this.ctx.knowledgeCouncil.convene(
          topics,
          _callbacks.onProgress,
        );
        return mkResp(session.summary ?? "Knowledge Council session complete.");
      } catch (err) {
        return mkResp(
          `Failed to convene Knowledge Council: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /council-history — show past council sessions
    if (
      text.toLowerCase() === "/council-history" &&
      this.ctx.knowledgeCouncil
    ) {
      return mkResp(this.ctx.knowledgeCouncil.getHistorySummary());
    }

    // ── Agent Watch commands ──────────────────────────────────
    const mkHtml = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
      preformatted: true,
    });

    // "watch my claude code" / "watch my opencode [port N]" / "watch" → register
    if (/^(\/watch|watch(\s+(my\s+)?(claude[\s-]*(code)?|opencode|agent|coding\s+agent))?)(\s+port\s+\d+)?$/i.test(text)) {
      if (!this.agentWatch) {
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
      return mkHtml(reg.telegramMessage);
    }

    // "unwatch" / "/unwatch" → stop watching all sessions for this user
    if (/^\/?(unwatch|stop watching|stop watch)$/i.test(text)) {
      if (!this.agentWatch) return mkResp("Agent Watch is not enabled.");
      const count = await this.agentWatch.unwatchUser(message.userId);
      return mkResp(
        count > 0
          ? `👁 Stopped watching ${count} session(s).`
          : "No active watch sessions for you.",
      );
    }

    // "watch status" / "/watch status"
    if (/^\/?(watch\s+status|agent\s+status)$/i.test(text)) {
      if (!this.agentWatch) return mkResp("Agent Watch is not enabled.");
      const st = this.agentWatch.getStatus();
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

  // ─── Private: Auto-Parliament ────────────────────────────────

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
    message: GatewayMessage,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse | null> {
    if (!this.ctx.learningEngine && !this.ctx.learningOrchestrator) return null;

    const text = message.text.trim();

    // /learn <topic> — explicit command
    const slashLearn = text.match(/^\/learn\s+(.+)$/i);
    if (slashLearn) {
      return this.executeLearnRequest(slashLearn[1].trim(), callbacks);
    }

    // Natural language patterns:
    //   "can you learn how to X", "learn how to X", "learn to X",
    //   "study X", "research X for me", "go learn about X",
    //   "teach yourself X", "figure out how to X"
    const nlPatterns = [
      /^(?:can you |please |go )?\s*learn\s+(?:how\s+)?(?:to\s+)?(.+?)[\s?.!]*$/i,
      /^(?:can you |please )?\s*study\s+(.+?)[\s?.!]*$/i,
      /^(?:can you |please )?\s*research\s+(.+?)(?:\s+for me)?[\s?.!]*$/i,
      /^(?:can you |please )?\s*teach yourself\s+(.+?)[\s?.!]*$/i,
      /^(?:can you |please )?\s*figure out\s+(?:how to\s+)?(.+?)[\s?.!]*$/i,
    ];

    for (const pattern of nlPatterns) {
      const match = text.match(pattern);
      if (match && match[1].length > 3) {
        return this.executeLearnRequest(match[1].trim(), callbacks);
      }
    }

    return null;
  }

  private async executeLearnRequest(
    topic: string,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse> {
    const owl = this.ctx.owl;

    await callbacks.onProgress?.(`🧠 Starting self-study on: **${topic}**`);
    await callbacks.onProgress?.(
      `📚 Researching and creating knowledge pellets...`,
    );

    try {
      // Use new LearningOrchestrator if available (TopicFusion + multi-pipeline synthesis)
      if (this.ctx.learningOrchestrator) {
        const cycle = await this.ctx.learningOrchestrator.learnTopic(
          topic,
          true,
        );

        if (
          !cycle.success ||
          (cycle.synthesisReport?.pelletsCreated ?? 0) === 0
        ) {
          return {
            content:
              `${owl.persona.emoji} I wasn't able to produce any useful knowledge about **${topic}**. ` +
              `This might be because the topic is too broad or my research didn't yield actionable results.\n\n` +
              `Try being more specific — e.g. instead of "emails", try "sending emails via SMTP in Node.js".`,
            owlName: owl.persona.name,
            owlEmoji: owl.persona.emoji,
            toolsUsed: [],
          };
        }

        const report = cycle.synthesisReport!;
        const pipelineSummary = Object.entries(report.byPipeline)
          .map(([p, n]) => `${p}: ${n}`)
          .join(", ");

        await callbacks.onProgress?.(`✅ Self-study complete!`);

        return {
          content:
            `${owl.persona.emoji} I've studied **${topic}** and created ${report.pelletsCreated} knowledge pellet(s):\n\n` +
            `  - Pipelines used: ${pipelineSummary}\n` +
            `  - Duration: ${Math.round(report.durationMs / 1000)}s\n\n` +
            `This knowledge is now saved and will be automatically used in future conversations about this topic.`,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          toolsUsed: [],
        };
      }

      // Fallback to legacy KnowledgeResearcher
      const { KnowledgeResearcher } = await import("../learning/researcher.js");
      const { KnowledgeGraphManager } =
        await import("../learning/knowledge-graph.js");

      const graphManager = new KnowledgeGraphManager(
        this.ctx.cwd ?? process.cwd(),
      );
      await graphManager.load();

      const researcher = new KnowledgeResearcher(
        this.ctx.provider,
        owl,
        this.ctx.config,
        this.ctx.pelletStore!,
        graphManager,
      );

      const result = await researcher.research(topic);
      await graphManager.save();

      if (result.pellets.length === 0) {
        return {
          content:
            `${owl.persona.emoji} I wasn't able to produce any useful knowledge about **${topic}**. ` +
            `This might be because the topic is too broad or my research didn't yield actionable results.\n\n` +
            `Try being more specific — e.g. instead of "emails", try "sending emails via SMTP in Node.js".`,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          toolsUsed: [],
        };
      }

      const pelletSummary = result.pellets
        .map((p) => `  - **${p.title}**`)
        .join("\n");

      const relatedSummary =
        result.relatedTopics.length > 0
          ? `\n\n**Related topics discovered:** ${result.relatedTopics.join(", ")}`
          : "";

      await callbacks.onProgress?.(`✅ Self-study complete!`);

      return {
        content:
          `${owl.persona.emoji} I've studied **${topic}** and created ${result.pellets.length} knowledge pellet(s):\n\n` +
          `${pelletSummary}${relatedSummary}\n\n` +
          `This knowledge is now saved and will be automatically used in future conversations about this topic.`,
        owlName: owl.persona.name,
        owlEmoji: owl.persona.emoji,
        toolsUsed: [],
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.evolution.error(`Learn request failed: ${msg}`);
      return {
        content:
          `${owl.persona.emoji} I tried to study **${topic}** but ran into an issue: ${msg}\n\n` +
          `I'll add it to my study queue for later.`,
        owlName: owl.persona.name,
        owlEmoji: owl.persona.emoji,
        toolsUsed: [],
      };
    }
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
        } catch {
          // Non-fatal — skill will be picked up on next restart
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

      // Register pellet ID so the next user message can be absorbed as feedback
      if (gapLearning?.pelletId) {
        this.postProcessor.setLastGapPelletId(gapLearning.pelletId);
      }

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

  // ─── Private: Session ────────────────────────────────────────

  private async getOrCreateSession(message: GatewayMessage): Promise<Session> {
    const key = message.sessionId;
    const cached = this.sessions.get(key);

    if (cached && Date.now() - cached.lastActivity <= SESSION_TIMEOUT_MS) {
      cached.lastActivity = Date.now();
      return cached.session;
    }

    // Load from disk or create fresh
    let session = await this.ctx.sessionStore.loadSession(key);
    if (!session) {
      session = this.ctx.sessionStore.createSession(this.ctx.owl.persona.name);
      session.id = key;
      await this.ctx.sessionStore.saveSession(session);
    }

    this.sessions.set(key, { session, lastActivity: Date.now() });
    return session;
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
      if (!userAlreadySaved) {
        session.messages.push({ role: "user", content: userText });
      }
      for (const msg of newMessages) {
        session.messages.push(msg);
      }
      if (finalContent?.trim()) {
        session.messages.push({ role: "assistant", content: finalContent });
      }
      if (session.messages.length > MAX_SESSION_HISTORY) {
        session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
      }
      await this.ctx.sessionStore.saveSession(session);
      const key = session.id;
      const cached = this.sessions.get(key);
      if (cached) cached.lastActivity = Date.now();
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
   * Remove sessions that haven't been active within SESSION_TIMEOUT_MS.
   * Also prunes their attempt logs so we don't accumulate memory for dead sessions.
   */
  private evictStaleSessions(): void {
    const now = Date.now();
    const activeIds = new Set<string>();
    for (const [key, cache] of this.sessions) {
      if (now - cache.lastActivity > SESSION_TIMEOUT_MS) {
        // Option B: fire endSession before evicting so episodic memory extraction,
        // learning pipeline, and DNA evolution all run for sessions that end without
        // an explicit /quit (e.g. Telegram users who just go silent).
        // endSession() captures session.messages synchronously before its first await,
        // so it's safe to delete from the map immediately after — the messages reference
        // is already captured by the time the async work begins.
        if (cache.session.messages.length >= 2) {
          this.endSession(key).catch((err) => {
            log.engine.warn(
              `[session-evict] endSession failed for "${key}": ${err instanceof Error ? err.message : err}`,
            );
          });
        }
        this.sessions.delete(key);
        this.stuckStreak.delete(key);
        this.attemptLogs.delete(key);
        log.engine.info(
          `[session-evict] Evicted stale session "${key}" (endSession triggered)`,
        );
      } else {
        activeIds.add(key);
      }
    }
    this.attemptLogs.pruneStale(activeIds);

    // Evict pending feedback older than 24 hours
    const FEEDBACK_TTL = 24 * 60 * 60 * 1000;
    for (const [id, fb] of this.pendingFeedback) {
      if (now - fb.createdAt > FEEDBACK_TTL) this.pendingFeedback.delete(id);
    }
  }

  // ─── Private: File Delivery ──────────────────────────────────

  /**
   * Deliver any files queued in pendingFiles by the send_file tool during a run.
   * Uses the adapter's deliverFile method if available — gracefully skips if not.
   */
  private async deliverPendingFiles(
    files: import("../engine/runtime.js").PendingFile[],
    channelId: string,
    userId: string,
  ): Promise<void> {
    if (!files.length) return;
    const adapter = this.adapters.get(channelId);
    if (!adapter?.deliverFile) return;
    for (const file of files) {
      try {
        await adapter.deliverFile(userId, file.path, file.caption);
      } catch (err) {
        log.engine.warn(
          `[${channelId}] File delivery to ${userId} failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
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
        } catch {
          // Non-fatal
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
        this.ctx.innerLife.thinkInBackground(
          typeof lastUserMsg.content === "string" ? lastUserMsg.content : "",
          messages,
        );
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
