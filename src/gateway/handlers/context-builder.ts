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
import type { MicroLearner } from "../../learning/micro-learner.js";
import type { SkillContextInjector } from "../../skills/injector.js";
import type { AttemptLog } from "../../memory/attempt-log.js";
import type { UserMentalModel } from "../../cognition/user-mental-model.js";
import { log } from "../../logger.js";
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

  constructor(
    private ctx: GatewayContext,
    private microLearner: MicroLearner | null,
    private skillInjector: SkillContextInjector | null,
    private userMentalModel: UserMentalModel | null = null,
  ) {}

  async build(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: AttemptLog,
    channelId?: string,
    userId?: string,
    continuityResult?:
      | import("../../cognition/continuity-engine.js").ContinuityResult
      | null,
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
      [...session.messages].reverse().find((m) => m.role === "user")?.content ??
      "";

    const MEMORY_TIMEOUT = 2_000;
    const withTimeout = <T>(promise: Promise<T>): Promise<T | null> =>
      Promise.race([
        promise,
        new Promise<null>((resolve) =>
          setTimeout(() => resolve(null), MEMORY_TIMEOUT),
        ),
      ]);

    // ── Temporal recall detection ──────────────────────────────
    const TEMPORAL_TRIGGERS =
      /\b(?:yesterday|last time|before|remember when|as I said|we discussed|you told me|earlier|previously|last week|other day)\b/i;
    const hasTemporalTrigger = TEMPORAL_TRIGGERS.test(userMessage);

    // ── Frustration / emotional signal detection ───────────────
    const FRUSTRATION_SIGNALS =
      /\b(?:still|again|already told|why.*keep|not working|broken|frustrated|fix this|you always|you never)\b/i;
    const hasFrustration = FRUSTRATION_SIGNALS.test(userMessage);

    // ── Opinion / debate request detection ────────────────────
    const OPINION_SIGNALS =
      /\b(?:what do you think|your opinion|agree|disagree|is it true|do you believe|controversial|debate|best|worst)\b/i;
    const isOpinionRequest = OPINION_SIGNALS.test(userMessage);

    // ── Conversational message detection (no context needed) ──
    const isConversational =
      userMessage.length < 80 &&
      !/\b(?:find|search|create|write|generate|check|analyze|run|scan|build|calculate|translate|download|fetch|get|show|list)\b/i.test(
        userMessage,
      );

    // ── Session depth ──────────────────────────────────────────
    const sessionDepth = session.messages.filter(
      (m) => m.role === "user",
    ).length;

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

    // Compressed history summary (Phase 2) — replaces the oldest messages
    // with a structured summary (~300 tokens vs ~8,000 for 40 raw messages).
    // Only injected when a summary exists for this session.
    let compressionSummaryContext = "";
    if (this.ctx.compressor && this.ctx.db) {
      try {
        compressionSummaryContext = this.ctx.compressor.buildContext(
          session.id,
          session.messages,
        );
      } catch {
        // Non-fatal
      }
    }

    // Conversation digest (L1 working memory) — persisted semantic snapshot of
    // what was found/decided/failed in the previous turn. Injected FIRST so the
    // model always knows "what I just did" before reading raw history.
    // Replaces WorkingContext which was in-memory only and lost on restart.
    let digestContext = "";
    let digest: Awaited<
      ReturnType<NonNullable<typeof this.ctx.digestManager>["load"]>
    > = null;
    let continuityContext = "";
    if (this.ctx.digestManager && sessionDepth > 0) {
      try {
        digest = await this.ctx.digestManager.load(session.id);
        if (
          digest &&
          (digest.artifacts.length > 0 ||
            digest.decisions.length > 0 ||
            digest.failed.length > 0 ||
            digest.task)
        ) {
          digestContext = this.ctx.digestManager.toContextString(digest);
        }

        // ── Continuity-based context enrichment (Letta-style named memory block) ──
        // Inject the verbatim last response as a pinned memory block so the model
        // knows exactly what "it", "that", or any specific entity name refers to.
        // CONTINUATION/FOLLOW_UP: full verbatim response (model needs to expand on it)
        // TOPIC_SWITCH: abbreviated summary only (model needs to know topic changed)
        const classification = continuityResult?.classification;
        if (continuityResult && digest?.lastAssistantResponse) {
          const isDirectFollowUp =
            classification === "CONTINUATION" ||
            classification === "FOLLOW_UP";
          const isFreshStart = classification === "FRESH_START";

          const lines: string[] = [
            `<prior_response classification="${classification}">`,
          ];

          if (continuityResult.priorTopicSummary) {
            lines.push(`<topic>${continuityResult.priorTopicSummary}</topic>`);
          }

          if (isDirectFollowUp) {
            // Full verbatim response — model needs exact details to answer follow-up
            lines.push(digest.lastAssistantResponse.slice(0, 2000));
            lines.push(
              "\nThe user is asking a follow-up to the above. Reference this content " +
                "to understand what 'it', 'that', or any specific entity name refers to. " +
                "Do NOT repeat information already shown — expand on it instead.",
            );
          } else if (isFreshStart) {
            // Returning user after a long gap — inject a BRIEF reminder of prior context.
            // The full episodic memory search handles deeper recall; this is a quick hint.
            // Without this the model has NO anchor and says "I don't have context".
            const brief = digest.lastAssistantResponse.slice(0, 200);
            lines.push(
              brief + (digest.lastAssistantResponse.length > 200 ? "..." : ""),
            );
            lines.push(
              "\nThe user is returning after a gap. The snippet above is from your last response. " +
                "Use the <past_episodes> below (if any) to recall what was discussed. " +
                "If no episodes exist yet, acknowledge the gap naturally without making up details.",
            );
          } else {
            // TOPIC_SWITCH: brief summary — model knows context exists but focus on new request
            const summary = digest.lastAssistantResponse.slice(0, 300);
            lines.push(
              summary +
                (digest.lastAssistantResponse.length > 300 ? "..." : ""),
            );
            lines.push(
              "\nThe user has switched topics. Prior context shown for reference only — " +
                "focus on the new request.",
            );
          }

          lines.push("</prior_response>");
          continuityContext = lines.join("\n");
          log.engine.info(
            `[Continuity] Injecting ${isDirectFollowUp ? "verbatim" : isFreshStart ? "fresh-start-hint" : "summary"} prior response block for ${classification}`,
          );
        }
      } catch {
        // Non-fatal
      }
    }

    // ─── Synthesis self-knowledge (Phase A) ──────────────────────
    // Tells the owl what tools/skills it has synthesized before.
    // Prevents "I cannot create or persist tools" responses.
    // Injected as an identity block at the TOP of the system prompt.
    let synthesisKnowledgeContext = "";
    if (this.ctx.db) {
      try {
        const owlName = this.ctx.owl.persona.name;
        const synthesized =
          this.ctx.db.synthesisMemory.getActiveForOwl(owlName);
        if (synthesized.length > 0) {
          const lines = synthesized.map((s) => {
            const uses = s.successCount + s.failCount;
            return `  • ${s.capabilityDescription.slice(0, 120)} (${s.synthesisApproach}, ${uses} use${uses !== 1 ? "s" : ""})`;
          });
          synthesisKnowledgeContext =
            `\n[Your Synthesis History]\n` +
            `You are running inside StackOwl — an autonomous agent that CAN build new tools.\n` +
            `You have previously built these capabilities:\n` +
            lines.join("\n") +
            "\n" +
            `When you encounter a task without a matching tool, output [CAPABILITY_GAP: description] to trigger synthesis.\n` +
            `Do NOT say you cannot persist or create tools — you have already done it.\n`;
        }
      } catch {
        // Non-fatal
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
      const ready = this.ctx.predictiveQueue
        .getReadyTasks()
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

    // MemoryBus — unified cross-store retrieval (Step 5)
    // Fires on every non-conversational message so the model has
    // cross-store recall (reflexion + pellets + profile + legacy memory.md).
    let memoryBusContext = "";
    if (this.ctx.memoryBus && !isConversational && userMessage) {
      try {
        const recalled = await this.ctx.memoryBus.recall(userMessage, 10, 2500);
        if (recalled.length > 0) {
          memoryBusContext = this.ctx.memoryBus.toSystemPrompt(recalled, 2500);
        }
      } catch {
        // Best-effort — non-fatal if MemoryBus query fails
      }
    }

    // ─── FactStore + EpisodicMemory retrieval ─────────────────
    // Facts: always retrieved for non-conversational messages.
    // Episodes: retrieved for ALL messages when user is returning (FRESH_START /
    // TOPIC_SWITCH) — these are the exact moments when cross-session context matters.
    // Also retrieved for any message with a temporal trigger or after 2+ turns.
    //
    // IMPORTANT: "isConversational" must NOT block episodic lookup for returning
    // users. A message like "what was I working on?" is < 80 chars with no action
    // keywords → classified conversational → but it NEEDS episodic context.
    let factContext = "";
    let episodicContext = "";

    const isReturningUser =
      continuityResult?.classification === "FRESH_START" ||
      continuityResult?.classification === "TOPIC_SWITCH";

    // Episodic lookup fires when: temporal trigger, 2+ turns in, returning user,
    // or session has no prior messages (genuinely first message — may be returning).
    const shouldQueryEpisodic =
      !!this.ctx.episodicMemory &&
      !!userMessage &&
      (hasTemporalTrigger || sessionDepth > 1 || isReturningUser || sessionDepth === 0);

    // Facts are only meaningful for non-conversational messages (actions/queries).
    // But for returning users even a short question needs facts.
    const shouldQueryFacts =
      !!this.ctx.factStore &&
      !!userMessage &&
      (!isConversational || isReturningUser);

    if (shouldQueryFacts || shouldQueryEpisodic) {
      // Tune episode retrieval based on context:
      // returning user → be generous (3 episodes, low threshold)
      // temporal trigger → wide recall (5 episodes, very low threshold)
      // normal → conservative (2 episodes, standard threshold)
      const episodeLimit = hasTemporalTrigger ? 5 : isReturningUser ? 3 : 2;
      const episodeThreshold = hasTemporalTrigger
        ? 0.2
        : isReturningUser
          ? 0.15 // generous for returning users — better to show than miss
          : 0.35;

      const [factResults, episodeResults] = await Promise.all([
        shouldQueryFacts
          ? withTimeout(
              // Use semantic (embedding) search when provider available — finds
              // "yt-dlp for instagram" when searching "download video from social media".
              // Falls back to keyword search automatically inside semanticSearch().
              this.ctx.provider
                ? this.ctx.factStore!.semanticSearch(
                    userMessage,
                    this.ctx.provider,
                    undefined,
                    3,
                  )
                : (async () =>
                    this.ctx.factStore!.search(userMessage, undefined, 3))(),
            ).catch(() => null)
          : Promise.resolve(null),
        shouldQueryEpisodic
          ? withTimeout(
              this.ctx.episodicMemory!.searchWithScoring(
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

    // ─── Cross-owl learnings (Phase 5) ───────────────────────
    // Inject skills/insights this owl (and any owl) has accumulated.
    // Enables knowledge sharing: if owl A learned how to fix X, owl B knows too.
    let owlLearningsContext = "";
    if (this.ctx.db && userMessage && (!isConversational || isReturningUser)) {
      try {
        const learnings = this.ctx.db.owlLearnings.search(userMessage, 3);
        if (learnings.length > 0) {
          owlLearningsContext =
            "\n<owl_learnings>\n" +
            learnings
              .map(
                (l) =>
                  `  <learning owl="${l.owlName}" category="${l.category}">${l.learning}</learning>`,
              )
              .join("\n") +
            "\n</owl_learnings>\n";
        }
      } catch {
        // Non-fatal
      }
    }

    // ─── Channel format hint ──────────────────────────────────
    // Injected when the channel cannot render markdown tables.
    // Primary prevention — keeps LLM output table-free from the start.
    const channelFormatHint =
      channelId === "telegram"
        ? "Formatting rules for this channel: never use markdown tables (| col | col |). " +
          "For structured data or comparisons, use bold labels and bullet points: **Label:** value. " +
          "For lists use numbered or bulleted lists. Keep responses concise."
        : "";

    // ─── Assemble enriched context (triage applied) ───────────
    // Order: continuity (prior response) → digest → temporal → mode → memory → conditional
    // continuityContext goes FIRST when present — the model must read "what I just said"
    // before processing the user's follow-up, just like Letta pins core memory at the top.
    const enrichedMemoryContext = [
      synthesisKnowledgeContext, // L0: owl self-knowledge — what tools it can build (identity block)
      continuityContext, // L1.5: pinned prior-response block (FOLLOW_UP/CONTINUATION/TOPIC_SWITCH)
      digestContext, // L1: artifacts/decisions/failures from last turn
      compressionSummaryContext, // L2: compressed history of older messages
      temporalContext,
      channelFormatHint,
      this.ctx.memoryContext ?? "",
      modeDirective,
      behavioralPatchContext,
      // Memory signals (high relevance)
      owlLearningsContext, // Cross-owl shared knowledge (Phase 5)
      factContext,
      episodicContext,
      // Conditional signals (injected only when relevant per triage above)
      intentContext,
      socraticContext,
      knowledgeContext,
      userProfileContext,
      inferredPrefsContext,
      predictiveContext,
      collabContext,
      ambientContext,
      memoryBusContext, // Cross-store unified memory (MemoryBus)
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
      // pelletSearch removed — runtime.ts uses pelletStore.search() directly
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
      pendingFiles: [],
      channelName: channelId,
      providerRegistry: this.ctx.providerRegistry,
      memorySearcher: this.ctx.memorySearcher,
      echoChamberDetector: this.ctx.echoChamberDetector,
      journalGenerator: this.ctx.journalGenerator,
      questManager: this.ctx.questManager,
      capsuleManager: this.ctx.capsuleManager,
      innerLife: this.ctx.innerLife,
      diagnosticEngine: new DiagnosticEngine(this.resolveSynthesisProvider()),
      // L3 memory access for remember() / recall() tools
      factStore: this.ctx.factStore,
      episodicMemory: this.ctx.episodicMemory,
      userId,
      db: this.ctx.db,
      sessionId: session.id,
    };
  }

  /**
   * Resolve the provider designated for synthesis/analysis tasks.
   * Reads config.synthesis.provider and looks it up in the registry.
   * Falls back to the default provider if not registered.
   */
  private resolveSynthesisProvider() {
    const providerName = this.ctx.config.synthesis?.provider ?? "anthropic";
    if (this.ctx.providerRegistry) {
      try {
        return this.ctx.providerRegistry.get(providerName);
      } catch {
        // synthesis provider not registered — fall back silently
      }
    }
    return this.ctx.provider;
  }
}
