/**
 * StackOwl — Owl Gateway (Core)
 *
 * The single point of entry for all incoming messages.
 * All business logic lives here:
 *   - Session management
 *   - Instinct evaluation
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
import { AttemptLogRegistry } from "../memory/attempt-log.js";
import { SkillContextInjector } from "../skills/injector.js";
import { ClawHubClient } from "../skills/clawhub.js";
import { SkillTracker } from "../skills/tracker.js";
import { log } from "../logger.js";
import { MemoryConsolidator } from "../memory/consolidator.js";
import { MemoryReflexionEngine } from "../memory/reflexion.js";
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
import { TaskQueue } from "../queue/task-queue.js";

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
  /** Timer tick interval for scheduled message delivery */
  private timerTickInterval: NodeJS.Timeout | null = null;

  // ─── Extracted Handlers (Improvement #4) ───────────────────
  private postProcessor: PostProcessor;
  private contextBuilder: ContextBuilder;
  /** Extracted session manager — used by new code paths, old inline code migrating incrementally */
  sessionManager: SessionManager;
  private taskQueue: TaskQueue;

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
  private static readonly STUCK_THRESHOLD = 2;

  /**
   * Cross-turn attempt logs — one per active session.
   * Persists across handle() calls so the model always knows what was
   * already tried in previous messages of this conversation.
   */
  private attemptLogs = new AttemptLogRegistry();

  constructor(public ctx: GatewayContext) {
    this.engine = new OwlEngine();

    // Initialize task queue (Improvement #2)
    this.taskQueue = ctx.taskQueue ?? new TaskQueue(ctx.config.queue);

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
    process.once('exit', saveDNAOnExit);
    process.once('SIGINT', () => { saveDNAOnExit(); process.exit(0); });
    process.once('SIGTERM', () => { saveDNAOnExit(); process.exit(0); });

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

    // Initialize extracted handlers (Improvement #4)
    // ContextBuilder is initialized after skillInjector below
    this.postProcessor = new PostProcessor(
      ctx,
      this.taskQueue,
      ctx.eventBus ?? null,
      this.microLearner,
      this.anticipator,
      ctx.costTracker ?? null,
    );
    this.contextBuilder = new ContextBuilder(ctx, this.microLearner, null);

    // Built-in middleware
    this.middleware.push(new LoggingMiddleware());
    if (ctx.config.gateway?.rateLimit) {
      this.middleware.push(new RateLimitMiddleware(ctx.config.gateway.rateLimit));
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
      const synthesisProviderName = ctx.config.synthesis?.provider ?? 'anthropic';
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

    // Initialize new feature modules (all optional, fire-and-forget load)
    this.initFeatureModules();
  }

  // ─── Adapter Registry ────────────────────────────────────────

  register(adapter: ChannelAdapter): void {
    this.adapters.set(adapter.id, adapter);
    log.engine.info(`Channel registered: ${adapter.name} [${adapter.id}]`);
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
    const laneKey = message.sessionId;
    const prev = this.lanes.get(laneKey) ?? Promise.resolve();
    const next = prev.then(async () => {
      const response = await this.handleInLane(message, callbacks);
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
          `⚠️ **I've been stuck on this task for ${streak} attempts and haven't been able to make progress.**\n\n` +
          `Here's what I tried:\n${cleanContent}\n\n` +
          `To move forward, I need you to choose one of these options:\n\n` +
          `**A) Provide more information or clarify** — if there's something I'm missing or misunderstanding, tell me and I'll try again.\n\n` +
          `**B) Try a completely different approach** — describe what you'd like me to do differently, and I'll start fresh.\n\n` +
          `**C) Accept that this can't be done right now** — I'll note the limitation and you can revisit it later.\n\n` +
          `_Reply with A, B, or C (or just tell me what you'd like to do)._`;

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
  }

  private async handleCore(
    message: GatewayMessage,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse> {
    const session = await this.getOrCreateSession(message);

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
          const emoji = skill.metadata.openclaw?.emoji || '⚡';
          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `${emoji} **Executing skill:** \`${skill.name}\``,
            );
          }
          const result = await this.skillInjector.executeStructuredSkill(
            skill, skillArgs || skill.description, callbacks.onProgress,
          );
          await this.saveSession(session, message.text, [], false, result.finalOutput);
          this.postProcess(session.messages, session.id);
          return {
            content: result.finalOutput,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: result.stepResults
              .filter(s => s.status === 'success')
              .map(s => s.stepId),
          };
        }

        // Unstructured skills — inject as prompt directive (existing path)
        const skillDirective =
          `[SKILL INVOKED: ${skill.name}]\n` +
          `The user has explicitly requested this skill. Follow its instructions exactly.\n\n` +
          `<skill name="${skill.name}">\n${skill.instructions}\n</skill>\n\n` +
          (skillArgs ? `User arguments: ${skillArgs}` : "");
        const engineCtx = this.buildEngineContext(
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
        await this.saveSession(session, message.text, response.newMessages, false, response.content);
        this.postProcess(session.messages, session.id);
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

    // Check for topic switch (heuristic, no LLM call)
    const freshStartDirective = this.detectTopicSwitch(
      message.text,
      session.messages,
    );
    // Flush both in-memory and on-disk session state atomically
    if (freshStartDirective) {
      session.messages = [];
      this.attemptLogs.delete(message.sessionId);
      await this.ctx.sessionStore.saveSession(session);
    }

    // Evaluate instincts — may inject behavioral constraints
    let text = message.text;
    if (this.ctx.instinctEngine && this.ctx.instinctRegistry) {
      const instincts = this.ctx.instinctRegistry.getContextInstincts(
        this.ctx.owl.persona.name,
      );
      const triggered = await this.ctx.instinctEngine.evaluate(
        text,
        instincts,
        {
          provider: this.ctx.provider,
          owl: this.ctx.owl,
          config: this.ctx.config,
        },
      );
      if (triggered) {
        log.engine.info(`Instinct triggered: ${triggered.name}`);
        text = `User Input: ${text}\n\n[SYSTEM OVERRIDE - INSTINCT TRIGGERED]\n${triggered.actionPrompt}`;
      }
    }

    // Track last active channel/user for scheduled message delivery
    this.lastActiveChannel = message.channelId;
    this.lastActiveUserId = message.userId;

    log.engine.incoming(message.channelId, message.text);

    // Dynamic skill injection — uses BM25 + usage-weighted semantic routing
    let dynamicSkillsContext = "";
    if (this.skillInjector) {
      const relevantSkills = await this.skillInjector.getRelevantSkills(text);
      if (relevantSkills.length > 0) {
        // Check if top skill is structured — execute directly instead of prompt injection
        const topSkill = relevantSkills[0];
        if (this.skillInjector.canExecuteStructured(topSkill)) {
          log.engine.info(`Structured skill execution: ${topSkill.name}`);
          const emoji = topSkill.metadata.openclaw?.emoji || '⚡';
          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `${emoji} **Executing skill:** \`${topSkill.name}\` — ${topSkill.description}`,
            );
          }

          const result = await this.skillInjector.executeStructuredSkill(
            topSkill, message.text, callbacks.onProgress,
          );

          // Save to session and return directly — no ReAct loop needed
          await this.saveSession(session, message.text, [], false, result.finalOutput);
          this.postProcess(session.messages, session.id);

          return {
            content: result.finalOutput,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: result.stepResults
              .filter(s => s.status === 'success')
              .map(s => s.stepId),
          };
        }

        // Unstructured skills — inject as context XML (existing path)
        dynamicSkillsContext = await this.skillInjector.injectIntoContext(text);
        const skillNames = relevantSkills.map((s) => s.name);
        log.engine.info(`Dynamic skill injection: ${skillNames.join(", ")}`);

        // Notify user about skill usage (like tool history)
        if (callbacks.onProgress) {
          for (const s of relevantSkills) {
            const emoji = s.metadata.openclaw?.emoji || '📋';
            await callbacks.onProgress(
              `${emoji} **Using skill:** \`${s.name}\` — ${s.description}`,
            );
          }
        }
      } else {
        log.engine.info(`[Skills] No skills matched for: "${text.slice(0, 60)}"`);
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

    // ─── Strategy Classification & Orchestrated Execution ─────
    // Single LLM call replaces both parliament detection and planner routing
    const strategy = await classifyStrategy(
      message.text,
      this.ctx.owlRegistry.listOwls(),
      this.ctx.toolRegistry?.getDefinitions().map(t => t.name) ?? [],
      session.messages.slice(-6),
      this.ctx.provider,
    );

    if (callbacks.onProgress && !['DIRECT', 'STANDARD'].includes(strategy.strategy)) {
      await callbacks.onProgress(
        `🎯 **Strategy: ${strategy.strategy}** — ${strategy.reasoning}`,
      );
    }

    const engineCtx = this.buildEngineContext(
      session,
      callbacks,
      dynamicSkillsContext,
      isIsolatedTask,
      this.attemptLogs.get(message.sessionId),
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
      modelUsed: '',
      newMessages: [],
      usage: orchResult.usage,
    };

    // Capability gap detected — try to synthesize the missing tool and retry
    if (response.pendingCapabilityGap && this.ctx.evolution) {
      return await this.handleCapabilityGap(
        message,
        response,
        session,
        engineCtx,
        callbacks,
      );
    }

    await this.saveSession(session, message.text, response.newMessages, false, response.content);
    this.postProcess(session.messages, session.id);

    // Detect and persist preferences expressed in this message (fire-and-forget)
    this.detectPreferences(message.text, message.channelId);

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

    // Memory consolidation
    try {
      const consolidator = new MemoryConsolidator(
        this.ctx.provider,
        this.ctx.owl,
        this.ctx.cwd ?? process.cwd(),
      );
      await consolidator.extractAndAppend(messages);
      log.engine.info("Memory consolidated.");
    } catch (err) {
      log.engine.warn(
        `Memory consolidation failed: ${err instanceof Error ? err.message : err}`,
      );
    }

    // Reactive learning (new orchestrator if available, fallback to legacy)
    if (this.ctx.learningOrchestrator) {
      try {
        const cycle = await this.ctx.learningOrchestrator.processConversation(messages);
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

    // Reflexion-based memory consolidation (structured JSON, replaces append-only memory.md)
    if (this.ctx.learningOrchestrator) {
      try {
        const reflexion = new MemoryReflexionEngine(
          this.ctx.cwd ?? process.cwd(),
          this.ctx.provider,
          this.ctx.owl,
        );
        await reflexion.consolidate(messages, sessionId);
        log.engine.info("[endSession:reflexion] ✓ memory consolidated");
      } catch (err) {
        log.engine.warn(
          `[endSession:reflexion] ✗ failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

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
        sessionId, messages, this.ctx.owl.persona.name, 'Session end snapshot',
      );
      await this.ctx.timelineManager.save().catch(() => {});
    }

    // Knowledge extraction — harvest knowledge from full session
    if (this.ctx.knowledgeReasoner && messages.length > 4) {
      try {
        await this.ctx.knowledgeReasoner.extractFromConversation(messages);
        await this.ctx.knowledgeGraph?.save();
        log.engine.info('[endSession:knowledge] ✓ extracted');
      } catch (err) {
        log.engine.warn(`[endSession:knowledge] ✗ failed: ${err instanceof Error ? err.message : err}`);
      }
    }

    // Pattern analysis — analyze full session for behavioral patterns
    if (this.ctx.patternAnalyzer) {
      try {
        const sessions = await this.ctx.sessionStore.listSessions();
        if (sessions.length > 0) {
          await this.ctx.patternAnalyzer.analyzeHistory(sessions as any[]);
          await this.ctx.patternAnalyzer.save();
          log.engine.info('[endSession:patterns] ✓ analyzed');
        }
      } catch (err) {
        log.engine.warn(`[endSession:patterns] ✗ failed: ${err instanceof Error ? err.message : err}`);
      }
    }

    // Save micro-learner profile on session end
    if (this.microLearner) {
      await this.microLearner.save().catch((err) => {
        log.engine.warn(`[endSession] Micro-learner save failed: ${err instanceof Error ? err.message : err}`);
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

  // ─── Feature Module Initialization ──────────────────────────

  private initFeatureModules(): void {
    // Context Mesh — start ambient signal collectors
    if (this.ctx.contextMesh) {
      this.ctx.contextMesh.start();
      log.engine.info('[feature] Context Mesh started');
    }

    // Trust Chain — load trust scores from disk
    if (this.ctx.trustChain) {
      this.ctx.trustChain.load().catch(err =>
        log.engine.warn(`[feature] Trust Chain load failed: ${err}`),
      );
      log.engine.info('[feature] Trust Chain initialized');
    }

    // Knowledge Graph — load graph from disk
    if (this.ctx.knowledgeGraph) {
      this.ctx.knowledgeGraph.load().catch(err =>
        log.engine.warn(`[feature] Knowledge Graph load failed: ${err}`),
      );
      log.engine.info('[feature] Knowledge Graph initialized');
    }

    // Timeline Manager — load snapshots
    if (this.ctx.timelineManager) {
      this.ctx.timelineManager.load().catch(err =>
        log.engine.warn(`[feature] Timeline load failed: ${err}`),
      );
      log.engine.info('[feature] Timeline Manager initialized');
    }

    // Collab Sessions — load persisted sessions
    if (this.ctx.collabManager) {
      this.ctx.collabManager.loadAll();
      log.engine.info('[feature] Collab Session Manager initialized');
    }

    // Pattern Analyzer — load patterns
    if (this.ctx.patternAnalyzer) {
      this.ctx.patternAnalyzer.load().catch(err =>
        log.engine.warn(`[feature] Pattern Analyzer load failed: ${err}`),
      );
      log.engine.info('[feature] Pattern Analyzer initialized');
    }

    // Predictive Queue — load queue
    if (this.ctx.predictiveQueue) {
      this.ctx.predictiveQueue.load().catch(err =>
        log.engine.warn(`[feature] Predictive Queue load failed: ${err}`),
      );
      log.engine.info('[feature] Predictive Queue initialized');
    }

    // Skill Arena — load tournament data
    if (this.ctx.skillArena) {
      this.ctx.skillArena.load().catch(err =>
        log.engine.warn(`[feature] Skill Arena load failed: ${err}`),
      );
      log.engine.info('[feature] Skill Arena initialized');
    }

    // Scheduled message delivery tick — polls every 5 seconds for due timers
    this.timerTickInterval = setInterval(() => {
      this.deliverScheduledMessages();
    }, 5_000);
    log.engine.info('[feature] Scheduled message delivery tick started (5s)');

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
    process.once('beforeExit', saveOnExit);
  }

  // ─── Proactive Messaging ─────────────────────────────────────

  /**
   * Send a proactive message to a specific user on a specific channel.
   */
  async sendProactive(
    channelId: string,
    userId: string,
    text: string,
  ): Promise<void> {
    const adapter = this.adapters.get(channelId);
    if (!adapter) return;
    const response: GatewayResponse = {
      content: text,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
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
        this.sendProactive(channelId, userId, msg.message).catch(err =>
          log.engine.warn(
            `[Timer] Failed to deliver scheduled message "${msg.id}": ${err instanceof Error ? err.message : err}`,
          ),
        );
        log.engine.info(`[Timer] Delivered "${msg.id}" to ${channelId}:${userId}`);
      } else {
        // No channel info — broadcast to all
        this.broadcastProactive(msg.message).catch(err =>
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
    if (text.toLowerCase() === '/trust' && this.ctx.trustChain) {
      return mkResp(this.ctx.trustChain.formatStatus());
    }

    // /timeline — show conversation timeline
    if (text.toLowerCase() === '/timeline' && this.ctx.timelineManager) {
      const timeline = this.ctx.timelineManager.getTimeline(message.sessionId);
      if (!timeline) return mkResp('No timeline data for this session yet.');
      const snapshotList = timeline.snapshots.map(s =>
        `  • [${s.id.slice(0, 8)}] ${s.metadata.snapshotAt} — ${s.messageIndex} messages${s.metadata.description ? ` (${s.metadata.description})` : ''}`,
      ).join('\n');
      const forkList = timeline.forks.length > 0
        ? '\n\n**Forks:**\n' + timeline.forks.map(f =>
            `  • ${f.createdAt} — forked at message ${f.forkIndex}${f.forkReason ? ` (${f.forkReason})` : ''}`,
          ).join('\n')
        : '';
      return mkResp(`**Timeline** (${timeline.totalMessages} messages)\n\n**Snapshots:**\n${snapshotList}${forkList}`);
    }

    // /fork [reason] — fork conversation from current point
    if (text.toLowerCase().startsWith('/fork') && this.ctx.timelineManager) {
      const reason = text.slice(5).trim() || undefined;
      const session = await this.getOrCreateSession(message);
      const snapshot = this.ctx.timelineManager.createSnapshot(
        message.sessionId, session.messages, owl.persona.name, 'Pre-fork snapshot',
      );
      const newSessionId = `${message.sessionId}:fork:${Date.now()}`;
      const fork = this.ctx.timelineManager.fork(snapshot.id, newSessionId, reason);
      await this.ctx.timelineManager.save();
      return mkResp(
        `🔀 **Conversation forked!**\n\n` +
        `Fork ID: \`${fork.id.slice(0, 8)}\`\n` +
        `Forked at message: ${fork.forkIndex}\n` +
        (reason ? `Reason: ${reason}\n` : '') +
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
          { userId: message.userId, displayName: message.userId, channelId: message.channelId },
        );
        return mkResp(
          `👥 **Collaborative session created!**\n\n` +
          `Name: **${session.name}**\n` +
          `Session ID: \`${session.id.slice(0, 8)}\`\n` +
          `Others can join with: \`/collab join ${session.id.slice(0, 8)}\``,
        );
      } catch (err) {
        return mkResp(`Failed to create session: ${err instanceof Error ? err.message : err}`);
      }
    }

    // /collab list — list active collab sessions
    if (text.toLowerCase() === '/collab list' && this.ctx.collabManager) {
      const sessions = this.ctx.collabManager.listSessions();
      if (sessions.length === 0) return mkResp('No active collaborative sessions.');
      const list = sessions.map(s =>
        `  • **${s.name}** (\`${s.id.slice(0, 8)}\`) — ${s.participants.length} participants, ${s.messages.length} messages`,
      ).join('\n');
      return mkResp(`**Active Collaborative Sessions:**\n${list}`);
    }

    // /forge start <name> — start recording a demonstration
    const forgeStart = text.match(/^\/forge\s+start\s+(.+)$/i);
    if (forgeStart && this.ctx.demoRecorder) {
      const id = this.ctx.demoRecorder.startRecording(
        forgeStart[1], forgeStart[1], this.ctx.cwd ?? process.cwd(),
      );
      return mkResp(
        `🔨 **Skill Forge recording started!**\n\n` +
        `Name: **${forgeStart[1]}**\n` +
        `Recording ID: \`${id.slice(0, 8)}\`\n\n` +
        `I'm now watching your actions. When done, use \`/forge stop\` to generate a skill.`,
      );
    }

    // /forge stop — stop recording and generate skill
    if (text.toLowerCase() === '/forge stop' && this.ctx.demoRecorder && this.ctx.forgeSynthesizer) {
      // Get the last active recording
      const activeIds = [...((this.ctx.demoRecorder as any).activeRecordings?.keys?.() ?? [])];
      if (activeIds.length === 0) return mkResp('No active recording to stop.');

      const recording = this.ctx.demoRecorder.endRecording(activeIds[activeIds.length - 1]);
      try {
        const skillMd = await this.ctx.forgeSynthesizer.synthesize(recording);
        const skillDir = this.ctx.config.skills?.directories?.[0] || './workspace/skills';
        const filePath = await this.ctx.forgeSynthesizer.saveSkill(skillMd, skillDir);

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
        return mkResp(`Skill generation failed: ${err instanceof Error ? err.message : err}`);
      }
    }

    // /swarm — show swarm status
    if (text.toLowerCase() === '/swarm' && this.ctx.swarmCoordinator) {
      const status = this.ctx.swarmCoordinator.getSwarmStatus();
      const nodeList = status.nodes.map(n =>
        `  • **${n.name}** (${n.platform}) — ${n.status}, load: ${(n.currentLoad * 100).toFixed(0)}%, capabilities: ${n.capabilities.join(', ')}`,
      ).join('\n');
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
      if (toggle === 'on') {
        return mkResp('🔊 Voice output enabled. Responses will be spoken aloud.');
      } else if (toggle === 'off') {
        return mkResp('🔇 Voice output disabled.');
      }
      const available = this.ctx.voiceAdapter.isAvailable();
      return mkResp(`🎤 Voice status: ${available ? 'Available' : 'Not configured'}`);
    }

    // /knowledge — show knowledge graph stats
    if (text.toLowerCase() === '/knowledge' && this.ctx.knowledgeGraph) {
      const stats = this.ctx.knowledgeGraph.getStats();
      const topNodes = stats.topNodes.slice(0, 5).map(n =>
        `  • **${n.title}** (accessed ${n.accessCount}x)`,
      ).join('\n');
      return mkResp(
        `**🧠 Knowledge Graph**\n\n` +
        `Nodes: ${stats.totalNodes}\n` +
        `Edges: ${stats.totalEdges}\n` +
        `Domains: ${stats.domains.join(', ') || 'none'}\n` +
        `Avg confidence: ${(stats.avgConfidence * 100).toFixed(0)}%\n\n` +
        `**Most accessed:**\n${topNodes || '  (none yet)'}`,
      );
    }

    // /predict — show predicted tasks
    if (text.toLowerCase() === '/predict' && this.ctx.predictiveQueue) {
      const presentation = this.ctx.predictiveQueue.formatForPresentation();
      return mkResp(presentation || 'No predictions ready yet. I need more interaction history to identify patterns.');
    }

    // /echo-check — run echo chamber analysis
    if (text.toLowerCase() === '/echo-check' && this.ctx.echoChamberDetector) {
      const analysis = await this.ctx.echoChamberDetector.analyze();
      if (analysis.detections.length === 0) {
        return mkResp(`**Echo Chamber Check** (${analysis.sessionCount} sessions)\n\n${analysis.overallAssessment}`);
      }
      const detectionList = analysis.detections.map(d =>
        `  - **${d.bias.replace(/_/g, ' ')}** (${(d.confidence * 100).toFixed(0)}%): ${d.evidence}`,
      ).join('\n');
      return mkResp(
        `**Echo Chamber Check** (${analysis.sessionCount} sessions)\n\n` +
        `${analysis.overallAssessment}\n\n**Patterns:**\n${detectionList}`,
      );
    }

    // /journal [weekly|monthly] — generate or view growth journal
    const journalMatch = text.match(/^\/journal(?:\s+(weekly|monthly))?$/i);
    if (journalMatch && this.ctx.journalGenerator) {
      const period = (journalMatch[1] as 'weekly' | 'monthly') || 'weekly';
      const entry = await this.ctx.journalGenerator.generate(period);
      return mkResp(entry.narrative);
    }

    // /quests — list active quests
    if (text.toLowerCase() === '/quests' && this.ctx.questManager) {
      const quests = await this.ctx.questManager.list();
      if (quests.length === 0) return mkResp('No active quests. Ask me to create one on any topic!');
      const list = quests.map(q => {
        const done = q.milestones.filter(m => m.completed).length;
        return `  - **${q.title}** [${q.status}] — ${done}/${q.milestones.length} milestones`;
      }).join('\n');
      return mkResp(`**Your Quests:**\n${list}`);
    }

    // /capsules — list time capsules
    if (text.toLowerCase() === '/capsules' && this.ctx.capsuleManager) {
      const capsules = await this.ctx.capsuleManager.list();
      if (capsules.length === 0) return mkResp('No time capsules. Ask me to create one!');
      const list = capsules.map(c => {
        const icon = c.status === 'sealed' ? '\uD83D\uDD12' : '\uD83D\uDCEC';
        return `  ${icon} **${c.id}** [${c.status}] — created ${new Date(c.createdAt).toLocaleDateString()}`;
      }).join('\n');
      return mkResp(`**Time Capsules:**\n${list}`);
    }

    // /constellations — show discovered patterns
    if (text.toLowerCase() === '/constellations' && this.ctx.constellationMiner) {
      const constellations = await this.ctx.constellationMiner.list();
      if (constellations.length === 0) return mkResp('No constellations discovered yet. I need more pellets to find patterns.');
      const list = constellations.slice(0, 5).map(c =>
        this.ctx.constellationMiner!.format(c),
      ).join('\n\n---\n\n');
      return mkResp(`**Discovered Constellations:**\n\n${list}`);
    }

    // /socratic [mode|off] — toggle Socratic mode
    const socraticMatch = text.match(/^\/socratic(?:\s+(pure|guided|reflective|devils_advocate|off))?$/i);
    if (socraticMatch && this.ctx.socraticEngine) {
      const mode = socraticMatch[1]?.toLowerCase();
      if (mode === 'off') {
        const ended = this.ctx.socraticEngine.deactivate(message.sessionId);
        if (ended) {
          return mkResp(`Socratic mode **deactivated** after ${ended.exchangeCount} exchanges.`);
        }
        return mkResp('Socratic mode was not active.');
      }
      const subMode = (mode as any) || 'guided';
      this.ctx.socraticEngine.activate(message.sessionId, subMode);
      return mkResp(
        `Socratic mode **activated** (${subMode}).\n\n` +
        `I will now respond primarily with questions to help you think deeper.\n` +
        `Use \`/socratic off\` to return to normal mode.`,
      );
    }

    // /council [topic1, topic2, ...] — convene a Knowledge Council
    if (text.toLowerCase().startsWith('/council') && this.ctx.knowledgeCouncil) {
      const topicsArg = text.slice(8).trim();
      const topics = topicsArg
        ? topicsArg.split(',').map(t => t.trim()).filter(Boolean)
        : undefined;

      try {
        const session = await this.ctx.knowledgeCouncil.convene(topics, _callbacks.onProgress);
        return mkResp(session.summary ?? 'Knowledge Council session complete.');
      } catch (err) {
        return mkResp(
          `Failed to convene Knowledge Council: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /council-history — show past council sessions
    if (text.toLowerCase() === '/council-history' && this.ctx.knowledgeCouncil) {
      return mkResp(this.ctx.knowledgeCouncil.getHistorySummary());
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

    await callbacks.onProgress?.(
      `🧠 Starting self-study on: **${topic}**`,
    );
    await callbacks.onProgress?.(
      `📚 Researching and creating knowledge pellets...`,
    );

    try {
      // Use new LearningOrchestrator if available (TopicFusion + multi-pipeline synthesis)
      if (this.ctx.learningOrchestrator) {
        const cycle = await this.ctx.learningOrchestrator.learnTopic(topic, true);

        if (!cycle.success || (cycle.synthesisReport?.pelletsCreated ?? 0) === 0) {
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
          .join(', ');

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
      const { KnowledgeResearcher } = await import('../learning/researcher.js');
      const { KnowledgeGraphManager } = await import('../learning/knowledge-graph.js');

      const graphManager = new KnowledgeGraphManager(this.ctx.cwd ?? process.cwd());
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

      const pelletSummary = result.pellets.map(p => `  - **${p.title}**`).join('\n');

      const relatedSummary = result.relatedTopics.length > 0
        ? `\n\n**Related topics discovered:** ${result.relatedTopics.join(', ')}`
        : '';

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

    await callbacks.onProgress?.(
      `🧠 I don't have that capability yet — building it now...`,
    );

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
        const skillDir = filePath.replace(/\/SKILL\.md$/, '');
        const parentDir = skillDir.replace(/\/[^/]+$/, '');
        try {
          await registry.loadFromDirectory(parentDir);
          if (this.skillInjector) {
            this.skillInjector.reindex();
          }
          log.evolution.info(`[Skill] Reindexed after synthesis: ${proposal.toolName}`);
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
      this.postProcess(session.messages, session.id);
      return toGatewayResponse(retryResponse);
    } catch (err) {
      log.evolution.error(
        `Gap handling failed: ${err instanceof Error ? err.message : err}`,
      );
      // Fallback: return original apologetic response (user message already saved)
      await this.saveSession(session, message.text, response.newMessages, true, response.content);
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
    // Guard: only append the user turn if it wasn't already saved
    // (capability gap retry path calls saveSession twice for the same user message)
    if (!userAlreadySaved) {
      session.messages.push({ role: "user", content: userText });
    }
    for (const msg of newMessages) {
      session.messages.push(msg);
    }

    // Always append the final assistant response so session history
    // includes what was actually sent to the user (newMessages only
    // contains intermediate ReAct loop messages with empty content).
    if (finalContent?.trim()) {
      session.messages.push({ role: "assistant", content: finalContent });
    }

    // Trim to avoid unbounded growth
    if (session.messages.length > MAX_SESSION_HISTORY) {
      session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
    }

    await this.ctx.sessionStore.saveSession(session);

    // Update cache timestamp
    const key = session.id;
    const cached = this.sessions.get(key);
    if (cached) cached.lastActivity = Date.now();
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
        this.sessions.delete(key);
        this.stuckStreak.delete(key);
        this.attemptLogs.delete(key);
        log.engine.info(`[session-evict] Evicted stale session "${key}"`);
      } else {
        activeIds.add(key);
      }
    }
    this.attemptLogs.pruneStale(activeIds);
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
   */
  private postProcess(messages: ChatMessage[], sessionId?: string): void {
    this.postProcessor.process(messages, sessionId);
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
  private buildEngineContext(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: import("../memory/attempt-log.js").AttemptLog,
  ): EngineContext {
    return this.contextBuilder.build(
      session,
      callbacks,
      dynamicSkillsContext,
      isolatedTask,
      attemptLog,
    );
  }

  /** Fire-and-forget: detect preference statements and persist them. */
  private detectPreferences(userMessage: string, channelId: string): void {
    if (!this.ctx.preferenceStore || !this.preferenceDetector) return;
    this.runBackground(
      "preference-detect",
      this.preferenceDetector.detect(userMessage, this.ctx.preferenceStore, channelId),
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
