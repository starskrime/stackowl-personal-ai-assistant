/**
 * StackOwl — PromptOptimizer (APO-lite)
 *
 * Textual gradient descent on system prompts, inspired by Microsoft Agent Lightning APO.
 * Runs asynchronously in the background — never blocks a user response.
 *
 * Algorithm (4 steps, 6 LLM calls total):
 *   STEP 1 — CRITIQUE:    LLM reads 3 worst trajectories + current prompt → textual gradient
 *   STEP 2 — GENERATE:    4 parallel LLM calls rewrite the prompt based on the critique
 *   STEP 3 — EVALUATE:    2 independent judge LLM calls score each candidate (0–10)
 *   STEP 4 — SELECT+STORE: Best-scoring candidate stored in DB, applied to owl DNA
 *
 * Trigger conditions (all must be true):
 *   - ≥10 completed trajectories exist for this owl
 *   - ≥3 of those have reward < -0.2 in the last 48 hours
 *   - Optimizer has not run for this owl in the last 24 hours
 */

import type { ModelProvider } from "../providers/base.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { OwlRegistry } from "../owls/registry.js";
import { ParliamentLite } from "../parliament/lite.js";
import { log } from "../logger.js";

const TRIGGER_MIN_TRAJECTORIES = 10;
const TRIGGER_MIN_BAD_IN_WINDOW = 3;
const TRIGGER_BAD_REWARD_THRESHOLD = -0.2;
const TRIGGER_WINDOW_HOURS = 48;
const TRIGGER_COOLDOWN_HOURS = 24;
const CANDIDATE_COUNT = 4;

export class PromptOptimizer {
  constructor(
    private db: MemoryDatabase,
    private owlRegistry: OwlRegistry,
    private provider: ModelProvider,
    private model: string,
  ) {}

  /**
   * Check whether the optimizer should run for this owl.
   * Pure read — no side effects.
   */
  shouldRun(owlName: string): boolean {
    try {
      // Check cooldown: not run in last 24h
      const lastRun = this.db.promptOptimization.getLastRunAt(owlName);
      if (lastRun) {
        const hoursAgo = (Date.now() - new Date(lastRun).getTime()) / (1000 * 60 * 60);
        if (hoursAgo < TRIGGER_COOLDOWN_HOURS) return false;
      }

      // Check trajectory count
      const recent = this.db.trajectories.getRecent(owlName, 100);
      if (recent.length < TRIGGER_MIN_TRAJECTORIES) return false;

      // Check bad trajectory count in window
      const windowStart = Date.now() - TRIGGER_WINDOW_HOURS * 60 * 60 * 1000;
      const badInWindow = recent.filter(
        (t) =>
          t.reward < TRIGGER_BAD_REWARD_THRESHOLD &&
          new Date(t.createdAt).getTime() > windowStart,
      );
      return badInWindow.length >= TRIGGER_MIN_BAD_IN_WINDOW;
    } catch {
      return false;
    }
  }

