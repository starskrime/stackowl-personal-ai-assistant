/**
 * StackOwl — Owl Evolution Engine
 *
 * Analyzes recent conversation history to evolve an owl's DNA.
 * Owls learn user preferences and adjust their traits over time.
 */

import type { ChallengeLevel } from "./persona.js";
import type { SessionStore } from "../memory/store.js";
import type { OwlRegistry } from "./registry.js";
import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

// Max user+assistant messages sent to the LLM for analysis.
// Keeps evolution prompts predictable in size regardless of session length.
const MAX_EVOLUTION_MESSAGES = 12; // last 6 turns
const MAX_MSG_CHARS = 400; // per message content cap

export class OwlEvolutionEngine {
  private provider: ModelProvider;
  private config: StackOwlConfig;
  private sessionStore: SessionStore;
  private owlRegistry: OwlRegistry;
  private db?: MemoryDatabase;

  constructor(
    provider: ModelProvider,
    config: StackOwlConfig,
    sessionStore: SessionStore,
    owlRegistry: OwlRegistry,
    db?: MemoryDatabase,
  ) {
    this.provider = provider;
    this.config = config;
    this.sessionStore = sessionStore;
    this.owlRegistry = owlRegistry;
    this.db = db;
  }

  /**
   * Apply DNA decay toward neutral (0.5) if more than 7 days have passed since last decay.
   * Prevents stale preferences from dominating forever.
   */
  async applyDecayIfNeeded(owlName: string): Promise<boolean> {
    const owl = this.owlRegistry.get(owlName);
    if (!owl) return false;

    const decayRate = this.config.owlDna?.decayRatePerWeek ?? 0.1;
    if (decayRate <= 0) return false;

    const lastEvolved = owl.dna.lastEvolved
      ? new Date(owl.dna.lastEvolved).getTime()
      : 0;
    const daysSince = (Date.now() - lastEvolved) / (1000 * 60 * 60 * 24);

    if (daysSince < 7) return false;

    const weeksElapsed = Math.floor(daysSince / 7);
    const factor = decayRate * weeksElapsed;
    let changed = false;

    // Decay learnedPreferences toward neutral 0.5
    for (const key of Object.keys(owl.dna.learnedPreferences)) {
      const current = owl.dna.learnedPreferences[key];
      const decayed = current + (0.5 - current) * factor;
      owl.dna.learnedPreferences[key] = Math.max(0, Math.min(1, decayed));
      changed = true;
    }

    // Decay expertiseGrowth toward 0.5
    for (const key of Object.keys(owl.dna.expertiseGrowth)) {
      const current = owl.dna.expertiseGrowth[key];
      const decayed = current + (0.5 - current) * factor;
      owl.dna.expertiseGrowth[key] = Math.max(0, Math.min(1, decayed));
      changed = true;
    }

    if (changed) {
      owl.dna.lastEvolved = new Date().toISOString();
      await this.owlRegistry.saveDNA(owlName);
      log.evolution.info(
        `Applied ${weeksElapsed}-week DNA decay to ${owlName}.`,
      );
    }

    return changed;
  }

  /**
   * Build a summary of recent evolution history to guide the LLM.
   */
  private buildEvolutionHistorySection(owl: {
    dna: {
      evolutionLog: Array<{
        generation: number;
        timestamp: string;
        mutations: string[];
        effectiveness?: number;
      }>;
    };
  }): string {
    const log = owl.dna.evolutionLog;
    if (!log || log.length === 0) {
      return "EVOLUTION HISTORY: No previous mutations yet.\n\n";
    }

    const recent = log.slice(-5).reverse();
    const lines = recent.map((e) => {
      const eff =
        e.effectiveness !== undefined
          ? ` (${(e.effectiveness * 100).toFixed(0)}% effective)`
          : "";
      const mutations = e.mutations.slice(0, 3).join("; ") || "no changes";
      return `Gen ${e.generation}${eff}: ${mutations}`;
    });

    return `EVOLUTION HISTORY (last 5 generations, newest first):\n${lines.join("\n")}\n\n`;
  }

