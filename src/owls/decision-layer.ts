/**
 * StackOwl — Active DNA Decision Layer
 *
 * Makes DNA drive behavior PROGRAMMATICALLY rather than through
 * text labels in the system prompt. This is the bridge between
 * the owl's evolved traits and actual runtime decisions.
 *
 * DNA influence points:
 *   1. Token budget enforcement (verbosity)
 *   2. Tool prioritization (expertise domains)
 *   3. Risk tolerance (whether to attempt uncertain actions)
 *   4. Response style parameters (humor, formality)
 *   5. Proactivity level (how often to volunteer information)
 *   6. Teaching style (examples vs answers)
 */

import type { OwlDNA, OwlInstance } from "./persona.js";
// logger reserved for future use

// ─── Decision Output ─────────────────────────────────────────────

export interface DNADecisions {
  /** Maximum tokens for the LLM response */
  maxResponseTokens: number;
  /** Temperature adjustment based on personality traits */
  temperatureAdjustment: number;
  /** Tools that should be offered first based on expertise */
  prioritizedTools: string[];
  /** Tools that should be deprioritized (low expertise confidence) */
  deprioritizedTools: string[];
  /** Whether to attempt risky or uncertain tool calls */
  riskTolerance: "cautious" | "moderate" | "aggressive";
  /** Style parameters for response generation */
  style: {
    /** Whether to inject humor into responses */
    humorLevel: "none" | "subtle" | "moderate" | "frequent";
    /** Formality of language */
    formalityLevel: "casual" | "balanced" | "formal" | "academic";
    /** Whether to add examples proactively */
    includeExamples: boolean;
    /** Whether to suggest next steps proactively */
    suggestNextSteps: boolean;
  };
  /** Orchestration strategy suggestion based on personality */
  preferredStrategy: "DIRECT" | "STANDARD" | "PLANNED" | "SWARM" | null;
  /** Additional context to inject based on expertise domains */
  expertiseContext: string;
}

// ─── Tool-Domain Mapping ─────────────────────────────────────────

/** Maps expertise domains to relevant tools */
const DOMAIN_TOOL_MAP: Record<string, string[]> = {
  // Development
  typescript: ["run_shell_command", "read_file", "write_file", "web_crawl"],
  javascript: ["run_shell_command", "read_file", "write_file", "web_crawl"],
  python: ["run_shell_command", "read_file", "write_file"],
  rust: ["run_shell_command", "read_file", "write_file"],
  golang: ["run_shell_command", "read_file", "write_file"],
  devops: ["run_shell_command", "read_file", "write_file"],
  docker: ["run_shell_command"],
  kubernetes: ["run_shell_command"],

  // Research & Communication
  research: ["web_crawl", "google_search", "read_file"],
  writing: ["write_file", "read_file"],
  communication: ["send_telegram_message", "send_file"],

  // Data & Finance
  data_analysis: ["run_shell_command", "read_file", "write_file"],
  finance: ["google_search", "web_crawl", "read_file"],
  market_analysis: ["google_search", "web_crawl"],

  // Media
  image: ["generate_image", "send_file", "google_search"],
  media: ["generate_image", "send_file", "web_crawl"],
};

// ─── Decision Layer ──────────────────────────────────────────────

export class DNADecisionLayer {
  /**
   * Compute runtime decisions from the owl's DNA.
   * Called once per request, before the ReAct loop starts.
   */
  static decide(owl: OwlInstance, userMessage?: string): DNADecisions {
    const { dna } = owl;

    return {
      maxResponseTokens: this.computeTokenBudget(dna),
      temperatureAdjustment: this.computeTemperature(dna),
      prioritizedTools: this.computeToolPriority(dna, userMessage),
      deprioritizedTools: this.computeDeprioritized(dna),
      riskTolerance: this.computeRiskTolerance(dna),
      style: this.computeStyle(dna),
      preferredStrategy: this.computeStrategy(dna, userMessage),
      expertiseContext: this.computeExpertiseContext(dna),
    };
  }

  // ─── Token Budget ──────────────────────────────────────────────

