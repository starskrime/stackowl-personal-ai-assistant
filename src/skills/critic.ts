/**
 * StackOwl — Skill Critic
 *
 * Evaluates the quality of a skill on three dimensions:
 *
 *   1. NAME CLARITY   — Would a user's natural phrasing trigger this skill?
 *                       "fetch_and_summarize" is good. "skill_2" is terrible.
 *
 *   2. INSTRUCTION CLARITY — Are the instructions specific and actionable?
 *                           Generic prose ("use tools to complete the task") scores 0.
 *                           Concrete steps with exact commands score 1.
 *
 *   3. TRIGGER PRECISION — Is the trigger scope right?
 *                          Too broad: fires for every message (noise).
 *                          Too narrow: never fires even when relevant.
 *
 * Returns a CritiqueResult with a 0-1 score per dimension, an overall score,
 * and specific improvement suggestions the SkillEvolver uses to rewrite.
 *
 * Inspired by:
 *   - Self-Refine (Madaan et al., 2023): iterative feedback before revision
 *   - Constitutional AI: critique → revision loop
 */

import type { ModelProvider } from '../providers/base.js';
import type { Skill } from './types.js';
import { log } from '../logger.js';

// ─── Types ───────────────────────────────────────────────────────

export interface DimensionScore {
  score: number;         // 0.0–1.0
  feedback: string;      // specific, actionable criticism
}

export interface CritiqueResult {
  skillName: string;
  nameClarityScore: DimensionScore;
  instructionClarityScore: DimensionScore;
  triggerPrecisionScore: DimensionScore;
  /** Weighted average: name×0.25, instructions×0.5, trigger×0.25 */
  overallScore: number;
  /** Needs rewrite if overall < REWRITE_THRESHOLD */
  needsRewrite: boolean;
  /** Concrete rewrite prompt — what the evolver should tell the LLM to fix */
  rewriteDirective: string;
}

const REWRITE_THRESHOLD = 0.65;

// ─── Heuristic pre-filter (no LLM needed for obvious failures) ────

const GENERIC_NAME_PATTERNS = [
  /^skill_?\d+$/i,
  /^synthesized_skill/i,
  /^custom_skill/i,
  /^new_skill/i,
  /^tool_?\d+$/i,
  /^(handler|processor|wrapper|helper)$/i,
];

function isGenericName(name: string): boolean {
  return GENERIC_NAME_PATTERNS.some(p => p.test(name));
}

