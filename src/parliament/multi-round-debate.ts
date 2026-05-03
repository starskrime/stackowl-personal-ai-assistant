/**
 * StackOwl — Multi-Round Debate Manager
 *
 * Manages the 3-round Parliament debate protocol:
 * - Round 1: Initial positions (sequential streaming)
 * - Round 2: Cross-examination (challenger targets specific owls)
 * - Round 3: Synthesis (mentor owl produces final verdict)
 */

import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { OwlInstance } from "../owls/persona.js";
import { OwlEngine } from "../engine/runtime.js";
import type { ParliamentSession, ParliamentPhase, OwlPosition, OwlChallenge } from "./protocol.js";
import { buildPerspectivePrompt } from "./perspectives.js";
import type { PerspectiveOverlay, PerspectiveRole } from "./perspectives.js";
import { assignPerspectives } from "./perspectives.js";
import { DiversityFilter } from "./diversity-filter.js";
import type { IntelligenceRouter } from "../intelligence/router.js";

// ─── Types ─────────────────────────────────────────────────────

export interface DebateCallbacks {
  onRoundStart?: (round: number, phase: ParliamentPhase) => Promise<void>;
  onPositionReady?: (position: OwlPosition) => Promise<void>;
  onChallengeReady?: (challenge: OwlChallenge) => Promise<void>;
  onSynthesisReady?: (synthesis: string, verdict: string) => Promise<void>;
}

export interface MultiRoundDebateConfig {
  topic: string;
  participants: OwlInstance[];
  perspectives: Map<string, PerspectiveOverlay>;
  contextMessages: { role: string; content: string }[];
  callbacks?: DebateCallbacks;
  maxRounds?: number;
}

// ─── MultiRoundDebateManager ────────────────────────────────────

export class MultiRoundDebateManager {
  private engine: OwlEngine;
  private diversityFilter?: DiversityFilter;

  constructor(
    private provider: ModelProvider,
    private config: StackOwlConfig,
    router?: IntelligenceRouter,
    providers?: Map<string, ModelProvider>,
  ) {
    this.engine = new OwlEngine();
    if (router && providers) {
      this.diversityFilter = new DiversityFilter(router, providers);
    }
  }

  /**
   * Run the full 3-round debate protocol.
   */
  async runDebate(session: ParliamentSession): Promise<void> {
    // Get perspectives from config.perspectiveRoles (same as orchestrator)
    const perspectives = assignPerspectives(
      session.config.participants,
      session.config.perspectiveRoles as PerspectiveRole[] | undefined,
    );

    await this.runRound1(session, perspectives);
    await this.runRound2(session, perspectives);
    await this.runRound3(session, perspectives);
  }

