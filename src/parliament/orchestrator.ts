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

export class ParliamentOrchestrator {
  private pelletGenerator: PelletGenerator;
  private pelletStore: PelletStore;
  private db?: MemoryDatabase;
  private readonly multiRoundDebate: MultiRoundDebateManager;

  constructor(
    provider: ModelProvider,
    config: StackOwlConfig,
    pelletStore: PelletStore,
    _toolRegistry?: ToolRegistry,
    db?: MemoryDatabase,
  ) {
    this.pelletStore = pelletStore;
    this.db = db;
    this.pelletGenerator = new PelletGenerator(makeProviderRouter(provider));
    this.multiRoundDebate = new MultiRoundDebateManager(provider, config);
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
        const pastVerdicts = this.db.parliamentVerdicts.findRelated(config.topic, 5);
        if (pastVerdicts.length > 0) {
          const validatedVerdicts = pastVerdicts.filter((v) => v.validated);
          if (validatedVerdicts.length > 0) {
            const verdictBlock =
              "\n[Past Parliament decisions on similar topics]:\n" +
              validatedVerdicts
                .map(
                  (v) =>
                    `  • "${v.topic.slice(0, 80)}" → ${v.verdict}` +
                    (v.validationSignal ? ` → ${v.validationSignal.toUpperCase()}` : "") +
                    (v.synthesis ? `: ${v.synthesis.slice(0, 100)}` : ""),
                )
                .join("\n") + "\n";
            session.config.contextMessages = [
              ...session.config.contextMessages,
              { role: "system" as const, content: verdictBlock },
            ];
            log.engine.info(
              `[Parliament] Injected ${validatedVerdicts.length} past verdict(s) for recall`,
            );
          }
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

      // Automatically generate a Pellet from this session
      const mdTranscript = this.formatSessionMarkdown(session, perspectives);
      try {
        const pellet = await this.pelletGenerator.generate(
          mdTranscript,
          `Parliament Session: ${config.topic}`,
        );
        if (pellet) {
          await this.pelletStore.save(pellet);
          log.engine.info(`[Parliament] Saved Knowledge Pellet: ${pellet.id}.md`);
        }
      } catch (pelletError) {
        log.engine.error(
          `[Parliament] Failed to generate pellet: ${pelletError}`,
        );
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
