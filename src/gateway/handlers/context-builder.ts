/**
 * StackOwl — Engine Context Builder
 *
 * Extracted from gateway/core.ts. Assembles the EngineContext
 * that the ReAct loop needs — merging ambient signals, knowledge,
 * predictions, collaboration state, user profile, etc.
 */

import type { Session } from "../../memory/store.js";
import type { GatewayContext } from "../types.js";
import type { GatewayCallbacks } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import { DiagnosticEngine } from "../../engine/diagnostic-engine.js";
import { PelletSearch } from "../../pellets/search.js";
import type { MicroLearner } from "../../learning/micro-learner.js";
import type { SkillContextInjector } from "../../skills/injector.js";
import type { AttemptLog } from "../../memory/attempt-log.js";
import type { UserMentalModel } from "../../cognition/user-mental-model.js";
import {
  computeTemporalContext,
  formatTemporalPrompt,
  loadPreviousSession,
} from "../../cognition/temporal-context.js";

export class ContextBuilder {
  /** Cache previous session lookup per session ID to avoid repeated disk reads */
  private prevSessionCache: Map<
    string,
    { session: Session | null; cachedAt: number }
  > = new Map();

  /** Lazily-created PelletSearch instance (normalized TF-IDF cosine similarity) */
  private pelletSearch: PelletSearch | null = null;

  constructor(
    private ctx: GatewayContext,
    private microLearner: MicroLearner | null,
    private skillInjector: SkillContextInjector | null,
    private userMentalModel: UserMentalModel | null = null,
  ) {
    // Eagerly create PelletSearch when pelletStore is available
    if (ctx.pelletStore) {
      this.pelletSearch = new PelletSearch(ctx.pelletStore);
    }
  }

