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
import { shouldUsePlanner } from "../engine/planner.js";
import { AttemptLogRegistry } from "../memory/attempt-log.js";
import { SkillContextInjector } from "../skills/injector.js";
import { ClawHubClient } from "../skills/clawhub.js";
import { SkillTracker } from "../skills/tracker.js";
import { log } from "../logger.js";
import { MemoryConsolidator } from "../memory/consolidator.js";
import { PreferenceDetector } from "../preferences/detector.js";
import type {
  GatewayMessage,
  GatewayResponse,
  GatewayCallbacks,
  ChannelAdapter,
  GatewayContext,
} from "./types.js";
import type { GatewayMiddleware, MiddlewareContext } from "./middleware.js";
import { RateLimitMiddleware, LoggingMiddleware } from "./middleware.js";

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
  private messageCount = 0;
  private skillInjector: SkillContextInjector | null = null;
  /** Singleton PreferenceDetector — avoids re-constructing on every message */
  private preferenceDetector: PreferenceDetector | null = null;

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

  constructor(private ctx: GatewayContext) {
    this.engine = new OwlEngine();

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

      this.skillInjector = new SkillContextInjector(
        registry,
        {
          maxSkills: 3,
          autoSearchClawHub: true,
          clawHubTargetDir:
            ctx.config.skills?.directories?.[0] || "./workspace/skills",
        },
        ctx.provider, // Pass provider for semantic routing + LLM disambiguation
        skillTracker,
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
        this.postProcess(session.messages);
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

    log.engine.incoming(message.channelId, message.text);

    // Dynamic skill injection — uses BM25 + usage-weighted semantic routing
    let dynamicSkillsContext = "";
    if (this.skillInjector) {
      const relevantSkills = await this.skillInjector.getRelevantSkills(text);
      if (relevantSkills.length > 0) {
        // Use the injector's composition-aware formatter
        dynamicSkillsContext = await this.skillInjector.injectIntoContext(text);
        log.engine.info(
          `Dynamic skill injection: ${relevantSkills.map((s) => s.name).join(", ")}`,
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

    const engineCtx = this.buildEngineContext(
      session,
      callbacks,
      dynamicSkillsContext,
      isIsolatedTask,
      this.attemptLogs.get(message.sessionId),
    );
    // Use planner for complex multi-step tasks when enabled
    const planningEnabled = this.ctx.config.engine?.planning?.enabled ?? false;
    const response =
      planningEnabled && shouldUsePlanner(text)
        ? await this.engine.runWithPlan(text, engineCtx)
        : await this.engine.run(text, engineCtx);

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
    this.postProcess(session.messages);

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

    // Reactive learning
    if (this.ctx.learningEngine) {
      try {
        await this.ctx.learningEngine.processConversation(messages);
        log.engine.info("[endSession:learning] ✓ completed");
      } catch (err) {
        log.engine.warn(
          `[endSession:learning] ✗ failed: ${err instanceof Error ? err.message : err}`,
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
  getCapabilityLedger() {
    return this.ctx.capabilityLedger;
  }
  getPreferenceStore() {
    return this.ctx.preferenceStore;
  }
  getReflexionEngine() {
    return this.ctx.reflexionEngine;
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
    message: GatewayMessage,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse | null> {
    if (!this.ctx.learningEngine) return null;

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

      const { response: retryResponse } =
        await this.ctx.evolution!.buildAndRetry(
          proposal,
          message.text,
          engineCtx,
          this.engine,
          askInstall,
          onProgress,
        );

      // userAlreadySaved=true: the user message was saved in the normal path before gap was detected
      await this.saveSession(
        session,
        message.text,
        retryResponse.newMessages,
        true,
        retryResponse.content,
      );
      this.postProcess(session.messages);
      return toGatewayResponse(retryResponse);
    } catch (err) {
      log.evolution.error(
        `Gap handling failed: ${err instanceof Error ? err.message : err}`,
      );
      // Fallback: return original apologetic response (user message already saved)
      await this.saveSession(session, message.text, response.newMessages, true, response.content);
      this.postProcess(session.messages);
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
   * Run a named background task. Logs both start and success/failure so
   * every subsystem is observable — no silent failures.
   */
  private runBackground(name: string, task: Promise<unknown>): void {
    task.then(
      () => log.engine.info(`[bg:${name}] ✓ completed`),
      (err) =>
        log.engine.warn(
          `[bg:${name}] ✗ failed: ${err instanceof Error ? err.message : String(err)}`,
        ),
    );
  }

  /**
   * Fire-and-forget tasks that run after every response.
   * Each task is named so failures are visible in logs.
   */
  private postProcess(messages: ChatMessage[]): void {
    if (this.ctx.learningEngine) {
      this.runBackground(
        "learning",
        this.ctx.learningEngine.processConversation(messages),
      );
    }

    this.messageCount++;
    const evolutionInterval = this.ctx.config.owlDna?.evolutionBatchSize ?? 10;
    if (
      this.messageCount % evolutionInterval === 0 &&
      this.ctx.evolutionEngine
    ) {
      this.runBackground(
        `dna-evolve(${this.ctx.owl.persona.name})`,
        this.ctx.evolutionEngine.evolve(this.ctx.owl.persona.name),
      );
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

  private buildEngineContext(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: import("../memory/attempt-log.js").AttemptLog,
  ): EngineContext {
    const preferencesContext =
      this.ctx.preferenceStore?.toContextString() ?? "";

    // Always-include skills: inject full XML instructions (not just names)
    // These are skills marked `openclaw.always: true` — always present in context.
    // Relevant skills (per-message) are injected in handle() as dynamicSkillsContext.
    let skillsContext = "";
    if (this.ctx.skillsLoader) {
      const registry = this.ctx.skillsLoader.getRegistry();
      const alwaysSkills = registry
        .listEnabled()
        .filter((s) => s.metadata.openclaw?.always === true);
      if (alwaysSkills.length > 0) {
        skillsContext =
          "\n## Always-Available Skills\n" +
          alwaysSkills
            .map(
              (s) => `\n<skill name="${s.name}">\n${s.instructions}\n</skill>`,
            )
            .join("\n");
      }
    }

    // Merge always-on skills + per-message relevant skills
    const finalSkillsContext = skillsContext + dynamicSkillsContext;

    return {
      provider: this.ctx.provider,
      owl: this.ctx.owl,
      sessionHistory: session.messages,
      config: this.ctx.config,
      toolRegistry: this.ctx.toolRegistry,
      pelletStore: this.ctx.pelletStore,
      capabilityLedger: this.ctx.capabilityLedger,
      cwd: this.ctx.cwd,
      memoryContext: this.ctx.memoryContext,
      preferencesContext: preferencesContext || undefined,
      skillsContext: finalSkillsContext || undefined,
      skillsRegistry: this.ctx.skillsLoader?.getRegistry(),
      skillTracker: this.skillInjector?.getTracker(),
      isolatedTask: isolatedTask,
      attemptLog,
      onProgress: callbacks.onProgress,
      onStreamEvent: callbacks.onStreamEvent,
      sendFile: callbacks.onFile,
      providerRegistry: this.ctx.providerRegistry,
    };
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
