/**
 * StackOwl — Parliament Lite
 *
 * A lightweight 2-owl, 1-round Parliament variant.
 *
 * Full Parliament costs 12–16 LLM calls (3 rounds × 3+ owls + synthesis).
 * Parliament Lite costs 4 calls (2 positions + 1 synthesis + 1 verdict extraction).
 *
 * Used for:
 *   - PromptOptimizer candidate evaluation (Phase C upgrade)
 *   - Synthesis design decisions (Phase E5)
 *   - Any frequent judgment call that benefits from two perspectives
 *
 * Output: a structured verdict (PROCEED | HOLD | ABORT | REVISE) with rationale.
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { ParliamentVerdictSignal } from "../memory/db.js";
import { log } from "../logger.js";

export interface LiteParliamentInput {
  topic: string;
  /** The question to vote on — should have a clear yes/no or PROCEED/HOLD answer */
  question: string;
  /** Supporting context (e.g. the candidates to evaluate, or the synthesis request) */
  context: string;
  /** The two owls to deliberate — first is advocate, second is devil's advocate */
  owls: [OwlInstance, OwlInstance];
  /** Optional: include past Parliament verdicts on similar topics */
  recallContext?: string;
}

export interface LiteOwlVote {
  owlName: string;
  vote: ParliamentVerdictSignal;
  rationale: string;
}

export interface LiteParliamentResult {
  id: string;
  topic: string;
  verdict: ParliamentVerdictSignal;
  synthesis: string;
  votes: LiteOwlVote[];
  createdAt: string;
}

export class ParliamentLite {
  constructor(
    private provider: ModelProvider,
    private config: StackOwlConfig,
    private db?: MemoryDatabase,
  ) {}

  async deliberate(input: LiteParliamentInput): Promise<LiteParliamentResult> {
    const sessionId = uuidv4();
    const [advocate, devil] = input.owls;

    log.engine.info(
      `[ParliamentLite] "${input.topic}" — ${advocate.persona.name} vs ${devil.persona.name}`,
    );

    const recallBlock = input.recallContext
      ? `\nPast Parliament decisions on related topics:\n${input.recallContext}\n\n`
      : "";

    // ── Round 1: Two position calls in parallel ───────────────────
    const baseContext =
      `TOPIC: ${input.topic}\n\n` +
      `QUESTION: ${input.question}\n\n` +
      `CONTEXT:\n${input.context.slice(0, 2000)}\n\n` +
      recallBlock;

    const [advocateRes, devilRes] = await Promise.all([
      this.provider.chat(
        [
          {
            role: "user",
            content:
              baseContext +
              `You are ${advocate.persona.emoji} ${advocate.persona.name} (${advocate.persona.type}).\n` +
              `Role: ADVOCATE — argue in favor of proceeding. Find the strongest reasons to say PROCEED.\n\n` +
              `Vote with exactly one of: [PROCEED] [HOLD] [ABORT] [REVISE]\n` +
              `Then give one sentence explaining why.\n` +
              `Format: VOTE: [PROCEED/HOLD/ABORT/REVISE] — <one-sentence rationale>`,
          },
        ],
        this.config.providers?.anthropic?.defaultModel ?? "claude-haiku-4-5-20251001",
      ).catch(() => ({ content: "VOTE: [HOLD] — Could not evaluate at this time." })),

      this.provider.chat(
        [
          {
            role: "user",
            content:
              baseContext +
              `You are ${devil.persona.emoji} ${devil.persona.name} (${devil.persona.type}).\n` +
              `Role: DEVIL'S ADVOCATE — find the strongest reasons NOT to proceed. Challenge assumptions.\n\n` +
              `Vote with exactly one of: [PROCEED] [HOLD] [ABORT] [REVISE]\n` +
              `Then give one sentence explaining why.\n` +
              `Format: VOTE: [PROCEED/HOLD/ABORT/REVISE] — <one-sentence rationale>`,
          },
        ],
        this.config.providers?.anthropic?.defaultModel ?? "claude-haiku-4-5-20251001",
      ).catch(() => ({ content: "VOTE: [HOLD] — Could not evaluate at this time." })),
    ]);

    // Parse votes
    const votes: LiteOwlVote[] = [
      this.parseVote(advocate.persona.name, advocateRes.content),
      this.parseVote(devil.persona.name, devilRes.content),
    ];

    // ── Round 2: Synthesis ────────────────────────────────────────
    const voteBlock = votes
      .map((v) => `${v.owlName}: ${v.vote} — ${v.rationale}`)
      .join("\n");

    const synthesisRes = await this.provider.chat(
      [
        {
          role: "user",
          content:
            `PARLIAMENT VOTES:\n${voteBlock}\n\n` +
            `TOPIC: ${input.topic}\n` +
            `QUESTION: ${input.question}\n\n` +
            `Synthesize a final verdict as a neutral arbitrator.\n` +
            `Output the final verdict as EXACTLY one of: PROCEED | HOLD | ABORT | REVISE\n` +
            `Then 1-2 sentences explaining the reasoning.\n` +
            `Format: VERDICT: <PROCEED/HOLD/ABORT/REVISE> — <rationale>`,
        },
      ],
      this.config.providers?.anthropic?.defaultModel ?? "claude-haiku-4-5-20251001",
    ).catch(() => ({ content: "VERDICT: HOLD — Could not synthesize verdict." }));

    const verdict = this.parseVerdict(synthesisRes.content);
    const synthesis = synthesisRes.content.replace(/^VERDICT:\s*\w+\s*[—-]\s*/i, "").trim();

    const result: LiteParliamentResult = {
      id: sessionId,
      topic: input.topic,
      verdict,
      synthesis: synthesis.slice(0, 500),
      votes,
      createdAt: new Date().toISOString(),
    };

    // Record verdict in DB for recall + delayed validation
    if (this.db) {
      try {
        this.db.parliamentVerdicts.record(
          sessionId,
          input.topic,
          verdict,
          votes.map((v) => v.owlName),
          synthesis,
        );
      } catch { /* non-fatal */ }
    }

    log.engine.info(`[ParliamentLite] Verdict: ${verdict} — ${synthesis.slice(0, 100)}`);

    return result;
  }

  private parseVote(owlName: string, content: string): LiteOwlVote {
    const VOTE_TAGS: ParliamentVerdictSignal[] = ["PROCEED", "ABORT", "REVISE", "HOLD"];
    let vote: ParliamentVerdictSignal = "HOLD";
    for (const tag of VOTE_TAGS) {
      if (content.toUpperCase().includes(`[${tag}]`) || content.toUpperCase().includes(`VOTE: ${tag}`)) {
        vote = tag;
        break;
      }
    }
    const rationale = content
      .replace(/VOTE:\s*\[?\w+\]?\s*[—-]?\s*/i, "")
      .slice(0, 200)
      .trim();
    return { owlName, vote, rationale };
  }

  private parseVerdict(content: string): ParliamentVerdictSignal {
    const VERDICTS: ParliamentVerdictSignal[] = ["PROCEED", "ABORT", "REVISE", "HOLD"];
    for (const v of VERDICTS) {
      if (content.toUpperCase().includes(`VERDICT: ${v}`) || content.toUpperCase().includes(v)) {
        return v;
      }
    }
    return "HOLD";
  }
}
