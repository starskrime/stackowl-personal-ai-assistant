import { log } from "../logger.js";
import type { GatewayMessage, GatewayResponse, GatewayContext, GatewayCallbacks } from "./types.js";
import type { ParliamentSession } from "../parliament/protocol.js";
import type { ChatMessage } from "../providers/base.js";
import { updateParliamentDNA } from "../owls/evolution.js";

// ─── shuffleArray utility (same as core.ts) ───────────────────────────────
function shuffleArray<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export interface IParliamentSubsystem {
  shouldAutoTrigger(messageText: string): Promise<boolean>;
  run(
    message: GatewayMessage,
    ctx: GatewayContext,
    callbacks?: GatewayCallbacks,
    session?: { id: string; messages: ChatMessage[] },
  ): Promise<GatewayResponse | null>;
}

/**
 * ParliamentSubsystem — canonical facade for all parliament execution paths.
 *
 * Collapses the 3× duplicated parliament logic in handleCore() into a single
 * testable class. Internally delegates to the parliament module instances
 * stored in GatewayContext.
 *
 * Uses the real MultiRoundDebateManager API: runDebate(session) mutates the
 * session in place and returns void. Synthesis is read from session.synthesis
 * after the call completes.
 */
export class ParliamentSubsystem implements IParliamentSubsystem {
  constructor(private readonly ctx: GatewayContext) {}

  async shouldAutoTrigger(messageText: string): Promise<boolean> {
    log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: entry", { textLen: messageText.length });
    const trigger = this.ctx.parliamentAutoTrigger;
    if (!trigger) {
      log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: no trigger configured");
      return false;
    }
    const result = await trigger.check(messageText, this.ctx.provider);
    log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: exit", { shouldTrigger: result.shouldTrigger, reason: result.reason });
    return result.shouldTrigger;
  }

  async run(
    message: GatewayMessage,
    ctx: GatewayContext,
    callbacks?: GatewayCallbacks,
    session?: { id: string; messages: ChatMessage[] },
  ): Promise<GatewayResponse | null> {
    log.parliament.debug("ParliamentSubsystem.run: entry", { sessionId: message.sessionId });

    const { multiRoundDebate, debatePelletGenerator } = ctx;

    if (!multiRoundDebate || !debatePelletGenerator) {
      log.parliament.debug("ParliamentSubsystem.run: dependencies missing, skipping");
      return null;
    }

    // ─── Topic worthiness check ────────────────────────────────────────────
    const { topicWorthiness } = ctx;
    let worthinessCategory = "other";
    if (topicWorthiness) {
      log.parliament.debug("ParliamentSubsystem.run: checking topic worthiness");
      const worthiness = await topicWorthiness.evaluate(message.text).catch((err: Error) => {
        log.parliament.error("ParliamentSubsystem.run: topic worthiness failed", err, { sessionId: message.sessionId });
        return null;
      });
      const isWorthy = worthiness ? (worthiness.isWorthy ?? true) : true;
      if (worthiness && !isWorthy) {
        log.parliament.debug("ParliamentSubsystem.run: topic not worthy", { score: worthiness.score });
        return null;
      }
      if (worthiness?.category) {
        worthinessCategory = worthiness.category;
      }
    }

    // ─── Notify caller that debate is beginning ────────────────────────────
    if (callbacks?.onProgress) {
      await callbacks.onProgress(`🦉 **Parliament** — convening debate on: ${message.text.slice(0, 80)}`);
    }

    // ─── Construct ParliamentSession ───────────────────────────────────────
    const participants = ctx.owlRegistry
      ? shuffleArray([...ctx.owlRegistry.listOwls()]).slice(0, 3)
      : [];

    const contextMessages = session
      ? session.messages.slice(-10).map(m => ({ role: m.role as "user" | "assistant", content: m.content }))
      : [];

    const debateSession: ParliamentSession = {
      id: `debate_${Date.now()}`,
      config: {
        topic: message.text.slice(0, 200),
        participants,
        contextMessages,
        callbacks: callbacks?.debateCallbacks,
      },
      phase: "setup",
      positions: [],
      challenges: [],
      synthesis: "",
      verdict: undefined,
      startedAt: Date.now(),
    };

    log.parliament.debug("ParliamentSubsystem.run: running debate", { participants: participants.length });

    // ─── Run the debate — mutates debateSession in place ──────────────────
    await multiRoundDebate.runDebate(debateSession);

    log.parliament.debug("ParliamentSubsystem.run: debate complete", {
      verdict: debateSession.verdict,
      synthesisLen: debateSession.synthesis?.length ?? 0,
    });

    // ─── Generate pellet — fire and forget, must not block response ────────
    void debatePelletGenerator.generateFromSession(debateSession).catch((err: Error) => {
      log.parliament.error("ParliamentSubsystem.run: pellet generation failed", err, { sessionId: message.sessionId });
    });

    // ─── Inject synthesis into ContextPipeline ─────────────────────────────
    const synthesis = debateSession.synthesis ?? "";
    const minorityContent =
      debateSession.positions.find(p => p.position === "AGAINST")?.argument
      ?? debateSession.challenges[0]?.challengeContent
      ?? "";
    const formattedSynthesis =
      `[Parliament concluded on "${debateSession.config.topic}"] Verdict: ${debateSession.verdict ?? "CONSENSUS_REACHED"}\n` +
      `The council's synthesis: ${synthesis.slice(0, 300)}\n` +
      (minorityContent ? `Key dissent: ${minorityContent.slice(0, 150)}\n` : "");

    try {
      ctx.contextPipeline?.setShortTermLayer(
        "parliament_synthesis",
        formattedSynthesis,
        { priority: 117, ttlTurns: 3 },
      );
    } catch (err) {
      log.parliament.warn("ParliamentSubsystem.run: context pipeline update failed", err);
    }

    // ─── DNA evolution — NEUTRAL verdict since GoalVerifier is not available here ──
    // GoalVerifier is a private field of GatewayCore; the subsystem runs with
    // NEUTRAL so DNA mutations are skipped until ownerdelegates a verifier.
    try {
      if (ctx.db && participants.length > 0) {
        const synthOwl =
          participants.find(p => (p.persona as unknown as Record<string, unknown>).mentorPersonality)
          ?? participants.find(p => p.persona.name === "Noctua")
          ?? participants.find(p => (p.persona as unknown as Record<string, unknown>).specialty === "architect")
          ?? participants[0];
        const challOwl = participants.find(p => p !== synthOwl);
        await updateParliamentDNA(
          synthOwl,
          challOwl,
          participants,
          debateSession.verdict ?? "",
          worthinessCategory,
          ctx.db,
          "NEUTRAL",
        );
      }
    } catch (err) {
      log.parliament.warn("ParliamentSubsystem.run: DNA update failed", err);
    }

    const response: GatewayResponse = {
      content: `${synthesis || "Parliament concluded without synthesis."}\n\n#Parliament`,
      owlName: ctx.owl.persona.name,
      owlEmoji: ctx.owl.persona.emoji,
      toolsUsed: ["parliament"],
    };

    log.parliament.debug("ParliamentSubsystem.run: exit", { sessionId: message.sessionId });
    return response;
  }
}