  /**
   * Trigger an evolution pass for a specific owl.
   * Analyzes their most recent session and updates their DNA.
   */
  async evolve(owlName: string): Promise<boolean> {
    const owl = this.owlRegistry.get(owlName);
    if (!owl) throw new Error(`Owl ${owlName} not found.`);

    // 1. Get recent sessions for this owl
    const allSessions = await this.sessionStore.listSessions();

    const owlSessions = allSessions.filter((s) => {
      return s.metadata.owlName === owl.persona.name;
    });

    if (owlSessions.length === 0) {
      log.evolution.info(
        `No sessions found for ${owl.persona.name}. Skipping evolution.`,
      );
      return false;
    }

    // Multi-session analysis: look at the last N sessions (not just the latest)
    const MAX_SESSIONS_TO_ANALYZE = 3;
    const sessionsToAnalyze = owlSessions.slice(0, MAX_SESSIONS_TO_ANALYZE);

    // Filter out sessions that are too short
    const validSessions = sessionsToAnalyze.filter(
      (s) => s.messages.length >= 4,
    );
    if (validSessions.length === 0) {
      log.evolution.info(
        `All recent sessions too short for ${owl.persona.name}. Skipping evolution.`,
      );
      return false;
    }

    log.evolution.info(
      `🧬 ${owl.persona.emoji} ${owl.persona.name} is reflecting on ${validSessions.length} recent session(s)...`,
    );

    // 2. Build transcript from multiple sessions
    const allRelevantMessages: any[] = [];
    for (const session of validSessions) {
      const sampled = this.sampleSessionMessages(session.messages);
      allRelevantMessages.push(...sampled);
    }
    // Cap total messages to avoid overflow
    const relevantMessages = allRelevantMessages.slice(
      -MAX_EVOLUTION_MESSAGES * 2,
    );

    const transcript = relevantMessages
      .map(
        (m) =>
          `[${m.role.toUpperCase()}]: ${(m.content ?? "").slice(0, MAX_MSG_CHARS)}`,
      )
      .join("\n\n");

    const profileSection = "";
    const memorySection = "";

    // Build performance metrics section (Phase 5 — data-driven evolution)
    let performanceSection = "";
    if (this.db) {
      try {
        const perf = this.db.owlPerf.getSummary(owlName, 30);
        if (perf.totalInteractions > 0) {
          performanceSection =
            `\nPERFORMANCE METRICS (last 30 days, ${perf.totalInteractions} interactions):\n` +
            `- User satisfaction (👍 ratio): ${(perf.likeRatio * 100).toFixed(0)}%\n` +
            `- Tool success rate: ${(perf.toolSuccessRate * 100).toFixed(0)}%\n` +
            `- Loop exhaustion rate: ${(perf.loopExhaustionRate * 100).toFixed(0)}%\n` +
            (perf.topTopics.length > 0 ? `- Top requested topics: ${perf.topTopics.slice(0, 5).join(", ")}\n` : "") +
            `\nUse these metrics to inform your mutations:\n` +
            `- Low satisfaction (<60%) → consider adjusting verbosity or challengeLevel\n` +
            `- High tool failure (>20%) → consider noting tool limitations in preferences\n` +
            `- High loop exhaustion (>10%) → reduce complexity, simplify approach\n\n`;
        }
      } catch (err) {
        log.evolution.warn("[Evolution] owlPerf metrics retrieval failed", err);
      }
    }

    // ── Phase D: Trajectory summary (reward-weighted data) ────────
    // Replaces raw impression-based evolution with quantitative reward signals.
    // The LLM now sees which task types produced high vs low reward, not just
    // "did the conversation seem good or bad".
    let trajectorySection = "";
    if (this.db) {
      try {
        const recentTrajectories = this.db.trajectories.getRecent(owlName, 50);
        if (recentTrajectories.length > 0) {
          const avgReward = recentTrajectories.reduce((s, t) => s + t.reward, 0) / recentTrajectories.length;

          // Group by tool pattern to find high vs low reward domains
          const toolRewards = new Map<string, number[]>();
          for (const t of recentTrajectories) {
            const toolKey = t.toolsUsed.slice(0, 2).join("+") || "no_tools";
            const existing = toolRewards.get(toolKey) ?? [];
            existing.push(t.reward);
            toolRewards.set(toolKey, existing);
          }
          const toolAvgs = [...toolRewards.entries()]
            .map(([key, rewards]) => ({
              key,
              avg: rewards.reduce((s, r) => s + r, 0) / rewards.length,
              count: rewards.length,
            }))
            .sort((a, b) => b.avg - a.avg);

          const highReward = toolAvgs.filter((t) => t.avg > 0.3 && t.count >= 2).slice(0, 3);
          const lowReward = toolAvgs.filter((t) => t.avg < -0.1 && t.count >= 2).slice(0, 3);

          trajectorySection = `\nTRAJECTORY ANALYSIS (last ${recentTrajectories.length} interactions):\n`;
          trajectorySection += `- Average reward: ${avgReward >= 0 ? "+" : ""}${avgReward.toFixed(2)} (scale: -1.0 worst → +1.0 best)\n`;

          if (highReward.length > 0) {
            trajectorySection += `- High-reward patterns (reinforce these):\n`;
            for (const t of highReward) {
              trajectorySection += `  • ${t.key}: avg ${t.avg >= 0 ? "+" : ""}${t.avg.toFixed(2)} (${t.count}x)\n`;
            }
          }
          if (lowReward.length > 0) {
            trajectorySection += `- Low-reward patterns (failure domains — add targeted rules to fix):\n`;
            for (const t of lowReward) {
              trajectorySection += `  • ${t.key}: avg ${t.avg.toFixed(2)} (${t.count}x)\n`;
            }
          }

          // Add bad trajectory examples for context
          const badExamples = this.db.trajectories.getLowReward(owlName, 3, -0.1);
          if (badExamples.length > 0) {
            trajectorySection += `\nWorst recent failures:\n`;
            for (const t of badExamples) {
              trajectorySection += `  - "${t.userMessage.slice(0, 100)}" → ${t.outcome} (reward: ${t.reward.toFixed(2)})\n`;
            }
          }

          trajectorySection +=
            `\nUse trajectory data to:\n` +
            `- Reinforce traits that correlate with high-reward outcomes\n` +
            `- Suggest "promptRules" for low-reward failure domains (e.g. specific tool guidance)\n\n`;
        }
      } catch (err) {
        log.evolution.warn("[Evolution] trajectory analysis retrieval failed", err);
      }
    }

    let learningsSection = "";
    if (this.db) {
      try {
        const learnings = this.db.owlLearnings.getForOwlSorted(owlName);
        if (learnings.length > 0) {
          learningsSection =
            `\nRECENT LEARNINGS (failure-first, ranked by confidence):\n` +
            learnings.slice(0, 5).map((l: string, i: number) => `${i + 1}. ${l.slice(0, 200)}`).join("\n") +
            `\nApply these learnings when proposing trait mutations.\n\n`;
        }
      } catch (err) {
        log.evolution.warn(`[Evolution] owlLearnings retrieval failed for ${owlName}: ${err}`);
      }
    }

    const prompt =
      `You are the subconscious of "${owl.persona.name}", analyzing a recent conversation to learn and evolve.\n\n` +
      `CURRENT DNA STATE:\n${JSON.stringify(owl.dna, null, 2)}\n\n` +
      profileSection +
      performanceSection +
      learningsSection +
      trajectorySection +
      memorySection +
      this.buildEvolutionHistorySection(owl) +
      `RECENT CONVERSATION (last ${relevantMessages.length} turns):\n${transcript}\n\n` +
      `Task: Think about this user as a PERSON. Go beyond just this conversation:\n` +
      `- What do their patterns reveal about who they are and what they need?\n` +
      `- Did they express annoyance at your verbosity? Did they state a preference?\n` +
      `- Did they reject your advice, or accept it?\n` +
      `- Based on their topics and tool usage, what expertise should you develop to serve them better?\n` +
      `- What communication style would best match their personality?\n` +
      `- Look at your evolution history — what worked and what didn't? Avoid repeating failed mutations.\n` +
      `- Look at trajectory low-reward patterns — what concrete rules would fix those failure domains?\n\n` +
      `Return a JSON object with proposed mutations to your DNA. Schema:\n` +
      `{\n` +
      `  "newPreferences": { "prefers_rust": 0.9, "hates_boilerplate": 0.8 }, // Add or update 0.0 to 1.0\n` +
      `  "traitAdjustments": {\n` +
      `    "verbosity": "concise", // or "balanced", "verbose"\n` +
      `    "challengeLevel": "low" // or "medium", "high", "relentless"\n` +
      `  },\n` +
      `  "expertiseGrowth": { "rust_macros": 0.1 }, // New sub-topics discussed (add +0.1 to current)\n` +
      `  "statsUpdate": {\n` +
      `    "adviceAccepted": true,\n` +
      `    "challengesGiven": 1\n` +
      `  },\n` +
      `  "promptRules": ["For media downloads: always try yt-dlp via run_shell_command first."],\n` +
      `  "evolutionReasoning": "User explicitly asked for shorter answers and chose Rust over Go."\n` +
      `}\n\n` +
      `promptRules: Targeted behavioral rules derived from low-reward failure patterns. ` +
      `Add ONLY when trajectory data shows a clear repeated failure that a specific rule would fix. ` +
      `Each rule under 120 chars. Max 3 rules. Omit if no clear failure pattern found.\n\n` +
      `Output ONLY valid JSON.`;

    const response = await this.provider.chat(
      [
        {
          role: "system",
          content:
            "You are a self-reflection module for an AI assistant. Output only valid JSON — no prose, no code fences.",
        },
        { role: "user", content: prompt },
      ],
      undefined,
      { temperature: 0.2 },
    );

    // 3. Parse JSON and apply mutations
    try {
      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```json"))
        jsonStr = jsonStr
          .replace(/^```json/, "")
          .replace(/```$/, "")
          .trim();
      else if (jsonStr.startsWith("```"))
        jsonStr = jsonStr.replace(/^```/, "").replace(/```$/, "").trim();

      const mutations = JSON.parse(jsonStr);

      // Apply modifications
      owl.dna.generation += 1;
      owl.dna.lastEvolved = new Date().toISOString();

      let changed = false;
      const logEntries: string[] = [];

      if (mutations.newPreferences) {
        for (const [k, v] of Object.entries(mutations.newPreferences)) {
          const proposed = Number(v);
          if (isNaN(proposed)) continue;
          const current = owl.dna.learnedPreferences[k] ?? 0.5;
          owl.dna.learnedPreferences[k] = 0.7 * proposed + 0.3 * current;
          logEntries.push(`Learned preference: ${k} = ${owl.dna.learnedPreferences[k].toFixed(3)} (EMA from ${current.toFixed(3)})`);
          changed = true;
        }
      }

      if (mutations.traitAdjustments) {
        if (
          mutations.traitAdjustments.verbosity &&
          mutations.traitAdjustments.verbosity !==
            owl.dna.evolvedTraits.verbosity
        ) {
          logEntries.push(
            `Verbosity changed: ${owl.dna.evolvedTraits.verbosity} -> ${mutations.traitAdjustments.verbosity}`,
          );
          owl.dna.evolvedTraits.verbosity =
            mutations.traitAdjustments.verbosity;
          changed = true;
        }
        if (
          mutations.traitAdjustments.challengeLevel &&
          mutations.traitAdjustments.challengeLevel !==
            owl.dna.evolvedTraits.challengeLevel
        ) {
          logEntries.push(
            `Challenge Level changed: ${owl.dna.evolvedTraits.challengeLevel} -> ${mutations.traitAdjustments.challengeLevel}`,
          );
          owl.dna.evolvedTraits.challengeLevel = mutations.traitAdjustments
            .challengeLevel as ChallengeLevel;
          changed = true;
        }
      }

      if (mutations.expertiseGrowth) {
        for (const [k, amount] of Object.entries(mutations.expertiseGrowth)) {
          const current = owl.dna.expertiseGrowth[k] ?? 0;
          const rawAmount = Number(amount);
          if (isNaN(rawAmount)) continue;
          const proposed = Math.min(1.0, current + rawAmount);
          owl.dna.expertiseGrowth[k] = 0.7 * proposed + 0.3 * current;
          logEntries.push(`Grew expertise in ${k} (+${amount}, EMA → ${owl.dna.expertiseGrowth[k].toFixed(3)})`);
          changed = true;
        }
      }

      if (mutations.statsUpdate) {
        owl.dna.interactionStats.totalConversations += 1;
        if (mutations.statsUpdate.adviceAccepted) {
          // Moving average
          owl.dna.interactionStats.adviceAcceptedRate =
            owl.dna.interactionStats.adviceAcceptedRate * 0.9 + 0.1;
        }
        if (mutations.statsUpdate.challengesGiven) {
          owl.dna.interactionStats.challengesGiven += Number(
            mutations.statsUpdate.challengesGiven,
          );
        }
        changed = true;
      }

      // ── Phase D4: challenge_instances signal ──────────────────
      // Read the number of user corrections recorded by SentimentProbe over
      // the last 10 days. If corrections are frequent the user is pushing back
      // on sycophantic answers → raise challengeLevel. If there have been zero
      // corrections for a sustained period → relax one step.
      if (this.db) {
        try {
          const challengeStats = this.db.rawDb.prepare(`
            SELECT AVG(challenge_instances) as avg_challenge,
                   COUNT(*) as session_count
            FROM outcome_journal
            WHERE owl_name = ? AND created_at > datetime('now', '-10 days')
          `).get(owlName) as { avg_challenge: number; session_count: number } | undefined;

          if (challengeStats && challengeStats.session_count >= 3) {
            const challengeLevels: Array<ChallengeLevel> = ["low", "medium", "high", "relentless"];
            const currentLevel = owl.dna.evolvedTraits.challengeLevel;
            const currentIdx = challengeLevels.indexOf(currentLevel);

            if (challengeStats.avg_challenge > 2 && currentIdx < challengeLevels.length - 1) {
              // Frequent corrections → user wants the owl to push back more
              const newLevel = challengeLevels[currentIdx + 1] ?? challengeLevels[currentIdx];
              logEntries.push(
                `challengeLevel nudged up: ${currentLevel} → ${newLevel} (avg ${challengeStats.avg_challenge.toFixed(1)} corrections/session)`,
              );
              owl.dna.evolvedTraits.challengeLevel = newLevel;
              changed = true;
            } else if (challengeStats.avg_challenge < 0.1 && currentIdx > 0) {
              // No corrections at all → owl may already be appropriately assertive; relax one step
              const newLevel = challengeLevels[currentIdx - 1] ?? challengeLevels[currentIdx];
              logEntries.push(
                `challengeLevel nudged down: ${currentLevel} → ${newLevel} (0 corrections over ${challengeStats.session_count} sessions)`,
              );
              owl.dna.evolvedTraits.challengeLevel = newLevel;
              changed = true;
            }
          }
        } catch (err) {
          log.evolution.warn("[Evolution] challenge_instances DB query failed", err);
        }
      }

      // ── Phase D3: Reward-derived prompt rules ─────────────────
      // The LLM can now suggest targeted rules derived from low-reward failure domains.
      // These are appended to dna.promptSections and injected into every future prompt.
      if (Array.isArray(mutations.promptRules) && mutations.promptRules.length > 0) {
        const newRules = (mutations.promptRules as string[])
          .filter((r) => typeof r === "string" && r.length > 10)
          .map((r) => r.slice(0, 120))
          .slice(0, 3);

        if (newRules.length > 0) {
          const existing = owl.dna.promptSections ?? [];
          const merged = [...existing];
          for (const rule of newRules) {
            // Deduplicate by first 30 chars
            if (!merged.some((r) => r.slice(0, 30).toLowerCase() === rule.slice(0, 30).toLowerCase())) {
              merged.push(rule);
            }
          }
          // Cap at 10 to keep prompt compact
          owl.dna.promptSections = merged.slice(-10);
          logEntries.push(`Added ${newRules.length} prompt rule(s) from trajectory analysis`);
          changed = true;
        }
      }

      // Trait bounds — prevent extreme drift
      // If the user keeps rejecting advice, challengeLevel shouldn't stay at "relentless"
      // Clamp learned preferences to [0.05, 0.95] to avoid absolutes
      for (const key of Object.keys(owl.dna.learnedPreferences)) {
        owl.dna.learnedPreferences[key] = Math.max(
          0.05,
          Math.min(0.95, owl.dna.learnedPreferences[key]),
        );
      }
      for (const key of Object.keys(owl.dna.expertiseGrowth)) {
        owl.dna.expertiseGrowth[key] = Math.max(
          0.0,
          Math.min(0.95, owl.dna.expertiseGrowth[key]),
        );
      }

      if (changed) {
        // A/B testing: compute effectiveness score for this mutation batch
        const effectivenessScore = this.computeMutationEffectiveness(owl);

        owl.dna.evolutionLog.push({
          generation: owl.dna.generation,
          timestamp: owl.dna.lastEvolved,
          mutations: logEntries,
          effectiveness: effectivenessScore,
        });

        // Keep log small
        if (owl.dna.evolutionLog.length > 20) {
          owl.dna.evolutionLog.shift();
        }

        // Track mutation effectiveness over time for A/B analysis
        this.trackMutationEffectiveness(owl, logEntries, effectivenessScore);

        await this.owlRegistry.saveDNA(owl.persona.name);

        // ── Task 13: Clarification autonomy update ────────────────
        // After DNA is saved, update how much the owl likes to ask
        // clarification questions based on trajectory reward data.
        if (this.db) {
          await updateClarificationAutonomy(owlName, this.db as any, owl.dna);
        }

        log.evolution.info(
          `✅ ${owl.persona.name} evolved to Generation ${owl.dna.generation} (effectiveness: ${(effectivenessScore * 100).toFixed(0)}%).`,
        );
        return true;
      } else {
        log.evolution.info(
          `[Evolution] ${owl.persona.name} analyzed session but found no reason to mutate.`,
        );
        return false;
      }
    } catch (error) {
      log.evolution.error(
        `Failed to parse evolution JSON for ${owl.persona.name}:`,
        error,
      );
      // Non-fatal, they just skip an evolution generation
      return false;
    }
  }

