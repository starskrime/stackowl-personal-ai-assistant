/**
 * StackOwl — Capability Need Assessor (CNA)
 *
 * Enterprise-grade pre-synthesis gate. Runs BEFORE any skill or tool is created.
 * Combines three checks from the research literature into a single LLM call:
 *
 *   Gate 1 — Request Type (HuggingGPT-inspired task taxonomy)
 *     CONVERSATIONAL: greetings, small talk, thanks → never synthesize
 *     INFORMATIONAL:  factual questions, explanations → never synthesize
 *     ANALYTICAL:     summarize, analyze, compare existing content → rarely synthesize
 *     OPERATIONAL:    do something on the system (send, create, control) → may synthesize
 *
 *   Gate 2 — Coverage Check (CREATOR framework "reuse gate")
 *     Can any combination of existing tools + skills handle this request?
 *     YES → route to existing capability, no synthesis
 *     NO  → synthesis is considered
 *
 *   Gate 3 — Novelty Guard (VOYAGER + ToolScope deduplication)
 *     Is this capability semantically close to an existing skill (>85% overlap)?
 *     YES → improve the existing skill instead of creating a new one
 *     NO  → a genuinely new skill is warranted
 *
 * Only when all three gates pass does synthesis proceed.
 *
 * Research basis:
 *   - CREATOR (Qian et al., EMNLP 2023): synthesis only when existing tools exhausted
 *   - ToolLLM DFSDT: empirical exhaustion before synthesis
 *   - VOYAGER (Wang et al., 2023): top-5 skill retrieval before synthesis
 *   - RAG-MCP (2025): tool count degradation, 13% → 43% accuracy with proper gating
 *   - ToolScope (2025): cosine similarity thresholds 0.85–0.92 for deduplication
 */

import type { ModelProvider } from "../providers/base.js";
import type { Skill } from "../skills/types.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export type RequestType =
  | "CONVERSATIONAL" // greetings, thanks, small talk
  | "INFORMATIONAL" // facts, explanations, knowledge questions
  | "ANALYTICAL" // summarize, analyze, compare existing content
  | "OPERATIONAL"; // system actions: send, create, control, download, run

export type SynthesisVerdict =
  | "SKIP" // Request type doesn't warrant synthesis
  | "COVERED" // Existing tools/skills already handle this
  | "NEAR_DUPLICATE" // Very similar skill exists — improve it instead of creating new
  | "SYNTHESIZE"; // Genuine capability gap — proceed with synthesis

export interface AssessmentResult {
  verdict: SynthesisVerdict;
  requestType: RequestType;
  reasoning: string;
  /** Name of the closest existing skill (for COVERED / NEAR_DUPLICATE verdicts) */
  suggestedExistingSkill?: string;
  /** Overlap score 0–1 for NEAR_DUPLICATE (keyword-based heuristic) */
  overlapScore?: number;
}

// ─── Heuristics ──────────────────────────────────────────────────

/** Fast keyword-based pre-filter — avoids LLM call for obvious cases. */
const CONVERSATIONAL_PATTERNS = [
  /^(hi|hello|hey|thanks|thank you|ok|okay|great|nice|cool|sure|got it|sounds good)[.!?\s]*$/i,
  /^(bye|goodbye|see you|later)[.!?\s]*$/i,
];

const INFORMATIONAL_STARTERS = [
  /^(what is|what are|what was|what were|who is|who are|explain|describe|define|tell me about)/i,
  /^(why does|why is|why are|how does|how do|how is|how are)/i,
  /^(when did|when was|when is|where is|where are)/i,
];

/** Words that signal a genuine system-level action need */
const OPERATIONAL_VERBS = [
  "send",
  "post",
  "email",
  "tweet",
  "message",
  "notify",
  "download",
  "upload",
  "save",
  "create file",
  "write file",
  "take screenshot",
  "capture",
  "record",
  "open app",
  "launch",
  "install",
  "run command",
  "control",
  "click",
  "type",
  "press",
  "monitor",
  "watch",
  "track",
  "schedule",
  "remind",
  "connect",
  "sync",
];