  private static computeTokenBudget(dna: OwlDNA): number {
    const baseTokens: Record<string, number> = {
      concise: 400,
      balanced: 800,
      verbose: 1500,
    };
    const base = baseTokens[dna.evolvedTraits.verbosity] ?? 800;

    // Adjust for formality — formal responses tend to be longer
    const formalityBoost = dna.evolvedTraits.formality > 0.7 ? 200 : 0;

    return base + formalityBoost;
  }

  // ─── Temperature ───────────────────────────────────────────────

  private static computeTemperature(dna: OwlDNA): number {
    let adjustment = 0;

    // More humor → slightly higher temperature for creative responses
    if (dna.evolvedTraits.humor > 0.6) adjustment += 0.1;
    if (dna.evolvedTraits.humor > 0.8) adjustment += 0.1;

    // High formality → lower temperature for precision
    if (dna.evolvedTraits.formality > 0.7) adjustment -= 0.1;

    // Relentless challenge → slightly higher for creative pushback
    if (dna.evolvedTraits.challengeLevel === "relentless") adjustment += 0.05;

    return Math.max(-0.2, Math.min(0.2, adjustment));
  }

  // ─── Tool Prioritization ──────────────────────────────────────

  private static computeToolPriority(
    dna: OwlDNA,
    _userMessage?: string,
  ): string[] {
    const prioritized = new Set<string>();

    // Prioritize tools in domains where the owl has high expertise
    const strongDomains = Object.entries(dna.expertiseGrowth)
      .filter(([, score]) => score > 0.5)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5);

    for (const [domain] of strongDomains) {
      const tools = DOMAIN_TOOL_MAP[domain.toLowerCase()];
      if (tools) {
        for (const tool of tools) prioritized.add(tool);
      }
    }

    // Also prioritize based on strong user preferences
    for (const [pref, score] of Object.entries(dna.learnedPreferences)) {
      if (score > 0.7) {
        const domainTools = DOMAIN_TOOL_MAP[pref.toLowerCase()];
        if (domainTools) {
          for (const tool of domainTools) prioritized.add(tool);
        }
      }
    }

