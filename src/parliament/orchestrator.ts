/**
 * StackOwl — Parliament Orchestrator
 *
 * Runs multi-owl brainstorming sessions with live streaming.
 * Now supports perspective roles and streaming callbacks.
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";
import type {
  ParliamentConfig,
  ParliamentSession,
} from "./protocol.js";
import { PelletGenerator, makeProviderRouter } from "../pellets/generator.js";
import type { PelletStore } from "../pellets/store.js";
import { assignPerspectives } from "./perspectives.js";
import type { PerspectiveOverlay } from "./perspectives.js";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";
import { MultiRoundDebateManager } from "./multi-round-debate.js";
import { withSpan } from "../infra/observability/context.js";
import { OwlEngine } from "../engine/runtime.js";
import type { OwlInstance } from "../owls/persona.js";
import { createDefaultDNA } from "../owls/persona.js";

export interface ValidatorResult {
  signal: "VALID" | "INVALID" | "UNCERTAIN";
  reason: string;
}

export function parseValidatorResponse(content: string): ValidatorResult {
  const upper = content.toUpperCase();
  let signal: ValidatorResult["signal"] = "UNCERTAIN";
  if (upper.includes("INVALID") && !upper.includes("NOT INVALID")) signal = "INVALID";
  else if (upper.includes("VALID") && !upper.includes("NOT VALID")) signal = "VALID";

  const reasonMatch = content.match(/(?:VALID|INVALID|UNCERTAIN)[^\n]*?[—–-]\s*(.+)/i);
  const reason = reasonMatch ? reasonMatch[1].trim() : content.slice(0, 200).trim();
  return { signal, reason };
}

export class ParliamentOrchestrator {
  private pelletGenerator: PelletGenerator;
  private pelletStore: PelletStore;
  private db?: MemoryDatabase;
  private readonly multiRoundDebate: MultiRoundDebateManager;
  private readonly provider: ModelProvider;
  private readonly config: StackOwlConfig;

  constructor(
    provider: ModelProvider,
    config: StackOwlConfig,
    pelletStore: PelletStore,
    _toolRegistry?: ToolRegistry,
    db?: MemoryDatabase,
  ) {
    this.provider = provider;
    this.config = config;
    this.pelletStore = pelletStore;
    this.db = db;
    this.pelletGenerator = new PelletGenerator(makeProviderRouter(provider));
    this.multiRoundDebate = new MultiRoundDebateManager(provider, config);
  }

  private async runAdversarialValidator(
    session: ParliamentSession,
  ): Promise<ValidatorResult> {
    const engine = new OwlEngine();
    const validatorOwl: OwlInstance = {
      persona: {
        name: "Validator",
        type: "specialist",
        emoji: "🔍",
        challengeLevel: "relentless",
        specialties: ["logic", "critical thinking"],
        traits: ["skeptical"],
        systemPrompt:
          "You are an adversarial logic validator. Your job is to find flaws in reasoning, not to agree. Be brief and ruthless.",
        sourcePath: "",
      },
      dna: createDefaultDNA("Validator", "relentless"),
    } as OwlInstance;

    const positionsText = session.positions
      .map((p) => `- ${p.owlName} [${p.position}]: ${p.argument}`)
      .join("\n");

    const prompt =
      `You are validating a Parliament verdict.\n\n` +
      `TOPIC: ${session.config.topic}\n\n` +
      `POSITIONS:\n${positionsText}\n\n` +
      `VERDICT: ${session.verdict}\n\n` +
      `SYNTHESIS: ${session.synthesis?.slice(0, 600) ?? "(none)"}\n\n` +
      (session.agentCitations ? `CITED BY SYNTHESIZER: ${session.agentCitations}\n\n` : "") +
      `Task: Does the verdict logically follow from the positions and cited reasoning? ` +
      `Output EXACTLY one of: VALID, INVALID, or UNCERTAIN. ` +
      `Follow it with an em-dash and ONE sentence explaining why. ` +
      `Example: "VALID — the cited position directly supports the PROCEED recommendation."`;

    try {
      const response = await engine.run(prompt, {
        provider: this.provider,
        owl: validatorOwl,
        sessionHistory: [],
        config: this.config,
      });
      return parseValidatorResponse(response.content);
    } catch (err) {
      log.parliament.warn("[Parliament] Adversarial validator failed — defaulting UNCERTAIN", err);
      return { signal: "UNCERTAIN", reason: "Validator call failed." };
    }
  }

  /**
   * Start and run a full Parliament session.
   */
  async convene(config: ParliamentConfig): Promise<ParliamentSession> {
    return withSpan("parliament.convene", async () => {
    const session: ParliamentSession = {
      id: uuidv4(),
      config,
      phase: "setup",
      positions: [],
      challenges: [],
      startedAt: Date.now(),
    };

    if (config.participants.length < 2) {
      throw new Error("A Parliament requires at least 2 owls.");
    }

    // Assign perspective roles to owls
    const perspectives = assignPerspectives(
      config.participants,
      config.perspectiveRoles,
    );

    log.engine.info(
      `[Parliament] Convened: "${config.topic}" with ${config.participants.length} owls`,
    );

    // ── E3: Parliament recall — inject past verdicts on related topics ───
    // Parliament now enters debates knowing its own track record on similar questions.
    if (this.db) {
      try {
        const pastVerdicts = this.db.parliamentVerdicts.findRelated(config.topic, 2);
        if (pastVerdicts.length > 0) {
          const verdictBlock =
            "\n[Past Parliament decisions on similar topics (highest confidence first)]:\n" +
            pastVerdicts
              .map(
                (v) =>
                  `  • "${v.topic.slice(0, 80)}" → ${v.verdict}` +
                  ` (confidence: ${v.confidenceScore.toFixed(2)})` +
                  (v.agentCitations ? ` | Cited: ${v.agentCitations.slice(0, 80)}` : "") +
                  (v.synthesis ? `: ${v.synthesis.slice(0, 100)}` : ""),
              )
              .join("\n") + "\n";
          session.config.contextMessages = [
            ...session.config.contextMessages,
            { role: "system" as const, content: verdictBlock },
          ];
          log.engine.info(
            `[Parliament] Injected ${pastVerdicts.length} past verdict(s) for recall (top-2 by confidence)`,
          );
        }
      } catch (err) {
        log.parliament.warn("parliament verdict recall failed", err);
      }
    }

    // Pre-load cross-owl learnings related to this topic (Phase 6)
    // Injects shared knowledge from previous Parliament sessions and remember() calls.
    if (this.db) {
      try {
        const learnings = this.db.owlLearnings.search(config.topic, 5);
        if (learnings.length > 0) {
          const knowledgeBlock =
            "\n[Pre-loaded cross-owl knowledge on this topic]:\n" +
            learnings.map((l) => `  - ${l.learning} (from ${l.owlName})`).join("\n") +
            "\n";
          // Inject into context messages so all owls see it
          session.config.contextMessages = [
            ...session.config.contextMessages,
            { role: "system" as const, content: knowledgeBlock },
          ];
          log.engine.info(
            `[Parliament] Injected ${learnings.length} cross-owl learnings for "${config.topic}"`,
          );
        }
      } catch (err) {
        log.parliament.warn("parliament cross-owl learnings inject failed", err);
      }
    }

    try {
      await this.multiRoundDebate.runDebate(session);

      session.completedAt = Date.now();
      session.phase = "complete";

      // ── Adversarial validator ──────────────────────────────────────────
      const HIGH_STAKES_VERDICTS = new Set(["ABORT", "REJECT"]);
      let confidenceScore = 0.6;
      let validatorResult: ValidatorResult = { signal: "UNCERTAIN", reason: "" };

      try {
        validatorResult = await this.runAdversarialValidator(session);
        session.validatorReasoning = validatorResult.reason;

        if (validatorResult.signal === "VALID") {
          confidenceScore = Math.min(0.95, 0.6 + 0.2);
          log.engine.info(`[Parliament] Validator: VALID — ${validatorResult.reason.slice(0, 80)}`);
        } else if (validatorResult.signal === "INVALID") {
          if (HIGH_STAKES_VERDICTS.has(session.verdict ?? "")) {
            log.engine.warn(`[Parliament] Validator INVALID on high-stakes verdict "${session.verdict}" — re-convening`);
            const rotated = [...session.config.participants].reverse();
            const retrySession: ParliamentSession = {
              id: session.id + "-retry",
              config: { ...session.config, participants: rotated },
              phase: "setup",
              positions: [],
              challenges: [],
              startedAt: Date.now(),
            };
            try {
              await this.multiRoundDebate.runDebate(retrySession);
              const retryValidator = await this.runAdversarialValidator(retrySession);
              if (retryValidator.signal === "VALID") {
                session.synthesis = retrySession.synthesis;
                session.verdict = retrySession.verdict;
                session.agentCitations = retrySession.agentCitations;
                session.validatorReasoning = retryValidator.reason;
                confidenceScore = 0.75;
                log.engine.info(`[Parliament] Retry VALID — adopted retry verdict "${session.verdict}"`);
              } else {
                session.verdict = "PARLIAMENT_INCONCLUSIVE";
                session.synthesis = `Original verdict was rejected by the validator. Retry also inconclusive. Reason: ${retryValidator.reason}`;
                confidenceScore = 0.1;
                log.engine.warn("[Parliament] Retry also invalid — verdict set to PARLIAMENT_INCONCLUSIVE");
              }
            } catch (retryErr) {
              log.parliament.warn("[Parliament] Re-convene failed", retryErr);
              confidenceScore = 0.2;
            }
          } else {
            confidenceScore = 0.3;
            log.engine.warn(`[Parliament] Validator INVALID on "${session.verdict}" — confidence lowered to 0.3`);
          }
        }
        // UNCERTAIN: keep warm start 0.6
      } catch (err) {
        log.parliament.warn("[Parliament] Validator pipeline error", err);
      }

      // Automatically generate a Pellet from this session
      const mdTranscript = this.formatSessionMarkdown(session, perspectives);
      const pellet = await Promise.race([
        this.pelletGenerator.generate(mdTranscript, `Parliament Session: ${config.topic}`),
        new Promise<null>((_, reject) =>
          setTimeout(() => reject(new Error("pellet generation timeout")), 30_000),
        ),
      ]).catch((err) => {
        log.parliament.warn("pellet generation failed or timed out", { err: (err as Error).message });
        return null;
      });
      if (pellet) {
        await this.pelletStore.save(pellet);
        log.engine.info(`[Parliament] Saved Knowledge Pellet: ${pellet.id}.md`);
      }

      // ── E2: Record verdict in parliament_verdicts for recall + delayed validation ──
      if (this.db && session.verdict) {
        try {
          this.db.parliamentVerdicts.record(
            session.id,
            config.topic,
            session.verdict as import("../memory/db.js").ParliamentVerdictSignal,
            config.participants.map((p) => p.persona.name),
            session.synthesis,
            {
              confidenceScore,
              topicClass: "tactical",
              agentCitations: session.agentCitations,
            },
          );
          log.engine.info(`[Parliament] Recorded verdict "${session.verdict}" for topic: ${config.topic.slice(0, 60)}`);
        } catch (err) {
          log.parliament.warn("parliament verdict record failed", err);
        }
      }

      // Write debate outcomes to owl_learnings for each participant (Phase 6)
      // Enables knowledge sharing: what was debated gets searchable by any owl.
      if (this.db && session.synthesis) {
        try {
          const insight = `Parliament on "${config.topic}" (verdict: ${session.verdict ?? "n/a"}): ${session.synthesis.slice(0, 200)}`;
          for (const owl of config.participants) {
            this.db.owlLearnings.add(owl.persona.name, insight, "insight", session.id, 0.8);
          }
          log.engine.info(
            `[Parliament] Wrote debate outcome to owl_learnings for ${config.participants.length} owls`,
          );
        } catch (err) {
          log.parliament.warn("parliament owl learnings write failed", err);
        }
      }

      return session;
    } catch (error) {
      log.engine.error(`[Parliament] Session failed: ${error}`);
      throw error;
    }
    }); // end withSpan("parliament.convene")
  }

  /**
   * Format a session into readable markdown.
   */
  formatSessionMarkdown(
    session: ParliamentSession,
    perspectives?: Map<string, PerspectiveOverlay>,
  ): string {
    let md = `🏛️ **PARLIAMENT SESSION:** ${session.config.topic}\n`;
    md += `═══════════════════════════════════════════════════════\n\n`;

    for (const p of session.positions) {
      const persp = perspectives?.get(p.owlName);
      const label = persp
        ? `${persp.emoji} ${persp.label} (${p.owlName})`
        : `${p.owlEmoji} **${p.owlName}**`;
      md += `${label}: [${p.position}] — "${p.argument}"\n\n`;
    }

    if (session.challenges.length > 0) {
      md += `*Cross-Examination:*\n`;
      for (const c of session.challenges) {
        const persp = perspectives?.get(c.owlName);
        const label = persp
          ? `${persp.emoji} ${persp.label}`
          : `**${c.owlName}**`;
        md += `> ${label} (to ${c.targetOwl}): "${c.challengeContent}"\n`;
      }
      md += `\n`;
    }

    md += `📋 **PARLIAMENT VERDICT**: [${session.verdict || "PENDING"}]\n`;
    md += `${session.synthesis}\n`;

    return md;
  }
}