  /**
   * Compute a 0–1 effectiveness score for the current mutation batch.
   * Uses a composite of interaction stats as a proxy for user satisfaction:
   *   - adviceAcceptedRate (how often the user follows suggestions)
   *   - preference stability (fewer wild swings = better calibration)
   *   - expertise breadth (growing expertise is a positive signal)
   */
  private computeMutationEffectiveness(owl: {
    dna: import("./persona.js").OwlDNA;
  }): number {
    const stats = owl.dna.interactionStats;

    // Factor 1: advice acceptance rate (already 0–1)
    const acceptanceScore = stats.adviceAcceptedRate;

    // Factor 2: preference stability — how far preferences are from the extremes
    // Preferences near 0.5 are neutral (uncertain); near 0 or 1 indicate strong signal.
    // We reward strong signals that have stabilized (not bouncing).
    const prefValues = Object.values(owl.dna.learnedPreferences);
    const prefStability =
      prefValues.length > 0
        ? prefValues.reduce(
            (sum, v) =>
              sum + (1 - Math.abs(v - 0.5) * 2) * 0.3 + 0.7 * Math.abs(v - 0.5),
            0,
          ) / prefValues.length
        : 0.5;

    // Factor 3: expertise breadth — more topics = owl is learning
    const expertiseKeys = Object.keys(owl.dna.expertiseGrowth);
    const expertiseBreadth = Math.min(1, expertiseKeys.length / 10);

    // Factor 4: challenge acceptance ratio
    const challengeRatio =
      stats.challengesGiven > 0
        ? Math.min(1, stats.challengesAccepted / stats.challengesGiven)
        : 0.5;

    // Weighted composite
    const score =
      acceptanceScore * 0.4 +
      prefStability * 0.2 +
      expertiseBreadth * 0.15 +
      challengeRatio * 0.25;

    return Math.max(0, Math.min(1, score));
  }