  /**
   * Round 1: Initial Positions
   *
   * Each owl delivers their position sequentially so they stream to the user.
   * Format: [FOR|AGAINST|CONDITIONAL|NEUTRAL|ANALYSIS] + argument
   */
  async runRound1(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
  ): Promise<void> {
    session.phase = "round1_position";
    const cb = session.config.callbacks;

    if (session.config.callbacks?.onRoundStart) {
      await session.config.callbacks.onRoundStart(1, "round1_position");
    }

    const tags = ["FOR", "AGAINST", "CONDITIONAL", "NEUTRAL", "ANALYSIS"] as const;

    const positionPromises = session.config.participants.map(async (owl) => {
      const perspective = perspectives.get(owl.persona.name);
      const roleLabel = perspective
        ? `${perspective.label} ${perspective.emoji}`
        : owl.persona.type;

      let prompt =
        `PARLIAMENT TOPIC: ${session.config.topic}\n\n` +
        `Task: Provide your initial hardline position on this topic based on your role as ${roleLabel}. ` +
        `State exactly one of these positions at the very beginning of your response: [FOR, AGAINST, CONDITIONAL, NEUTRAL, ANALYSIS]. ` +
        `Then provide a single paragraph (max 4 sentences) arguing your case. Be opinionated.`;

      if (perspective) {
        prompt = buildPerspectivePrompt(prompt, perspective);
      }

      const sessionHistory = session.config.contextMessages.map((m) => ({
        role: m.role as import("../providers/base.js").MessageRole,
        content: m.content,
      }));

      const response = await this.engine.run(prompt, {
        provider: this.provider,
        owl,
        sessionHistory,
        config: this.config,
      });

      // Extract position tag
      let positionScore: OwlPosition["position"] = "ANALYSIS";
      for (const tag of tags) {
        if (
          response.content.toUpperCase().includes(`[${tag}]`) ||
          response.content.startsWith(tag)
        ) {
          positionScore = tag;
          break;
        }
      }

      // Clean content
      let cleanArg = response.content;
      for (const tag of tags) {
        cleanArg = cleanArg
          .replace(`[${tag}]`, "")
          .replace(new RegExp(`^${tag}[:\\s]*`, "i"), "")
          .trim();
      }

      const position: OwlPosition = {
        owlName: owl.persona.name,
        owlEmoji: perspective?.emoji || owl.persona.emoji,
        position: positionScore,
        argument: cleanArg,
      };

      return { position, owl };
    });

    const results = await Promise.allSettled(positionPromises);

    for (let i = 0; i < results.length; i++) {
      const result = results[i];
      const owl = session.config.participants[i];
      const perspective = perspectives.get(owl.persona.name);

      let position: OwlPosition;
      if (result.status === "fulfilled") {
        position = result.value.position;
      } else {
        // Fallback: neutral position on error
        position = {
          owlName: owl.persona.name,
          owlEmoji: perspective?.emoji || owl.persona.emoji,
          position: "NEUTRAL",
          argument: "No position available.",
        };
      }

      session.positions.push(position);

      if (cb?.onPositionReady) {
        await cb.onPositionReady(position);
      }
    }

    // Run DiversityFilter to identify the top-2 most-disagreeing positions
    if (this.diversityFilter && session.positions.length >= 2) {
      try {
        const { pair, reasoning } = await this.diversityFilter.selectDivergingPair(session.positions);
        session.diversePair = pair;
        session.diversityReasoning = reasoning || undefined;
      } catch {
        session.diversePair = [
          session.positions[0],
          session.positions[session.positions.length - 1],
        ];
      }
    }
  }

  /**
   * Round 2: Cross-Examination
   *
   * The designated challenger (devils_advocate or highest challenge level)
   * challenges specific owls' positions.
   */
  async runRound2(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
  ): Promise<void> {
    session.phase = "round2_challenge";
    const cb = session.config.callbacks;

    if (session.config.callbacks?.onRoundStart) {
      await session.config.callbacks.onRoundStart(2, "round2_challenge");
    }

    // Use only the diverging pair for Round 2 to prevent context pollution
    const targetPositions = session.diversePair ?? session.positions;
    const allPositions = targetPositions
      .map((p) => {
        const persp = perspectives.get(p.owlName);
        const label = persp ? `${persp.label}` : p.owlName;
        return `- ${label} [${p.position}]: ${p.argument}`;
      })
      .join("\n\n");

    // Pick the single most contrary/challenging owl
    const challengeRank: Record<string, number> = {
      relentless: 3,
      high: 2,
      medium: 1,
      low: 0,
    };

    let challenger = session.config.participants.find(
      (o) => perspectives.get(o.persona.name)?.role === "devils_advocate",
    );
    if (!challenger) {
      challenger =
        session.config.participants
          .filter((o) => o.dna.evolvedTraits.challengeLevel !== "low")
          .sort(
            (a, b) =>
              (challengeRank[b.dna.evolvedTraits.challengeLevel] ?? 0) -
              (challengeRank[a.dna.evolvedTraits.challengeLevel] ?? 0),
          )[0] ?? session.config.participants[0];
    }

    const perspective = perspectives.get(challenger.persona.name);
    let prompt =
      `PARLIAMENT TOPIC: ${session.config.topic}\n\n` +
      `The two most fundamentally disagreeing positions are:\n${allPositions}\n\n` +
      `Task: Review these positions. If you see a gaping hole in someone's logic, a missed risk, or a naive assumption, ` +
      `call them out specifically. Name the participant you are challenging. Keep it to 2-3 sentences. ` +
      `If both positions are mostly reasonable, play devil's advocate against the stronger one.`;

    if (perspective) {
      prompt = buildPerspectivePrompt(prompt, perspective);
    }

    const sessionHistory = session.config.contextMessages.map((m) => ({
      role: m.role as import("../providers/base.js").MessageRole,
      content: m.content,
    }));

    const response = await this.engine.run(prompt, {
      provider: this.provider,
      owl: challenger,
      sessionHistory,
      config: this.config,
    });

    // Try to figure out who they challenged
    let targetOwl = "";
    for (const p of session.config.participants) {
      if (
        p.persona.name !== challenger.persona.name &&
        response.content.includes(p.persona.name)
      ) {
        targetOwl = p.persona.name;
        break;
      }
    }
    if (!targetOwl) {
      for (const [owlName, persp] of perspectives) {
        if (
          owlName !== challenger.persona.name &&
          response.content.includes(persp.label)
        ) {
          targetOwl = owlName;
          break;
        }
      }
    }
    if (!targetOwl) targetOwl = "Group";

    const challenge: OwlChallenge = {
      owlName: challenger.persona.name,
      targetOwl,
      challengeContent: response.content,
    };

    session.challenges.push(challenge);

    if (cb?.onChallengeReady) {
      await cb.onChallengeReady(challenge);
    }
  }

