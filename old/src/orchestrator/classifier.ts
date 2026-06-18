/**
 * StackOwl — Strategy Classifier
 *
 * Single LLM call that replaces both shouldConveneParliament() AND
 * shouldUsePlanner() with a rich execution strategy decision.
 *
 * Returns a TaskStrategy with strategy type, owl assignments, subtasks,
 * and reasoning — all from one model call.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { TaskStrategy, StrategyType } from "./types.js";
import { log } from "../logger.js";

// ─── Quick Exit Patterns ─────────────────────────────────────

const GREETING_PATTERNS =
  /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|goodbye|good morning|good evening|gm|gn)\b/i;

function isTrivia(text: string): boolean {
  const trimmed = text.trim();
  if (GREETING_PATTERNS.test(trimmed)) return true;
  return false;
}

// ─── Default Strategy ────────────────────────────────────────

function makeDefault(owlName: string): TaskStrategy {
  return {
    strategy: "STANDARD",
    reasoning: "Default strategy",
    confidence: 0.5,
    depth: "quick",
    owlAssignments: [{ owlName, role: "lead", reasoning: "Default owl" }],
  };
}

function makeDirect(owlName: string): TaskStrategy {
  return {
    strategy: "DIRECT",
    reasoning: "Trivial message, no tools needed",
    confidence: 1.0,
    depth: "quick",
    owlAssignments: [{ owlName, role: "lead", reasoning: "Default owl" }],
  };
}

// ─── Owl Summary for Prompt ──────────────────────────────────

function formatOwlsForPrompt(owls: OwlInstance[]): string {
  return owls
    .map((owl) => {
      const specialties = owl.persona.specialties?.join(", ") || "general";
      const expertise = owl.dna.expertiseGrowth
        ? Object.entries(owl.dna.expertiseGrowth)
            .sort(([, a], [, b]) => b - a)
            .slice(0, 3)
            .map(([k, v]) => `${k}(${v.toFixed(1)})`)
            .join(", ")
        : "";
      return `- ${owl.persona.name} (${owl.persona.type}): specialties=[${specialties}], challenge=${owl.dna.evolvedTraits.challengeLevel}${expertise ? `, expertise=[${expertise}]` : ""}`;
    })
    .join("\n");
}

// ─── Research Intent Detection ─────────────────────────────────

const RESEARCH_PATTERNS: Array<{ pattern: RegExp; label: string }> = [
  // Explicit research verbs
  {
    pattern:
      /\b(do research|research|investigate|deep.?(?:search|dive|look)|look into)\b/i,
    label: "research-verb",
  },
  // Comparison queries
  {
    pattern: /\bcompare\s+[^?]+\s+(?:vs|versus|against|and)\s+[^?]+/i,
    label: "comparison",
  },
  // Multi-part / thorough queries
  {
    pattern:
      /\b(tell me everything|explain in depth|comprehensive|thorough|complete picture|full analysis)\b/i,
    label: "thorough",
  },
  // Multi-question (3+ ?)
  { pattern: /\?.*\?.*\?/s, label: "multi-question" },
  // Long research questions (50+ words with multiple keywords)
  { pattern: /^(.{200,})$/s, label: "long-research" },
  // "How do I..." with multiple sub-questions
  { pattern: /\b(how\s+do\s+(?:i|we|they))\b.*\?/i, label: "how-to-deep" },
  // "Everything about" queries
  { pattern: /\beverything\s+about\b/i, label: "everything-about" },
];

const RESEARCH_KEYWORD_COUNT = 3;

function detectResearchIntent(text: string): {
  isDeep: boolean;
  reason: string;
  subtopics: string[];
} {
  const trimmed = text.trim();
  const matchedLabels: string[] = [];

  for (const { pattern, label } of RESEARCH_PATTERNS) {
    if (pattern.test(trimmed)) {
      matchedLabels.push(label);
    }
  }

  // Count research-action keywords
  const researchKeywords = trimmed.match(
    /\b(search|find|lookup|check|analyze|compare|investigate|research|explore|review|evaluate|assess|examine|look up|gather|collect)\b/gi,
  );
  const keywordCount = researchKeywords ? researchKeywords.length : 0;

  // Word count check
  const wordCount = trimmed.split(/\s+/).length;

  // Extract subtopics: split on "and", ",", ";" that are near research content
  const subtopicSplit = trimmed
    .split(/\s*(?:,|;|\band\b|\bvs\.?\b|\bversus\b)\s*/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10 && s.length < 100);

  // Multi-subtopic detection: 3+ distinct comma/and-separated concepts
  const hasMultiSubtopics = subtopicSplit.length >= 3;

  // Decision logic
  if (
    matchedLabels.includes("research-verb") ||
    matchedLabels.includes("comparison")
  ) {
    return {
      isDeep: true,
      reason: matchedLabels.includes("comparison")
        ? "comparison query detected"
        : "explicit research request",
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (
    matchedLabels.includes("thorough") ||
    matchedLabels.includes("everything-about")
  ) {
    return {
      isDeep: true,
      reason: "thorough/comprehensive request",
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (matchedLabels.includes("multi-question") && wordCount >= 30) {
    return {
      isDeep: true,
      reason: `multi-question research (${matchedLabels.join(", ")})`,
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (
    matchedLabels.includes("long-research") &&
    keywordCount >= RESEARCH_KEYWORD_COUNT
  ) {
    return {
      isDeep: true,
      reason: `long research query (${wordCount} words, ${keywordCount} research keywords)`,
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (hasMultiSubtopics && keywordCount >= 2 && wordCount >= 40) {
    return {
      isDeep: true,
      reason: `multi-subtopic research (${subtopicSplit.length} distinct aspects)`,
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  return { isDeep: false, reason: "", subtopics: [] };
}

// ─── Classify Strategy ───────────────────────────────────────

/**
 * Classify a user message into an execution strategy.
 * Returns a TaskStrategy with strategy type, owl assignments, and optional subtasks.
 */
export async function classifyStrategy(
  userMessage: string,
  owls: OwlInstance[],
  toolNames: string[],
  recentContext: ChatMessage[],
  provider: ModelProvider,
): Promise<TaskStrategy> {
  const defaultOwl =
    owls.find((o) => o.persona.name === "Noctua")?.persona.name ??
    owls[0]?.persona.name ??
    "Noctua";

  // Quick exit for trivial messages
  if (isTrivia(userMessage)) {
    return makeDirect(defaultOwl);
  }

  const owlSummary = formatOwlsForPrompt(owls);
  const toolSummary = toolNames.slice(0, 20).join(", ");
  const contextSummary = recentContext
    .slice(-3)
    .map((m) => `${m.role}: ${(m.content ?? "").slice(0, 150)}`)
    .join("\n");

  const prompt =
    `You are a task routing classifier for an AI assistant with multiple specialist agents (owls). ` +
    `Given a user message, decide the optimal execution strategy.\n\n` +
    `AVAILABLE OWLS:\n${owlSummary}\n\n` +
    `AVAILABLE TOOLS: ${toolSummary}\n\n` +
    (contextSummary ? `RECENT CONVERSATION:\n${contextSummary}\n\n` : "") +
    `STRATEGIES:\n` +
    `- DIRECT: Simple greetings, thanks, trivial questions. No tools. Use default owl (Noctua).\n` +
    `- STANDARD: Most requests. Single owl with tool access. Default when unsure.\n` +
    `- SPECIALIST: Task clearly falls into one owl's domain. Route to that specialist.\n` +
    `- PLANNED: Multi-step work with sequential dependencies. Decompose into subtasks with dependsOn.\n` +
    `- PARLIAMENT: A decision, dilemma, or tradeoff where multiple perspectives genuinely help. Select 2-5 relevant owls.\n` +
    `- SWARM: Multiple INDEPENDENT subtasks that each benefit from a different specialist. Each runs in parallel.\n\n` +
    `RULES:\n` +
    `- PLANNED is REQUIRED (not optional) for ANY request with 3+ distinct sequential steps, ` +
    `multi-phase workflows, or tasks where the output of one step feeds into the next ` +
    `(e.g. "build X, then test it", "research Y then write a report", "first create Z then deploy it").\n` +
    `- PLANNED is REQUIRED when the user says "first... then...", "after that", "step by step", ` +
    `"phase 1... phase 2", or describes a workflow with clear sequential dependencies.\n` +
    `- SWARM is REQUIRED when 2+ independent specialist subtasks exist that have absolutely NO data dependency between them.\n` +
    `- STANDARD is for single-phase requests that need tools but have no sequential dependencies (lookups, single writes, calculations).\n` +
    `- DIRECT is ONLY for greetings, thanks, trivial one-sentence questions needing zero tools.\n` +
    `- SPECIALIST when the task clearly belongs to one owl's domain AND is single-phase.\n` +
    `- PARLIAMENT only for genuine value, ethical, or architectural tradeoff dilemmas — NOT factual questions.\n` +
    `- When deciding between STANDARD and PLANNED: if you would mentally decompose the task into ` +
    `sub-steps before executing, choose PLANNED.\n` +
    `- Never choose STANDARD for a task you would break into phases in your head.\n` +
    `- For PARLIAMENT: assign 2-5 owls based on topic relevance.\n` +
    `- For SWARM/PLANNED: provide subtasks with id, description, assignedOwl, dependsOn.\n\n` +
    `Respond with ONLY valid JSON:\n` +
    `{\n` +
    `  "strategy": "DIRECT|STANDARD|SPECIALIST|PLANNED|PARLIAMENT|SWARM",\n` +
    `  "reasoning": "one sentence explaining why",\n` +
    `  "confidence": 0.0-1.0,\n` +
    `  "owlAssignments": [{"owlName": "...", "role": "lead|reviewer|subtask:...", "reasoning": "..."}],\n` +
    `  "subtasks": [{"id": 1, "description": "...", "assignedOwl": "...", "dependsOn": [], "toolsNeeded": []}],\n` +
    `  "parliamentConfig": {"topic": "refined debate topic", "owlCount": 3}\n` +
    `}\n\n` +
    `Only include "subtasks" for PLANNED/SWARM. Only include "parliamentConfig" for PARLIAMENT.\n\n` +
    `USER MESSAGE: "${userMessage}"`;

  try {
    const response = await provider.chat(
      [{ role: "user", content: prompt }],
      undefined,
      { temperature: 0.1, maxTokens: 512 },
    );

    // Strip thinking tags before extracting JSON — MiniMax injects <think> blocks
    const cleanContent = response.content
      .replace(/<\/?think[^>]*>[\s\S]*?<\/?think[^>]*>/gi, "")
      .replace(/<think>[\s\S]*?<\/think>/gi, "");

    const jsonMatch = cleanContent.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      log.engine.warn(
        "[Classifier] No JSON in response, defaulting to STANDARD",
      );
      return makeDefault(defaultOwl);
    }

    const parsed = JSON.parse(jsonMatch[0]) as TaskStrategy;

    // Validate strategy type
    const validStrategies: StrategyType[] = [
      "DIRECT",
      "STANDARD",
      "SPECIALIST",
      "PLANNED",
      "PARLIAMENT",
      "SWARM",
    ];
    if (!validStrategies.includes(parsed.strategy)) {
      log.engine.warn(
        `[Classifier] Invalid strategy "${parsed.strategy}", defaulting to STANDARD`,
      );
      return makeDefault(defaultOwl);
    }

    // ── Post-parse enforcement: escalate STANDARD → PLANNED when multi-step signals
    // are present in the user message, regardless of what the LLM classified.
    // This catches cases where the classifier under-estimates task complexity.
    const multiStepSignals =
      /\b(first[\s\S]{0,40}then|after\s+that|next\s+step|step\s+\d|phase\s+\d|followed\s+by|and\s+then|set\s+up[\s\S]{0,30}and[\s\S]{0,30}(deploy|test|run|connect)|build[\s\S]{0,30}then|create[\s\S]{0,30}then|research[\s\S]{0,30}write|analyze[\s\S]{0,30}generate)\b/i;
    if (
      parsed.strategy === "STANDARD" &&
      (parsed.confidence == null || parsed.confidence < 0.75) &&
      multiStepSignals.test(userMessage)
    ) {
      log.engine.info(
        `[Classifier] Multi-step signals detected in message — escalating STANDARD → PLANNED ` +
        `(original confidence: ${parsed.confidence?.toFixed(2) ?? "n/a"})`,
      );
      parsed.strategy = "PLANNED";
      parsed.reasoning =
        `Multi-step sequential signals detected; auto-escalated from STANDARD to PLANNED.`;
    }

    // Ensure at least one owl assignment
    if (!parsed.owlAssignments || parsed.owlAssignments.length === 0) {
      parsed.owlAssignments = [
        { owlName: defaultOwl, role: "lead", reasoning: "Fallback" },
      ];
    }

    // Validate owl names exist
    const owlNames = new Set(owls.map((o) => o.persona.name));
    for (const assignment of parsed.owlAssignments) {
      if (!owlNames.has(assignment.owlName)) {
        // Try case-insensitive match
        const match = owls.find(
          (o) =>
            o.persona.name.toLowerCase() === assignment.owlName.toLowerCase(),
        );
        assignment.owlName = match?.persona.name ?? defaultOwl;
      }
    }

    // Validate subtask owl assignments
    if (parsed.subtasks) {
      for (const sub of parsed.subtasks) {
        if (!owlNames.has(sub.assignedOwl)) {
          const match = owls.find(
            (o) =>
              o.persona.name.toLowerCase() === sub.assignedOwl.toLowerCase(),
          );
          sub.assignedOwl = match?.persona.name ?? defaultOwl;
        }
      }
    }

    const researchSignal = detectResearchIntent(userMessage);

    log.engine.info(
      `[Classifier] "${userMessage.slice(0, 60)}..." → ${parsed.strategy} ` +
        `(confidence: ${parsed.confidence?.toFixed(2)}) ` +
        `owls: [${parsed.owlAssignments.map((a) => a.owlName).join(", ")}] ` +
        `reason: ${parsed.reasoning}` +
        (researchSignal.isDeep
          ? ` [DEEP RESEARCH: ${researchSignal.reason}]`
          : ""),
    );

    return {
      strategy: parsed.strategy ?? "STANDARD",
      reasoning: parsed.reasoning ?? "Default strategy",
      confidence: parsed.confidence ?? 0.5,
      depth: researchSignal.isDeep ? "deep" : "quick",
      researchSignal: researchSignal.isDeep
        ? {
            reason: researchSignal.reason,
            subtopics: researchSignal.subtopics,
            autoDetected: true,
          }
        : undefined,
      owlAssignments: parsed.owlAssignments ?? [
        { owlName: defaultOwl, role: "lead", reasoning: "Default" },
      ],
      subtasks: parsed.subtasks,
      parliamentConfig: parsed.parliamentConfig,
    };
  } catch (err) {
    log.engine.warn(
      `[Classifier] Failed: ${err instanceof Error ? err.message : String(err)}, defaulting to STANDARD`,
    );
    return makeDefault(defaultOwl);
  }
}