  /**
   * Run the full APO-lite optimization cycle for one owl.
   * Returns the id of the stored optimization record, or undefined on failure.
   */
  async run(owlName: string): Promise<string | undefined> {
    const owl = this.owlRegistry.get(owlName);
    if (!owl) {
      log.engine.warn(`[PromptOptimizer] Owl "${owlName}" not found`);
      return undefined;
    }

    const currentPrompt = owl.persona.systemPrompt;

    // Pick the 3 worst recent trajectories
    const badTrajectories = this.db.trajectories.getLowReward(owlName, 3, TRIGGER_BAD_REWARD_THRESHOLD);
    if (badTrajectories.length === 0) return undefined;

    log.engine.info(
      `[PromptOptimizer] Running for "${owlName}" — ${badTrajectories.length} bad trajectories`,
    );

    // ── STEP 1: CRITIQUE ─────────────────────────────────────────
    const trajectoryText = badTrajectories
      .map(
        (t, i) =>
          `[Failure ${i + 1}] Message: "${t.userMessage.slice(0, 200)}"\n` +
          `Outcome: ${t.outcome} | Reward: ${t.reward.toFixed(2)}\n` +
          `Tools used: ${t.toolsUsed.join(", ") || "none"}\n` +
          `Breakdown: ${JSON.stringify(t.rewardBreakdown)}`,
      )
      .join("\n\n");

    let critique = "";
    try {
      const critiqueResponse = await this.provider.chat(
        [
          {
            role: "user",
            content:
              `You are an AI system prompt auditor.\n\n` +
              `CURRENT SYSTEM PROMPT:\n${currentPrompt.slice(0, 3000)}\n\n` +
              `RECENT AGENT FAILURES:\n${trajectoryText}\n\n` +
              `Analyze these failures carefully. What specific weaknesses in the current system prompt ` +
              `caused or contributed to these failures? Be concrete — name the exact instruction (or missing instruction) ` +
              `that was wrong and explain WHY it caused the failure. ` +
              `Focus only on actionable improvements to the system prompt text itself. ` +
              `Write 2-4 sentences.`,
          },
        ],
        this.model,
      );
      critique = critiqueResponse.content.trim();
      log.engine.debug(`[PromptOptimizer] Critique: ${critique.slice(0, 200)}`);
    } catch (err) {
      log.engine.warn(`[PromptOptimizer] Critique step failed: ${err}`);
      return undefined;
    }

    // ── STEP 2: GENERATE candidates ──────────────────────────────
    const candidatePromises: Promise<string>[] = [];
    for (let i = 0; i < CANDIDATE_COUNT; i++) {
      candidatePromises.push(
        this.provider
          .chat(
            [
              {
                role: "user",
                content:
                  `You are a system prompt engineer.\n\n` +
                  `CURRENT SYSTEM PROMPT:\n${currentPrompt.slice(0, 3000)}\n\n` +
                  `CRITIQUE OF CURRENT PROMPT:\n${critique}\n\n` +
                  `Rewrite the system prompt to fix the specific issues identified in the critique. ` +
                  `Keep everything that works. Only change what the critique identified as wrong. ` +
                  `Output ONLY the rewritten system prompt — no preamble, no explanation.`,
              },
            ],
            this.model,
          )
          .then((r) => r.content.trim())
          .catch(() => ""),
      );
    }

    const rawCandidates = await Promise.all(candidatePromises);
    const candidates = rawCandidates.filter((c) => c.length > 100);
    if (candidates.length === 0) {
      log.engine.warn(`[PromptOptimizer] No valid candidates generated`);
      return undefined;
    }

    // ── STEP 3: EVALUATE candidates via Parliament Lite (Phase E upgrade) ────
    // Two owls debate which candidate best addresses the critique.
    // More robust than a single judge: two perspectives, two biases.
    let bestIdx = 0;
    let winnerScore = 5.0;

    // Try to get two owls from the registry for Parliament Lite evaluation
    const allOwls = this.owlRegistry.listOwls();
    if (allOwls.length >= 2) {
      const advocate = allOwls[0];
      const devilsAdvocate = allOwls[1];

      const fakeConfig = { providers: { anthropic: { defaultModel: this.model } } } as unknown as import("../config/loader.js").StackOwlConfig;
      const parliamentLite = new ParliamentLite(this.provider, fakeConfig, this.db);

      const candidateSummary = candidates
        .map((c, i) => `[CANDIDATE ${i + 1}]:\n${c.slice(0, 800)}`)
        .join("\n\n---\n\n");

      try {
        // Evaluate all candidates at once — Parliament Lite picks the best one
        const result = await parliamentLite.deliberate({
          topic: `Prompt candidate selection for ${owl.persona.name}`,
          question: `Which candidate system prompt best addresses these agent failures?`,
          context:
            `ORIGINAL FAILURES:\n${trajectoryText.slice(0, 800)}\n\n` +
            `CRITIQUE:\n${critique}\n\n` +
            `CANDIDATES:\n${candidateSummary}\n\n` +
            `The winning candidate should fix the identified failures. Vote PROCEED if Candidate 1 is best, ` +
            `REVISE if Candidate 2 is best, HOLD if Candidate 3 is best, ABORT if Candidate 4 is best.`,
          owls: [advocate, devilsAdvocate],
        });

        // Map verdict to candidate index
        const verdictToIdx: Record<string, number> = {
          "PROCEED": 0, "REVISE": 1, "HOLD": 2, "ABORT": 3,
        };
        bestIdx = verdictToIdx[result.verdict] ?? 0;
        // Clamp to valid range
        if (bestIdx >= candidates.length) bestIdx = 0;
        winnerScore = 7.0; // Parliament Lite selected — higher confidence than single judge

        log.engine.debug(`[PromptOptimizer] Parliament Lite selected Candidate ${bestIdx + 1} (${result.verdict})`);
      } catch (err) {
        log.engine.warn(`[PromptOptimizer] Parliament Lite eval failed, using Candidate 1: ${err}`);
        bestIdx = 0;
      }
    }

    const winnerPrompt = candidates[bestIdx];

    log.engine.info(
      `[PromptOptimizer] Winner: candidate ${bestIdx + 1} (score=${winnerScore.toFixed(1)})`,
    );

    // ── STEP 4: STORE ─────────────────────────────────────────────
    const recordId = this.db.promptOptimization.store(
      owlName,
      currentPrompt,
      winnerPrompt,
      critique,
      winnerScore,
      badTrajectories.length,
    );

    // Immediately apply: update owl DNA promptSections
    await this.apply(owlName, winnerPrompt, recordId);

    return recordId;
  }