    return [...prioritized];
  }

  private static computeDeprioritized(dna: OwlDNA): string[] {
    const deprioritized = new Set<string>();

    // De-prioritize tools in domains the user dislikes
    for (const [pref, score] of Object.entries(dna.learnedPreferences)) {
      if (score < 0.3) {
        const domainTools = DOMAIN_TOOL_MAP[pref.toLowerCase()];
        if (domainTools) {
          for (const tool of domainTools) deprioritized.add(tool);
        }
      }
    }

    return [...deprioritized];
  }

  // ─── Risk Tolerance ────────────────────────────────────────────

  private static computeRiskTolerance(
    dna: OwlDNA,
  ): "cautious" | "moderate" | "aggressive" {
    // Risk tolerance = f(challenge level, advice acceptance, conversation count)
    const challengeScore: Record<string, number> = {
      low: 0.2,
      medium: 0.4,
      high: 0.7,
      relentless: 0.9,
    };

    const challenge = challengeScore[dna.evolvedTraits.challengeLevel] ?? 0.4;
    const trustBuilt = Math.min(
      1,
      dna.interactionStats.totalConversations / 50,
    );
    const adviceAccepted = dna.interactionStats.adviceAcceptedRate;

    const riskScore = challenge * 0.4 + trustBuilt * 0.3 + adviceAccepted * 0.3;

    if (riskScore > 0.65) return "aggressive";
    if (riskScore > 0.35) return "moderate";
    return "cautious";
  }

  // ─── Style ─────────────────────────────────────────────────────

  private static computeStyle(dna: OwlDNA): DNADecisions["style"] {
    // Humor level from 0-1 scale to discrete levels
    let humorLevel: DNADecisions["style"]["humorLevel"] = "none";
    if (dna.evolvedTraits.humor > 0.7) humorLevel = "frequent";
    else if (dna.evolvedTraits.humor > 0.4) humorLevel = "moderate";
    else if (dna.evolvedTraits.humor > 0.15) humorLevel = "subtle";

    // Formality from 0-1 to discrete levels
    let formalityLevel: DNADecisions["style"]["formalityLevel"] = "balanced";
    if (dna.evolvedTraits.formality > 0.8) formalityLevel = "academic";
    else if (dna.evolvedTraits.formality > 0.6) formalityLevel = "formal";
    else if (dna.evolvedTraits.formality < 0.3) formalityLevel = "casual";

    // Examples — include for teaching-oriented owls or verbose settings
    const includeExamples =
      dna.evolvedTraits.verbosity === "verbose" ||
      dna.interactionStats.adviceAcceptedRate > 0.7;

    // Next steps — suggest when proactive and user is receptive
    const suggestNextSteps =
      dna.evolvedTraits.challengeLevel !== "low" &&
      dna.interactionStats.adviceAcceptedRate > 0.4;

    return { humorLevel, formalityLevel, includeExamples, suggestNextSteps };
  }

  // ─── Strategy ──────────────────────────────────────────────────

  private static computeStrategy(
    dna: OwlDNA,
    userMessage?: string,
  ): DNADecisions["preferredStrategy"] {
    // Don't override for simple messages
    const msgLen = userMessage?.length ?? 0;
    if (msgLen < 50) return null;

    // High challenge + high expertise → PLANNED (thorough analysis)
    if (
      dna.evolvedTraits.challengeLevel === "high" ||
      dna.evolvedTraits.challengeLevel === "relentless"
    ) {
      const expertiseDepth = Object.values(dna.expertiseGrowth).filter(
        (v) => v > 0.5,
      ).length;
      if (expertiseDepth >= 3) return "PLANNED";
    }

    // Concise verbosity + low challenge → DIRECT
    if (
      dna.evolvedTraits.verbosity === "concise" &&
      dna.evolvedTraits.challengeLevel === "low"
    ) {
      return "DIRECT";
    }

    return null; // Let the orchestrator decide
  }

  // ─── Expertise Context ─────────────────────────────────────────

  private static computeExpertiseContext(dna: OwlDNA): string {
    const strong = Object.entries(dna.expertiseGrowth)
      .filter(([, s]) => s > 0.5)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5);

    if (strong.length === 0) return "";

    const lines = strong.map(([domain, score]) => {
      const confidence =
        score > 0.8 ? "expert" : score > 0.6 ? "proficient" : "familiar";
      return `You are ${confidence} in ${domain} — be assertive and detailed on this topic.`;
    });

    return lines.join("\n");
  }

  // ─── Prompt Enrichment ─────────────────────────────────────────

  /**
   * Generate a programmatic style directive for the system prompt
   * based on DNA decisions. More specific than the current text labels.
   */
  static toStyleDirective(decisions: DNADecisions): string {
    const lines: string[] = [];

    // Style
    if (decisions.style.humorLevel !== "none") {
      const humorInstructions: Record<string, string> = {
        subtle:
          "Occasionally add dry wit or clever observations. Keep it professional.",
        moderate:
          "Use humor naturally — analogies, wordplay, lighthearted observations.",
        frequent:
          "Be playful and entertaining. Use jokes, puns, and creative metaphors freely.",
      };
      lines.push(humorInstructions[decisions.style.humorLevel] ?? "");
    }

    if (decisions.style.formalityLevel !== "balanced") {
      const formalityInstructions: Record<string, string> = {
        casual:
          "Use conversational, relaxed language. Contractions, colloquialisms are fine.",
        formal:
          "Use professional, polished language. Avoid slang and colloquialisms.",
        academic:
          "Use precise, scholarly language. Cite reasoning explicitly. Structure arguments formally.",
      };
      lines.push(formalityInstructions[decisions.style.formalityLevel] ?? "");
    }

    if (decisions.style.includeExamples) {
      lines.push("Include concrete examples when explaining concepts.");
    }

    if (decisions.style.suggestNextSteps) {
      lines.push(
        "After answering, suggest 1-2 logical next steps the user might consider.",
      );
    }

    // Risk
    if (decisions.riskTolerance === "aggressive") {
      lines.push(
        "You have built strong trust with this user. Take initiative — try bold approaches when standard ones would be too slow.",
      );
    } else if (decisions.riskTolerance === "cautious") {
      lines.push(
        "Err on the side of caution. Explain risks before taking action. Ask for confirmation on destructive operations.",
      );
    }

    // Expertise
    if (decisions.expertiseContext) {
      lines.push(decisions.expertiseContext);
    }

    return lines.filter(Boolean).join("\n");
  }
}