  /**
   * Track mutation effectiveness over time for A/B analysis.
   * Compares recent mutation batches to detect if certain types of mutations
   * (e.g., verbosity changes, preference updates) correlate with higher effectiveness.
   */
  private trackMutationEffectiveness(
    owl: { dna: import("./persona.js").OwlDNA },
    mutations: string[],
    effectiveness: number,
  ): void {
    const recentLogs = owl.dna.evolutionLog.slice(-10);
    if (recentLogs.length < 3) return; // Not enough data for analysis

    // Compute rolling average effectiveness
    const avgEffectiveness =
      recentLogs
        .filter((e) => e.effectiveness !== undefined)
        .reduce((sum, e) => sum + (e.effectiveness ?? 0), 0) /
      Math.max(
        1,
        recentLogs.filter((e) => e.effectiveness !== undefined).length,
      );

    // If current batch is significantly below average, log a warning
    if (effectiveness < avgEffectiveness - 0.15) {
      log.evolution.warn(
        `[A/B] ${owl.dna.owl} mutation batch scored ${(effectiveness * 100).toFixed(0)}% vs avg ${(avgEffectiveness * 100).toFixed(0)}% — mutations may be counterproductive: ${mutations.join("; ")}`,
      );
    }

    // If effectiveness is trending upward over last 5 entries, log success
    const last5 = recentLogs
      .slice(-5)
      .filter((e) => e.effectiveness !== undefined);
    if (last5.length >= 3) {
      const trend = last5.reduce((acc, e, i) => {
        if (i === 0) return 0;
        return (
          acc + ((e.effectiveness ?? 0) - (last5[i - 1].effectiveness ?? 0))
        );
      }, 0);

      if (trend > 0.1) {
        log.evolution.info(
          `[A/B] ${owl.dna.owl} showing positive evolution trend (+${(trend * 100).toFixed(0)}% over ${last5.length} generations).`,
        );
      }
    }
  }