function hasConcreteInstructions(instructions: string): boolean {
  // Concrete instructions contain: numbered steps, shell commands, specific tool calls
  const concreteMarkers = [
    /\d+\.\s+/,                // numbered list
    /run_shell_command|read_file|write_file|web_crawl|web_fetch/i,
    /```[\s\S]+```/,           // code block
    /\$\s*\w+|`[^`]+`/,       // inline commands
    /step \d+/i,
  ];
  return concreteMarkers.some(p => p.test(instructions));
}

// ─── Critic ──────────────────────────────────────────────────────

export class SkillCritic {
  constructor(private provider: ModelProvider) {}

  /**
   * Evaluate a skill. Uses a single LLM call for all three dimensions.
   * Falls back to heuristic scoring if the LLM call fails.
   */
  async critique(skill: Skill): Promise<CritiqueResult> {
    try {
      return await this.critiqueWithLLM(skill);
    } catch (err) {
      log.engine.warn(`[SkillCritic] LLM critique failed for "${skill.name}", using heuristic: ${err instanceof Error ? err.message : String(err)}`);
      return this.critiqueHeuristic(skill);
    }
  }

  private async critiqueWithLLM(skill: Skill): Promise<CritiqueResult> {
    const prompt =
      `You are a quality evaluator for AI assistant skills (SKILL.md files).\n` +
      `Rate the following skill on three dimensions (each 0.0–1.0) and give specific feedback.\n\n` +
      `SKILL:\n` +
      `Name: ${skill.name}\n` +
      `Description: ${skill.description}\n` +
      `Instructions:\n${skill.instructions.slice(0, 800)}\n\n` +
      `Scoring dimensions:\n` +
      `1. NAME_CLARITY (0-1): Would a user's natural phrasing ("send me weather", "take a screenshot")\n` +
      `   trigger this skill by keyword? Generic names (skill_1, synthesized_skill, handler) = 0.0.\n` +
      `   Descriptive verb-noun names (summarize_webpage, send_daily_brief) = 1.0.\n\n` +
      `2. INSTRUCTION_CLARITY (0-1): Are instructions concrete and actionable?\n` +
      `   Vague prose ("use appropriate tools to complete the task") = 0.0.\n` +
      `   Numbered steps with exact tool calls/commands = 1.0.\n\n` +
      `3. TRIGGER_PRECISION (0-1): Is the trigger scope right?\n` +
      `   Too broad (would fire for ANY message) = 0.1. Too narrow (would never fire) = 0.1.\n` +
      `   Fires for exactly the right family of requests = 1.0.\n\n` +
      `Return ONLY this JSON (no prose, no code fences):\n` +
      `{\n` +
      `  "name_clarity": { "score": 0.0, "feedback": "exact issue and how to fix it" },\n` +
      `  "instruction_clarity": { "score": 0.0, "feedback": "exact issue and how to fix it" },\n` +
      `  "trigger_precision": { "score": 0.0, "feedback": "exact issue and how to fix it" },\n` +
      `  "rewrite_directive": "One paragraph telling the rewriter exactly what to change and why"\n` +
      `}`;

    const response = await this.provider.chat(
      [{ role: 'user', content: prompt }],
      undefined,
      { temperature: 0, maxTokens: 512 },
    );

    const match = response.content.match(/\{[\s\S]*\}/);
    if (!match) throw new Error('No JSON in LLM critique response');

    const parsed = JSON.parse(match[0]) as {
      name_clarity: { score: number; feedback: string };
      instruction_clarity: { score: number; feedback: string };
      trigger_precision: { score: number; feedback: string };
      rewrite_directive: string;
    };

    const nameScore = Math.max(0, Math.min(1, parsed.name_clarity.score));
    const instrScore = Math.max(0, Math.min(1, parsed.instruction_clarity.score));
    const trigScore = Math.max(0, Math.min(1, parsed.trigger_precision.score));
    const overall = nameScore * 0.25 + instrScore * 0.5 + trigScore * 0.25;

    return {
      skillName: skill.name,
      nameClarityScore: { score: nameScore, feedback: parsed.name_clarity.feedback },
      instructionClarityScore: { score: instrScore, feedback: parsed.instruction_clarity.feedback },
      triggerPrecisionScore: { score: trigScore, feedback: parsed.trigger_precision.feedback },
      overallScore: overall,
      needsRewrite: overall < REWRITE_THRESHOLD,
      rewriteDirective: parsed.rewrite_directive ?? '',
    };
  }

  /** Fast heuristic critique — no LLM call */
  critiqueHeuristic(skill: Skill): CritiqueResult {
    // Name clarity
    let nameScore = 0.8;
    let nameFeedback = 'Name looks descriptive.';
    if (isGenericName(skill.name)) {
      nameScore = 0.1;
      nameFeedback = `Name "${skill.name}" is generic. Use a verb-noun name like "summarize_webpage" or "send_weather_brief".`;
    } else if (skill.name.length < 5) {
      nameScore = 0.3;
      nameFeedback = 'Name is too short to be descriptive.';
    } else if (!skill.name.includes('_')) {
      nameScore = 0.6;
      nameFeedback = 'Name should use snake_case with a verb (e.g., fetch_news, send_summary).';
    }

    // Instruction clarity
    const hasSteps = hasConcreteInstructions(skill.instructions);
    const instrScore = hasSteps ? 0.8 : 0.3;
    const instrFeedback = hasSteps
      ? 'Instructions contain concrete steps.'
      : 'Instructions are vague. Add numbered steps with exact tool calls (run_shell_command, read_file, etc.).';

    // Trigger precision — heuristic: description length and specificity
    const descWords = skill.description.split(/\s+/).length;
    let trigScore = 0.7;
    let trigFeedback = 'Trigger scope seems reasonable.';
    if (descWords < 4) {
      trigScore = 0.3;
      trigFeedback = 'Description is too short to define a precise trigger. Add what type of user request activates this.';
    } else if (skill.description.toLowerCase().includes('anything') || skill.description.toLowerCase().includes('all tasks')) {
      trigScore = 0.1;
      trigFeedback = 'Description is too broad — will trigger for everything. Narrow down to a specific domain.';
    }

    const overall = nameScore * 0.25 + instrScore * 0.5 + trigScore * 0.25;

    const issues: string[] = [];
    if (nameScore < 0.6) issues.push(`Name: ${nameFeedback}`);
    if (instrScore < 0.6) issues.push(`Instructions: ${instrFeedback}`);
    if (trigScore < 0.6) issues.push(`Trigger: ${trigFeedback}`);

    return {
      skillName: skill.name,
      nameClarityScore: { score: nameScore, feedback: nameFeedback },
      instructionClarityScore: { score: instrScore, feedback: instrFeedback },
      triggerPrecisionScore: { score: trigScore, feedback: trigFeedback },
      overallScore: overall,
      needsRewrite: overall < REWRITE_THRESHOLD,
      rewriteDirective: issues.length > 0
        ? `Rewrite this skill addressing: ${issues.join('; ')}`
        : 'Minor improvements only.',
    };
  }
}