function heuristicRequestType(userRequest: string): RequestType | null {
  const lower = userRequest.toLowerCase().trim();

  // Very short messages are likely conversational
  if (lower.length < 20 && CONVERSATIONAL_PATTERNS.some((p) => p.test(lower))) {
    return "CONVERSATIONAL";
  }

  // Informational starters
  if (INFORMATIONAL_STARTERS.some((p) => p.test(lower))) {
    return "INFORMATIONAL";
  }

  // Operational verbs
  if (OPERATIONAL_VERBS.some((v) => lower.includes(v))) {
    return "OPERATIONAL";
  }

  return null; // uncertain — let LLM decide
}

/** Keyword-based overlap between a user request and a skill's name + description */
function computeOverlap(userRequest: string, skill: Skill): number {
  const requestWords = userRequest
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length > 3);
  const skillText = `${skill.name} ${skill.description}`.toLowerCase();
  if (requestWords.length === 0) return 0;
  const matchCount = requestWords.filter((w) => skillText.includes(w)).length;
  return matchCount / requestWords.length;
}

// ─── Assessor ────────────────────────────────────────────────────

export class CapabilityNeedAssessor {
  constructor(private provider: ModelProvider) {}

  /**
   * Assess whether a capability gap warrants new skill synthesis.
   *
   * Returns a verdict with reasoning. Call this BEFORE generateSkillMd().
   *
   * @param gapDescription — Why the engine declared a gap (e.g. "Need ability to
   *   programmatically control Chrome browser"). When provided, the engine has
   *   ALREADY tried existing tools and determined they are insufficient — so the
   *   assessor should weigh this heavily toward SYNTHESIZE.
   */
  async assess(
    userRequest: string,
    availableToolNames: string[],
    existingSkills: Skill[],
    gapDescription?: string,
  ): Promise<AssessmentResult> {
    // ── Gate 1: Fast heuristic pre-filter ────────────────────────
    const heuristicType = heuristicRequestType(userRequest);

    if (heuristicType === "CONVERSATIONAL") {
      return {
        verdict: "SKIP",
        requestType: "CONVERSATIONAL",
        reasoning: "Conversational request — no skill synthesis needed.",
      };
    }

    if (heuristicType === "INFORMATIONAL") {
      return {
        verdict: "SKIP",
        requestType: "INFORMATIONAL",
        reasoning:
          "Informational request — web search or existing knowledge is sufficient.",
      };
    }

    // ── Gate 3: Fast deduplication before LLM call ────────────────
    let bestOverlap = 0;
    let bestSkill: Skill | undefined;
    for (const skill of existingSkills) {
      const overlap = computeOverlap(userRequest, skill);
      if (overlap > bestOverlap) {
        bestOverlap = overlap;
        bestSkill = skill;
      }
    }

    if (bestOverlap >= 0.85 && bestSkill) {
      return {
        verdict: "NEAR_DUPLICATE",
        requestType: "OPERATIONAL",
        reasoning: `Skill "${bestSkill.name}" already covers this (${(bestOverlap * 100).toFixed(0)}% keyword overlap). Improve it instead.`,
        suggestedExistingSkill: bestSkill.name,
        overlapScore: bestOverlap,
      };
    }

    // ── Fast-path: if the engine explicitly declared a gap, trust it ──
    // The engine already tried all available tools and determined they can't
    // handle the request. Running the LLM assessor would just second-guess
    // that decision (often incorrectly, e.g. seeing "google_search" and
    // concluding it covers "open Chrome browser").
    if (gapDescription) {
      return {
        verdict: "SYNTHESIZE",
        requestType: "OPERATIONAL",
        reasoning: `Engine gap: ${gapDescription.slice(0, 120)}`,
      };
    }

    // ── Gates 1+2 combined LLM call ───────────────────────────────
    try {
      return await this.assessWithLLM(
        userRequest,
        availableToolNames,
        existingSkills,
        bestSkill,
        bestOverlap,
      );
    } catch (err) {
      log.engine.warn(
        `[CapabilityNeedAssessor] LLM assessment failed, defaulting to SYNTHESIZE: ${err instanceof Error ? err.message : String(err)}`,
      );
      // Fail open — if assessor errors, allow synthesis rather than block legitimate needs
      return {
        verdict: "SYNTHESIZE",
        requestType: "OPERATIONAL",
        reasoning: "Assessment unavailable — proceeding with synthesis.",
      };
    }
  }