  async build(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: AttemptLog,
  ): Promise<EngineContext> {
    // ─── Temporal Context (Phase 1 — zero LLM cost) ──────────────
    let temporalContext = "";
    try {
      const timezone =
        (this.ctx.config as any).timezone ??
        Intl.DateTimeFormat().resolvedOptions().timeZone;
      // Cache previous session for 10 minutes to avoid repeated disk reads
      const CACHE_TTL = 10 * 60 * 1000;
      let previousSession: Session | null = null;
      const cached = this.prevSessionCache.get(session.id);
      if (cached && Date.now() - cached.cachedAt < CACHE_TTL) {
        previousSession = cached.session;
      } else {
        previousSession = await loadPreviousSession(
          this.ctx.sessionStore,
          session.id,
        );
        this.prevSessionCache.set(session.id, {
          session: previousSession,
          cachedAt: Date.now(),
        });
      }
      const snapshot = computeTemporalContext(
        session,
        previousSession,
        timezone,
      );
      temporalContext = formatTemporalPrompt(snapshot);
    } catch {
      // Non-fatal — temporal context is supplementary
    }

    const preferencesContext =
      this.ctx.preferenceStore?.toContextString() ?? "";

    // Always-include skills
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

    const finalSkillsContext = skillsContext + dynamicSkillsContext;

    // ─── Context Triage — only inject what's relevant ────────────
    // Instead of dumping 20+ signals into every prompt, score each signal
    // and include only what's relevant to this specific message.
    const userMessage =
      [...session.messages]
        .reverse()
        .find((m) => m.role === "user")?.content ?? "";

    const MEMORY_TIMEOUT = 2_000;
    const withTimeout = <T>(promise: Promise<T>): Promise<T | null> =>
      Promise.race([
        promise,
        new Promise<null>((resolve) => setTimeout(() => resolve(null), MEMORY_TIMEOUT)),
      ]);

    // ── Temporal recall detection ──────────────────────────────
    const TEMPORAL_TRIGGERS = /\b(?:yesterday|last time|before|remember when|as I said|we discussed|you told me|earlier|previously|last week|other day)\b/i;
    const hasTemporalTrigger = TEMPORAL_TRIGGERS.test(userMessage);

    // ── Frustration / emotional signal detection ───────────────
    const FRUSTRATION_SIGNALS = /\b(?:still|again|already told|why.*keep|not working|broken|frustrated|fix this|you always|you never)\b/i;
    const hasFrustration = FRUSTRATION_SIGNALS.test(userMessage);

    // ── Opinion / debate request detection ────────────────────
    const OPINION_SIGNALS = /\b(?:what do you think|your opinion|agree|disagree|is it true|do you believe|controversial|debate|best|worst)\b/i;
    const isOpinionRequest = OPINION_SIGNALS.test(userMessage);

    // ── Conversational message detection (no context needed) ──
    const isConversational = userMessage.length < 80 &&
      !/\b(?:find|search|create|write|generate|check|analyze|run|scan|build|calculate|translate|download|fetch|get|show|list)\b/i.test(userMessage);

    // ── Session depth ──────────────────────────────────────────
    const sessionDepth = session.messages.filter(m => m.role === "user").length;

    // ─── Always-included signals ──────────────────────────────

    // Mode directive (ASSISTANT vs REACTIVE) — always relevant
    const hasActiveItems =
      (this.ctx.intentStateMachine?.getActive().length ?? 0) > 0 ||
      (this.ctx.commitmentTracker?.getPending().length ?? 0) > 0 ||
      (this.ctx.goalGraph?.getStale(1).length ?? 0) > 0;

    const modeDirective = hasActiveItems
      ? `<mode>ASSISTANT</mode>

You have active tasks, commitments, or pending goals. Be proactive:
- Follow up on things you've promised to do
- Anticipate the user's needs based on current tasks
- Don't just answer and stop — think about what else might be helpful
- If you're waiting on something, let the user know`
      : `<mode>REACTIVE</mode>

The user has no active tasks right now. Be concise and helpful:
- Answer directly what they're asking
- Don't add unnecessary fluff
- If you complete a task, confirm completion briefly`;

    // Behavioral patches — always inject (top 3, prevents repeated errors)
    let behavioralPatchContext = "";
    if (this.ctx.pelletStore) {
      try {
        const allPellets = await this.ctx.pelletStore.listAll();
        const patches = allPellets
          .filter((p) => p.tags?.includes("behavioral-patch"))
          .slice(0, 3); // Top 3 only (was 5)
        if (patches.length > 0) {
          behavioralPatchContext =
            "\n<learned_rules>\n" +
            "Rules learned from past mistakes — follow these to avoid repeating errors:\n" +
            patches
              .map((p) => `  <rule>${p.content.slice(0, 200)}</rule>`)
              .join("\n") +
            "\n</learned_rules>\n";
        }
      } catch {
        // Non-fatal
      }
    }

    // Socratic mode — only inject when active for this session
    let socraticContext = "";
    if (this.ctx.socraticEngine?.isActive(session.id)) {
      socraticContext = this.ctx.socraticEngine.toContextString(session.id);
    }

    // ─── Conditionally-included signals ──────────────────────

    // Active intents — only when there ARE active intents
    let intentContext = "";
    const activeIntents = this.ctx.intentStateMachine?.getActive() ?? [];
    if (activeIntents.length > 0) {
      intentContext = this.ctx.intentStateMachine!.toContextString();
    }

    // Working context — only when session has tool activity (not first message)
    let workingCtxString = "";
    if (this.ctx.workingContextManager && sessionDepth > 1) {
      const wc = this.ctx.workingContextManager.get(session.id);
      if (wc) {
        workingCtxString = wc.toContextString();
      }
    }

    // Ground state — only in established conversations (10+ exchanges)
    let groundStateContext = "";
    if (this.ctx.groundState && sessionDepth >= 10) {
      const userId = session.id.split(":")[1] || session.id;
      this.ctx.groundState.setSession(session.id);
      groundStateContext = this.ctx.groundState.toContextString(userId);
    }

    // User mental model — only when frustration signals detected
    let mentalModelContext = "";
    if (this.userMentalModel && hasFrustration) {
      mentalModelContext = this.userMentalModel.toContextString();
    }

    // Echo chamber — only when user asks for opinions/debate
    let echoChamberContext = "";
    if (this.ctx.echoChamberDetector && isOpinionRequest) {
      echoChamberContext = this.ctx.echoChamberDetector.toContextString();
    }

    // Predictive queue — only when high-confidence predictions exist
    let predictiveContext = "";
    if (this.ctx.predictiveQueue) {
      const ready = this.ctx.predictiveQueue.getReadyTasks()
        .filter((t) => t.confidence >= 0.7);
      if (ready.length > 0) {
        predictiveContext =
          "\n<predicted_tasks>\n" +
          ready
            .map(
              (t) =>
                `  <task confidence="${t.confidence.toFixed(2)}">${t.action}</task>`,
            )
            .join("\n") +
          "\n</predicted_tasks>\n";
      }
    }

    // User profile — only for non-conversational messages (action requests)
    let userProfileContext = "";
    if (this.microLearner && !isConversational) {
      userProfileContext = this.microLearner.toContextString();
    }

    // Inferred preferences — only for action requests
    let inferredPrefsContext = "";
    if (this.ctx.preferenceModel && !isConversational) {
      inferredPrefsContext = this.ctx.preferenceModel.toContextString();
    }

    // Knowledge graph — only for non-conversational messages
    let knowledgeContext = "";
    if (this.ctx.knowledgeReasoner && !isConversational && userMessage) {
      const nodes = this.ctx.knowledgeGraph?.search(userMessage, 3);
      if (nodes && nodes.length > 0) {
        knowledgeContext =
          "\n<knowledge_context>\n" +
          nodes
            .map(
              (n) =>
                `  <fact domain="${n.domain}" confidence="${n.confidence}">${n.title}: ${n.content}</fact>`,
            )
            .join("\n") +
          "\n</knowledge_context>\n";
      }
    }

    // Collab context — only when active collab sessions exist
    let collabContext = "";
    if (this.ctx.collabManager) {
      const userSessions = this.ctx.collabManager.getUserSessions(
        session.id.split(":")[1] || session.id,
      );
      if (userSessions.length > 0) {
        collabContext = this.ctx.collabManager.buildCollabContext(
          userSessions[0].id,
        );
      }
    }

    // Ambient context — skip for conversational messages
    let ambientContext = "";
    if (this.ctx.contextMesh && !isConversational) {
      ambientContext = this.ctx.contextMesh.toContextBlock(5);
    }

    // ─── FactStore + EpisodicMemory retrieval ─────────────────
    // Facts: retrieve only when keyword overlap suggests relevance
    // Episodes: retrieve when temporal trigger detected OR non-trivial message
    let factContext = "";
    let episodicContext = "";

    if (userMessage && !isConversational) {
      const episodeLimit = hasTemporalTrigger ? 5 : 2;
      const episodeThreshold = hasTemporalTrigger ? 0.2 : 0.35; // Raised from 0.3

      const [factResults, episodeResults] = await Promise.all([
        this.ctx.factStore
          ? withTimeout(
              (async () => this.ctx.factStore!.search(userMessage, undefined, 3))(), // Reduced from 5
            ).catch(() => null)
          : Promise.resolve(null),
        this.ctx.episodicMemory && (hasTemporalTrigger || sessionDepth > 1)
          ? withTimeout(
              this.ctx.episodicMemory.searchWithScoring(
                userMessage,
                episodeLimit,
                this.ctx.provider ?? undefined,
                episodeThreshold,
              ),
            ).catch(() => null)
          : Promise.resolve(null),
      ]);

      if (factResults && factResults.length > 0) {
        factContext =
          "\n<remembered_facts>\n" +
          factResults
            .map(
              (f) =>
                `  <fact category="${f.category}" confidence="${f.confidence.toFixed(2)}">${f.fact}</fact>`,
            )
            .join("\n") +
          "\n</remembered_facts>\n";
      }

      if (episodeResults && episodeResults.length > 0) {
        episodicContext =
          "\n<past_episodes>\n" +
          episodeResults
            .map(
              (ep) =>
                `  <episode date="${new Date(ep.date).toLocaleDateString()}" sentiment="${ep.sentiment ?? "neutral"}" importance="${(ep.importance ?? 0.5).toFixed(1)}">${ep.summary}</episode>`,
            )
            .join("\n") +
          "\n</past_episodes>\n";
      }
    }

    // ─── Assemble enriched context (triage applied) ───────────
    // Order: temporal (frames time) → mode → memory → conditional signals
    const enrichedMemoryContext = [
      temporalContext,
      this.ctx.memoryContext ?? "",
      modeDirective,
      behavioralPatchContext,
      // Memory signals (high relevance)
      factContext,
      episodicContext,
      // Conditional signals (injected only when relevant per triage above)
      intentContext,
      workingCtxString,
      socraticContext,
      knowledgeContext,
      userProfileContext,
      inferredPrefsContext,
      predictiveContext,
      collabContext,
      ambientContext,
      // Low-frequency signals (injected only in specific circumstances)
      mentalModelContext,
      echoChamberContext,
      groundStateContext,
    ]
      .filter(Boolean)
      .join("\n");

    return {
      provider: this.ctx.provider,
      owl: this.ctx.owl,
      sessionHistory: session.messages,
      config: this.ctx.config,
      toolRegistry: this.ctx.toolRegistry,
      pelletStore: this.ctx.pelletStore,
      pelletSearch: this.pelletSearch ?? undefined,
      capabilityLedger: this.ctx.capabilityLedger,
      cwd: this.ctx.cwd,
      memoryContext: enrichedMemoryContext || undefined,
      preferencesContext: preferencesContext || undefined,
      skillsContext: finalSkillsContext || undefined,
      skillsRegistry: this.ctx.skillsLoader?.getRegistry(),
      skillTracker: this.skillInjector?.getTracker(),
      isolatedTask,
      attemptLog,
      onProgress: callbacks.onProgress,
      onStreamEvent: callbacks.onStreamEvent,
      sendFile: callbacks.onFile,
      providerRegistry: this.ctx.providerRegistry,
      memorySearcher: this.ctx.memorySearcher,
      echoChamberDetector: this.ctx.echoChamberDetector,
      journalGenerator: this.ctx.journalGenerator,
      questManager: this.ctx.questManager,
      capsuleManager: this.ctx.capsuleManager,
      innerLife: this.ctx.innerLife,
      diagnosticEngine: new DiagnosticEngine(this.ctx.provider),
    };
  }
}
