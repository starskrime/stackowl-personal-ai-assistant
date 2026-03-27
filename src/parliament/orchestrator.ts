/**
 * StackOwl — Parliament Orchestrator
 *
 * Runs multi-owl brainstorming sessions with live streaming.
 * Now supports perspective roles and streaming callbacks.
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import { OwlEngine } from "../engine/runtime.js";
import type { ToolRegistry } from "../tools/registry.js";
import type {
  ParliamentConfig,
  ParliamentSession,
  OwlPosition,
  ParliamentCallbacks,
} from "./protocol.js";
import { PelletGenerator } from "../pellets/generator.js";
import type { PelletStore } from "../pellets/store.js";
import { assignPerspectives, buildPerspectivePrompt } from "./perspectives.js";
import type { PerspectiveOverlay } from "./perspectives.js";
import { log } from "../logger.js";

export class ParliamentOrchestrator {
  private provider: ModelProvider;
  private engine: OwlEngine;
  private config: StackOwlConfig;
  private pelletGenerator: PelletGenerator;
  private pelletStore: PelletStore;
  private toolRegistry?: ToolRegistry;

  constructor(
    provider: ModelProvider,
    config: StackOwlConfig,
    pelletStore: PelletStore,
    toolRegistry?: ToolRegistry,
  ) {
    this.provider = provider;
    this.config = config;
    this.pelletStore = pelletStore;
    this.toolRegistry = toolRegistry;
    this.engine = new OwlEngine();
    this.pelletGenerator = new PelletGenerator();
  }

  /**
   * Start and run a full Parliament session.
   */
  async convene(config: ParliamentConfig): Promise<ParliamentSession> {
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

    const cb = config.callbacks;

    log.engine.info(
      `[Parliament] Convened: "${config.topic}" with ${config.participants.length} owls`,
    );

    try {
      await this.runRound1(session, perspectives, cb);
      await this.runRound2(session, perspectives, cb);
      await this.runRound3(session, perspectives, cb);

      session.completedAt = Date.now();
      session.phase = "complete";

      // Automatically generate a Pellet from this session
      const mdTranscript = this.formatSessionMarkdown(session, perspectives);
      try {
        const pellet = await this.pelletGenerator.generate(
          mdTranscript,
          `Parliament Session: ${config.topic}`,
          {
            provider: this.provider,
            owl: config.participants[0],
            config: this.config,
          },
        );
        await this.pelletStore.save(pellet);
        log.engine.info(`[Parliament] Saved Knowledge Pellet: ${pellet.id}.md`);
      } catch (pelletError) {
        log.engine.error(
          `[Parliament] Failed to generate pellet: ${pelletError}`,
        );
      }

      return session;
    } catch (error) {
      log.engine.error(`[Parliament] Session failed: ${error}`);
      throw error;
    }
  }

  /**
   * Round 1: Initial Positions
   * Each owl delivers their position — streamed to user as each one completes.
   */
  private async runRound1(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
    cb?: ParliamentCallbacks,
  ): Promise<void> {
    session.phase = "round1_position";

    if (cb?.onRoundStart) {
      await cb.onRoundStart(1, "round1_position");
    }

    // Run positions sequentially so they stream to user one-by-one
    for (const owl of session.config.participants) {
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
        toolRegistry: this.toolRegistry,
      });

      // Extract position tag
      let positionScore: OwlPosition["position"] = "ANALYSIS";
      const tags = [
        "FOR",
        "AGAINST",
        "CONDITIONAL",
        "NEUTRAL",
        "ANALYSIS",
      ] as const;
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

      session.positions.push(position);

      // Stream to user immediately
      if (cb?.onPositionReady) {
        await cb.onPositionReady(position);
      }
    }
  }

  /**
   * Round 2: Cross-Examination (Sequential)
   */
  private async runRound2(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
    cb?: ParliamentCallbacks,
  ): Promise<void> {
    session.phase = "round2_challenge";

    if (cb?.onRoundStart) {
      await cb.onRoundStart(2, "round2_challenge");
    }

    const allPositions = session.positions
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

    // Prefer the owl with devils_advocate perspective, otherwise highest challenge level
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
      `Other participants have stated their positions:\n${allPositions}\n\n` +
      `Task: Review the positions. If you see a gaping hole in someone's logic, a missed risk, or a naive assumption, ` +
      `call them out specifically. Name the participant you are challenging. Keep it to 2-3 sentences. ` +
      `If everyone is mostly right, play devil's advocate.`;

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
    // Also check perspective labels
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

    const challenge = {
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
   */
  private async runRound3(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
    cb?: ParliamentCallbacks,
  ): Promise<void> {
    session.phase = "round3_synthesis";

    if (cb?.onRoundStart) {
      await cb.onRoundStart(3, "round3_synthesis");
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

    const history = `TOPIC: ${session.config.topic}\n\nPositions:\n${positionsText}\n\nChallenges:\n${challengesText}`;

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
      await cb.onSynthesisReady(session.synthesis, session.verdict);
    }
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