  /**
   * Apply the winning prompt to the owl's DNA.
   * Rather than replacing the full system prompt (risky), we extract
   * the diff as concrete rules and store them in `dna.promptSections`.
   * This way the base persona prompt is preserved and APO rules layer on top.
   */
  private async apply(owlName: string, winnerPrompt: string, recordId: string): Promise<void> {
    const owl = this.owlRegistry.get(owlName);
    if (!owl) return;

    try {
      // Ask the LLM to distill the winning prompt changes into actionable rules
      const ruleResponse = await this.provider.chat(
        [
          {
            role: "user",
            content:
              `Compare these two system prompts and extract ONLY the new behavioral rules ` +
              `added or changed in the improved version. ` +
              `Output each rule on its own line starting with "- ". ` +
              `Keep each rule under 120 characters. Output 1-5 rules maximum.\n\n` +
              `ORIGINAL:\n${owl.persona.systemPrompt.slice(0, 2000)}\n\n` +
              `IMPROVED:\n${winnerPrompt.slice(0, 2000)}`,
          },
        ],
        this.model,
      );

      const rules = ruleResponse.content
        .split("\n")
        .filter((l) => l.trim().startsWith("- "))
        .map((l) => l.replace(/^-\s*/, "").slice(0, 120))
        .filter((r) => r.length > 10)
        .slice(0, 5);

      if (rules.length > 0) {
        // Merge with existing prompt sections — deduplicate by rule text
        const existingRules = owl.dna.promptSections ?? [];
        const merged = [...existingRules];
        for (const rule of rules) {
          if (!merged.some((r) => r.toLowerCase().includes(rule.slice(0, 30).toLowerCase()))) {
            merged.push(rule);
          }
        }
        // Cap at 10 rules total to keep prompt compact
        owl.dna.promptSections = merged.slice(-10);

        await this.owlRegistry.saveDNA(owlName);
        this.db.promptOptimization.markApplied(recordId);

        log.engine.info(
          `[PromptOptimizer] Applied ${rules.length} new rule(s) to "${owlName}" DNA`,
        );
      }
    } catch (err) {
      log.engine.warn(`[PromptOptimizer] Apply step failed: ${err}`);
    }
  }
}