  private async assessWithLLM(
    userRequest: string,
    availableToolNames: string[],
    existingSkills: Skill[],
    closestSkill: Skill | undefined,
    closestOverlap: number,
  ): Promise<AssessmentResult> {
    const toolList =
      availableToolNames.length > 0
        ? availableToolNames.join(", ")
        : "run_shell_command, read_file, write_file, web_crawl";

    const skillList =
      existingSkills.length > 0
        ? existingSkills.map((s) => `• ${s.name}: ${s.description}`).join("\n")
        : "(none registered yet)";

    const prompt =
      `You are a STRICT capability analyst for an AI assistant. Answer in JSON only.\n\n` +
      `User request: "${userRequest}"\n\n` +
      `Available primitive tools: ${toolList}\n\n` +
      `Existing skills (higher-level task definitions):\n${skillList}\n\n` +
      `Answer these two questions:\n\n` +
      `Q1 — REQUEST TYPE: Classify the request into exactly one category:\n` +
      `  CONVERSATIONAL: greetings, thanks, small talk, yes/no replies\n` +
      `  INFORMATIONAL: asking for facts, definitions, explanations, opinions\n` +
      `  ANALYTICAL: summarize/analyze/compare content the user is providing\n` +
      `  OPERATIONAL: requires performing a SYSTEM ACTION (file I/O, network call, OS control, sending something, controlling hardware/software, running a process)\n\n` +
      `Q2 — COVERAGE: Can the user's request be fully accomplished using the primitive tools AND/OR existing skills listed above?\n` +
      `  Answer YES only if a clear execution path exists using what is listed.\n` +
      `  Answer NO if the request requires a capability genuinely absent from the list.\n\n` +
      `Return ONLY this JSON (no prose, no fences):\n` +
      `{\n` +
      `  "request_type": "CONVERSATIONAL|INFORMATIONAL|ANALYTICAL|OPERATIONAL",\n` +
      `  "covered_by_existing": true|false,\n` +
      `  "covering_skill_or_tool": "name of the tool/skill that covers it, or null",\n` +
      `  "reasoning": "one sentence explaining your decision"\n` +
      `}`;

    const response = await this.provider.chat(
      [{ role: "user", content: prompt }],
      undefined,
      { temperature: 0, maxTokens: 256 },
    );

    const match = response.content.match(/\{[\s\S]*\}/);
    if (!match) throw new Error("No JSON in assessment response");

    const parsed = JSON.parse(match[0]) as {
      request_type: string;
      covered_by_existing: boolean;
      covering_skill_or_tool: string | null;
      reasoning: string;
    };

    const requestType = (
      ["CONVERSATIONAL", "INFORMATIONAL", "ANALYTICAL", "OPERATIONAL"].includes(
        parsed.request_type,
      )
        ? parsed.request_type
        : "OPERATIONAL"
    ) as RequestType;

    // SKIP for non-operational requests
    if (requestType === "CONVERSATIONAL" || requestType === "INFORMATIONAL") {
      return {
        verdict: "SKIP",
        requestType,
        reasoning:
          parsed.reasoning ?? "Non-operational request — synthesis skipped.",
      };
    }

    // ANALYTICAL requests can almost always be handled by existing tools
    if (requestType === "ANALYTICAL" && parsed.covered_by_existing) {
      return {
        verdict: "COVERED",
        requestType,
        reasoning: parsed.reasoning,
        suggestedExistingSkill: parsed.covering_skill_or_tool ?? undefined,
      };
    }

    // COVERED — existing capability handles it
    if (parsed.covered_by_existing) {
      return {
        verdict: "COVERED",
        requestType,
        reasoning: parsed.reasoning,
        suggestedExistingSkill: parsed.covering_skill_or_tool ?? undefined,
      };
    }

    // Near-duplicate check with LLM-confirmed overlap
    if (closestSkill && closestOverlap >= 0.6) {
      return {
        verdict: "NEAR_DUPLICATE",
        requestType,
        reasoning: `Similar skill "${closestSkill.name}" exists. ${parsed.reasoning}`,
        suggestedExistingSkill: closestSkill.name,
        overlapScore: closestOverlap,
      };
    }

    // Genuine gap — allow synthesis
    return {
      verdict: "SYNTHESIZE",
      requestType,
      reasoning: parsed.reasoning,
    };
  }
}