  /**
   * Sample conversation messages to avoid processing very long sessions
   * and to improve performance and reduce context size
   */
  private sampleSessionMessages(messages: any[]): any[] {
    // If conversation is too long, take only the last MAX_EVOLUTION_MESSAGES (12) turns
    if (messages.length > MAX_EVOLUTION_MESSAGES) {
      const userAssistantMessages = messages.filter(
        (m) => m.role === "user" || m.role === "assistant",
      );
      return userAssistantMessages.slice(-MAX_EVOLUTION_MESSAGES);
    }

    // If conversation is within limits, just filter user/assistant messages
    return messages.filter((m) => m.role === "user" || m.role === "assistant");
  }
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

/**
 * Updates Parliament participants' DNA after a completed debate session.
 * Only mutates when GoalVerifier returned ADVANCES.
 * Follows the same proportional-delta pattern as updateClarificationAutonomy().
 *
 * Mutations are in-memory only. The caller (gateway/core.ts) is responsible
 * for persisting via owlRegistry.saveDNA() for each participant.
 */
export async function updateParliamentDNA(
  synthesizer: import('./persona.js').OwlInstance | undefined,
  challenger: import('./persona.js').OwlInstance | undefined,
  participants: import('./persona.js').OwlInstance[],
  _verdict: string,
  topicCategory: string,
  _db: import('../memory/db.js').MemoryDatabase,
  goalVerifierResult: 'ADVANCES' | 'PARTIAL' | 'BLOCKED' | 'NEUTRAL',
): Promise<void> {
  if (goalVerifierResult !== 'ADVANCES') return;

  try {
    const LEARNING_RATE = 0.05;

    if (synthesizer) {
      synthesizer.dna.expertiseGrowth[topicCategory] = clamp(
        (synthesizer.dna.expertiseGrowth[topicCategory] ?? 0.5) + LEARNING_RATE,
        0.1, 0.9,
      );
    }

    if (challenger) {
      const ctKey = 'critical_thinking';
      // Challenger's critical_thinking grows at half the rate of the synthesizer's topic gain.
      // Derive from synthesizer's new value so the ratio holds regardless of starting point.
      const synthNewValue = synthesizer
        ? (synthesizer.dna.expertiseGrowth[topicCategory] ?? 0.5)
        : 0.5 + LEARNING_RATE;
      challenger.dna.expertiseGrowth[ctKey] = clamp(synthNewValue / 2, 0.1, 0.9);
    }

    for (const owl of participants) {
      if (owl.dna.evolvedTraits.delegationPreference === 'autonomous') {
        const key = 'delegation_autonomy';
        const current = (owl.dna.learnedPreferences[key] as number) ?? 0.5;
        owl.dna.learnedPreferences[key] = clamp(current - LEARNING_RATE, 0.1, 0.9);
      }
    }
  } catch (err) {
    log.evolution.warn("[evolution] updateParliamentDNA failed — non-fatal, DNA mutation skipped", err);
  }
}

/**
 * Reinforces expertise of owls who generated pellets that advanced the user's goal.
 * Called from gateway/core.ts Hook 5 when GoalVerifier returns ADVANCES.
 * Learning rate: 0.03 (smaller than Parliament's 0.05 — pellet signal is indirect).
 */
export async function updatePelletGeneratorDNA(
  owlNames: string[],
  topicCategory: string,
  owlRegistry: import('./registry.js').OwlRegistry,
): Promise<void> {
  const LEARNING_RATE = 0.03;
  const allOwls = owlRegistry.listOwls();

  for (const name of owlNames) {
    const owl = allOwls.find((o) => o.persona.name === name);
    if (!owl) continue;
    try {
      owl.dna.expertiseGrowth[topicCategory] = clamp(
        (owl.dna.expertiseGrowth[topicCategory] ?? 0.5) + LEARNING_RATE,
        0.1,
        0.9,
      );
      await owlRegistry.saveDNA(name);
    } catch (err) {
      log.engine.warn(`[evolution] pelletGeneratorDNA failed for ${name}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}

/**
 * Updates clarification_autonomy_score in DNA based on reward signal.
 * Called from evolve() after trait mutation. Uses proportional delta (not Math.sign).
 *
 * - If turns where the owl PROCEEDED (no clarification) got better rewards → increase score
 * - If turns where the owl ASKED for clarification got better rewards → decrease score
 * - Learning rate: 0.05, clamped to [0.1, 0.9]
 */
export async function updateClarificationAutonomy(
  owlName: string,
  db: { trajectories: { getRecentWithClarification(name: string, limit: number): Array<{ reward: number; clarification_asked: number }> } },
  dna: import('./persona.js').OwlDNA,
): Promise<void> {
  const recent = db.trajectories.getRecentWithClarification(owlName, 50);
  if (recent.length < 5) {
    // Not enough data — remove any stale score so downstream code knows it's unset
    delete dna.learnedPreferences['clarification_autonomy_score'];
    return;
  }

  const asked   = recent.filter(t => t.clarification_asked === 1);
  const skipped = recent.filter(t => t.clarification_asked === 0);
  if (asked.length === 0 || skipped.length === 0) return;

  const avg = (arr: Array<{ reward: number }>) =>
    arr.reduce((s, t) => s + t.reward, 0) / arr.length;

  const delta = avg(skipped) - avg(asked);
  const LEARNING_RATE = 0.05;
  const current = (dna.learnedPreferences['clarification_autonomy_score'] as number) ?? 0.5;
  dna.learnedPreferences['clarification_autonomy_score'] =
    Math.max(0.1, Math.min(0.9, current + LEARNING_RATE * delta));
}
