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
  private userProfileProvider?: () => import('../learning/micro-learner.js').UserProfile | null;

  constructor(
    provider: ModelProvider,
    config: StackOwlConfig,
    sessionStore: SessionStore,
    owlRegistry: OwlRegistry,
    userProfileProvider?: () => import('../learning/micro-learner.js').UserProfile | null,
  ) {
    this.provider = provider;
    this.config = config;
    this.sessionStore = sessionStore;
    this.owlRegistry = owlRegistry;
    this.userProfileProvider = userProfileProvider;
  }

  /**
   * Apply DNA decay toward neutral (0.5) if more than 7 days have passed since last decay.
   * Prevents stale preferences from dominating forever.
   */
  async applyDecayIfNeeded(owlName: string): Promise<boolean> {
    const owl = this.owlRegistry.get(owlName);
    if (!owl) return false;

    const decayRate = this.config.owlDna?.decayRatePerWeek ?? 0.01;
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
    const validSessions = sessionsToAnalyze.filter((s) => s.messages.length >= 4);
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
    const relevantMessages = allRelevantMessages.slice(-MAX_EVOLUTION_MESSAGES * 2);

    const transcript = relevantMessages
      .map(
        (m) =>
          `[${m.role.toUpperCase()}]: ${(m.content ?? "").slice(0, MAX_MSG_CHARS)}`,
      )
      .join("\n\n");

    // If a user profile is available (from MicroLearner), include it
    // to give the evolution LLM a holistic view of who this person is
    let profileSection = '';
    if (this.userProfileProvider) {
      const profile = this.userProfileProvider();
      if (profile && profile.totalMessages > 10) {
        const topTopics = Object.entries(profile.topics)
          .sort(([, a], [, b]) => b - a)
          .slice(0, 5)
          .map(([t, c]) => `${t}(${c}x)`).join(', ');
        const topTools = Object.entries(profile.toolUsage)
          .sort(([, a], [, b]) => b - a)
          .slice(0, 5)
          .map(([t, c]) => `${t}(${c}x)`).join(', ');
        profileSection =
          `\nUSER PROFILE (${profile.totalMessages} total messages):\n` +
          `- Top topics: ${topTopics || 'none'}\n` +
          `- Most used tools: ${topTools || 'none'}\n` +
          `- Interaction style: ${profile.commandRate > 0.5 ? 'command-oriented (prefers actions)' : profile.questionRate > 0.4 ? 'question-oriented (prefers explanations)' : 'conversational'}\n` +
          `- Avg message length: ${Math.round(profile.avgMessageLength)} chars\n` +
          `- Sentiment: ${profile.positiveSignals}+ / ${profile.negativeSignals}-\n\n`;
      }
    }

    const prompt =
      `You are the subconscious of "${owl.persona.name}", analyzing a recent conversation to learn and evolve.\n\n` +
      `CURRENT DNA STATE:\n${JSON.stringify(owl.dna, null, 2)}\n\n` +
      profileSection +
      `RECENT CONVERSATION (last ${relevantMessages.length} turns):\n${transcript}\n\n` +
      `Task: Think about this user as a PERSON. Go beyond just this conversation:\n` +
      `- What do their patterns reveal about who they are and what they need?\n` +
      `- Did they express annoyance at your verbosity? Did they state a preference?\n` +
      `- Did they reject your advice, or accept it?\n` +
      `- Based on their topics and tool usage, what expertise should you develop to serve them better?\n` +
      `- What communication style would best match their personality?\n\n` +
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
      `  "evolutionReasoning": "User explicitly asked for shorter answers and chose Rust over Go."\n` +
      `}\n\n` +
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
          owl.dna.learnedPreferences[k] = Number(v);
          logEntries.push(`Learned preference: ${k} = ${v}`);
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
          const current = owl.dna.expertiseGrowth[k] || 0;
          owl.dna.expertiseGrowth[k] = Math.min(1.0, current + Number(amount));
          logEntries.push(`Grew expertise in ${k} (+${amount})`);
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
  private computeMutationEffectiveness(owl: { dna: import("./persona.js").OwlDNA }): number {
    const stats = owl.dna.interactionStats;

    // Factor 1: advice acceptance rate (already 0–1)
    const acceptanceScore = stats.adviceAcceptedRate;

    // Factor 2: preference stability — how far preferences are from the extremes
    // Preferences near 0.5 are neutral (uncertain); near 0 or 1 indicate strong signal.
    // We reward strong signals that have stabilized (not bouncing).
    const prefValues = Object.values(owl.dna.learnedPreferences);
    const prefStability = prefValues.length > 0
      ? prefValues.reduce((sum, v) => sum + (1 - Math.abs(v - 0.5) * 2) * 0.3 + 0.7 * Math.abs(v - 0.5), 0) / prefValues.length
      : 0.5;

    // Factor 3: expertise breadth — more topics = owl is learning
    const expertiseKeys = Object.keys(owl.dna.expertiseGrowth);
    const expertiseBreadth = Math.min(1, expertiseKeys.length / 10);

    // Factor 4: challenge acceptance ratio
    const challengeRatio = stats.challengesGiven > 0
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
    const avgEffectiveness = recentLogs
      .filter(e => e.effectiveness !== undefined)
      .reduce((sum, e) => sum + (e.effectiveness ?? 0), 0) / Math.max(1, recentLogs.filter(e => e.effectiveness !== undefined).length);

    // If current batch is significantly below average, log a warning
    if (effectiveness < avgEffectiveness - 0.15) {
      log.evolution.warn(
        `[A/B] ${owl.dna.owl} mutation batch scored ${(effectiveness * 100).toFixed(0)}% vs avg ${(avgEffectiveness * 100).toFixed(0)}% — mutations may be counterproductive: ${mutations.join('; ')}`,
      );
    }

    // If effectiveness is trending upward over last 5 entries, log success
    const last5 = recentLogs.slice(-5).filter(e => e.effectiveness !== undefined);
    if (last5.length >= 3) {
      const trend = last5.reduce((acc, e, i) => {
        if (i === 0) return 0;
        return acc + ((e.effectiveness ?? 0) - (last5[i - 1].effectiveness ?? 0));
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