  /**
   * Round 3: Synthesis
   *
   * The synthesizer owl (mentor preferred, then Noctua, then architect)
   * produces a final verdict with recommendation.
   */
  async runRound3(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
  ): Promise<void> {
    session.phase = "round3_synthesis";
    const cb = session.config.callbacks;

    if (session.config.callbacks?.onRoundStart) {
      await session.config.callbacks.onRoundStart(3, "round3_synthesis");
    }

    // Prefer mentor perspective for synthesis, then Noctua, then architect
    let synthesizer = session.config.participants.find(
      (o) => perspectives.get(o.persona.name)?.role === "mentor",
    );
    if (!synthesizer)
      synthesizer = session.config.participants.find(
        (o) => o.persona.name === "Noctua",
      );
    if (!synthesizer)
      synthesizer = session.config.participants.find(
        (o) => o.persona.type === "architect",
      );
    if (!synthesizer) synthesizer = session.config.participants[0];

    const positionsText = session.positions
      .map((p) => {
        const persp = perspectives.get(p.owlName);
        const label = persp ? `${persp.label} (${p.owlName})` : p.owlName;
        return `- ${label} [${p.position}]: ${p.argument}`;
      })
      .join("\n");

    const challengesText = session.challenges
      .map((c) => {
        const persp = perspectives.get(c.owlName);
        const label = persp ? `${persp.label}` : c.owlName;
        return `- ${label} challenged ${c.targetOwl}: ${c.challengeContent}`;
      })
      .join("\n");

    const diversityContext = session.diversePair
      ? `\nKey disagreement: ${session.diversePair[0].owlName} vs ${session.diversePair[1].owlName}` +
        (session.diversityReasoning ? ` — ${session.diversityReasoning}` : "")
      : "";

    const history =
      `TOPIC: ${session.config.topic}\n\nPositions:\n${positionsText}\n\nChallenges:\n${challengesText}` +
      diversityContext;

    const prompt =
      `Here is the transcript of a Parliament session:\n\n${history}\n\n` +
      `Task: Synthesize this debate into a final verdict. ` +
      `1. Provide a clear recommendation (e.g., PROCEED, HOLD, ABORT, REVISE). ` +
      `2. Summarize the critical tradeoffs identified by the group. ` +
      `3. Suggest the concrete next step. ` +
      `Do NOT give a non-answer. Make a call even if the group is divided.`;

    const sessionHistory = session.config.contextMessages.map((m) => ({
      role: m.role as import("../providers/base.js").MessageRole,
      content: m.content,
    }));

    const response = await this.engine.run(prompt, {
      provider: this.provider,
      owl: synthesizer,
      sessionHistory,
      config: this.config,
    });

    session.synthesis = response.content;

    const match = response.content.match(
      /\b(PROCEED|HOLD|ABORT|REVISE|APPROVE|REJECT)\b/i,
    );
    session.verdict = match ? match[1].toUpperCase() : "CONSENSUS_REACHED";

    if (cb?.onSynthesisReady) {
      await cb.onSynthesisReady(session.synthesis, session.verdict ?? "CONSENSUS_REACHED");
    }
  }
}