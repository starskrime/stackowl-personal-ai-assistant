/**
 * StackOwl — Skill Evolver
 *
 * The self-improvement engine for skills. Runs during idle time to:
 *
 *   1. CRITIQUE — score every skill with SkillCritic
 *   2. REWRITE  — rewrite low-scoring skills using the critique as context
 *   3. VALIDATE — parse the rewritten SKILL.md to ensure it's well-formed
 *   4. PERSIST  — overwrite the original file only if the rewrite is better
 *
 * This implements the Self-Refine loop (Madaan et al., 2023):
 *   generate → critique → refine → critique → stop if score ≥ threshold
 *
 * Max 2 refinement iterations per skill to avoid infinite loops.
 * Skills that score ≥ 0.65 are left untouched.
 */

import { writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';
import { SkillCritic, type CritiqueResult } from './critic.js';
import { SkillParser } from './parser.js';
import type { SkillsRegistry } from './registry.js';
import type { Skill } from './types.js';
import { log } from '../logger.js';

// ─── Types ───────────────────────────────────────────────────────

export interface SkillEvolutionEntry {
  skillName: string;
  originalScore: number;
  finalScore: number;
  iterations: number;
  improved: boolean;
  timestamp: string;
}

export interface SkillEvolutionReport {
  evaluated: number;
  improved: number;
  unchanged: number;
  failed: number;
  entries: SkillEvolutionEntry[];
}

// ─── Evolver ─────────────────────────────────────────────────────

export class SkillEvolver {
  private critic: SkillCritic;
  private parser: SkillParser;
  private static readonly MAX_ITERATIONS = 2;
  private static readonly QUALITY_THRESHOLD = 0.65;

  constructor(
    private provider: ModelProvider,
    private config: StackOwlConfig,
  ) {
    this.critic = new SkillCritic(provider);
    this.parser = new SkillParser();
  }

  /**
   * Run a full evolution pass over all skills in the registry.
   * Called during idle time (5 AM via ProactivePinger).
   */
  async evolveAll(registry: SkillsRegistry): Promise<SkillEvolutionReport> {
    const skills = registry.listEnabled();
    log.engine.info(`[SkillEvolver] Starting evolution pass over ${skills.length} skill(s)...`);

    const report: SkillEvolutionReport = {
      evaluated: 0,
      improved: 0,
      unchanged: 0,
      failed: 0,
      entries: [],
    };

    for (const skill of skills) {
      // Skip skills without a known source path (built-in, non-file skills)
      if (!skill.sourcePath || skill.sourcePath === 'unknown') continue;

      try {
        const entry = await this.evolveSkill(skill);
        report.evaluated++;
        report.entries.push(entry);
        if (entry.improved) report.improved++;
        else report.unchanged++;
      } catch (err) {
        report.failed++;
        log.engine.warn(`[SkillEvolver] Failed to evolve "${skill.name}": ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    // Reload registry so the improved skills are live immediately
    if (report.improved > 0) {
      for (const entry of report.entries.filter(e => e.improved)) {
        const improved = registry.get(entry.skillName);
        if (improved) {
          // Re-parse and re-register
          try {
            const reParsed = await this.parser.parse(improved.sourcePath);
            registry.register(reParsed);
          } catch { /* non-fatal */ }
        }
      }
      log.engine.info(`[SkillEvolver] ✓ Evolution complete: ${report.improved}/${report.evaluated} skills improved`);
    } else {
      log.engine.info(`[SkillEvolver] All skills already at quality threshold — no rewrites needed`);
    }

    return report;
  }

  /**
   * Evolve a single skill: critique → rewrite → re-critique → persist if better.
   */
  async evolveSkill(skill: Skill): Promise<SkillEvolutionEntry> {
    const critique = await this.critic.critique(skill);
    const originalScore = critique.overallScore;

    const entry: SkillEvolutionEntry = {
      skillName: skill.name,
      originalScore,
      finalScore: originalScore,
      iterations: 0,
      improved: false,
      timestamp: new Date().toISOString(),
    };

    if (!critique.needsRewrite) {
      log.engine.info(`[SkillEvolver] "${skill.name}" score ${originalScore.toFixed(2)} — no rewrite needed`);
      return entry;
    }

    log.engine.info(`[SkillEvolver] "${skill.name}" score ${originalScore.toFixed(2)} — starting Self-Refine loop`);

    let currentContent = await this.readSkillFile(skill.sourcePath);
    let bestContent = currentContent;
    let bestScore = originalScore;
    let currentCritique = critique;

    for (let i = 0; i < SkillEvolver.MAX_ITERATIONS; i++) {
      entry.iterations++;

      const rewritten = await this.rewrite(skill, currentContent, currentCritique);
      if (!rewritten) break;

      // Validate the rewrite is parseable
      let rewrittenSkill: Skill;
      try {
        rewrittenSkill = this.parser.parseContent(rewritten, skill.sourcePath);
      } catch (parseErr) {
        log.engine.warn(`[SkillEvolver] Rewrite of "${skill.name}" iteration ${i + 1} failed validation: ${parseErr instanceof Error ? parseErr.message : String(parseErr)}`);
        break;
      }

      // Re-critique the rewrite
      const newCritique = await this.critic.critique(rewrittenSkill);
      log.engine.info(`[SkillEvolver] "${skill.name}" iteration ${i + 1}: ${originalScore.toFixed(2)} → ${newCritique.overallScore.toFixed(2)}`);

      if (newCritique.overallScore > bestScore) {
        bestContent = rewritten;
        bestScore = newCritique.overallScore;
      }

      if (newCritique.overallScore >= SkillEvolver.QUALITY_THRESHOLD) {
        // Good enough — stop iterating
        break;
      }

      currentContent = rewritten;
      currentCritique = newCritique;
    }

    // Only write if we actually improved
    if (bestScore > originalScore + 0.05) {
      await this.writeSkillFile(skill.sourcePath, bestContent);
      entry.finalScore = bestScore;
      entry.improved = true;
      log.engine.info(`[SkillEvolver] ✓ "${skill.name}" improved: ${originalScore.toFixed(2)} → ${bestScore.toFixed(2)}`);
    } else {
      log.engine.info(`[SkillEvolver] "${skill.name}" rewrite did not improve score sufficiently — keeping original`);
    }

    return entry;
  }

  /**
   * Ask the LLM to rewrite a skill given specific critique feedback.
   */
  private async rewrite(
    skill: Skill,
    currentContent: string,
    critique: CritiqueResult,
  ): Promise<string | null> {
    const feedbackBlock =
      `CRITIQUE FEEDBACK (must address ALL of these):\n` +
      `- Name clarity (${critique.nameClarityScore.score.toFixed(2)}): ${critique.nameClarityScore.feedback}\n` +
      `- Instruction clarity (${critique.instructionClarityScore.score.toFixed(2)}): ${critique.instructionClarityScore.feedback}\n` +
      `- Trigger precision (${critique.triggerPrecisionScore.score.toFixed(2)}): ${critique.triggerPrecisionScore.feedback}\n` +
      `\nSpecific rewrite directive: ${critique.rewriteDirective}`;

    const prompt =
      `You are rewriting a StackOwl SKILL.md to improve its quality.\n\n` +
      `CURRENT SKILL.md:\n${currentContent}\n\n` +
      `${feedbackBlock}\n\n` +
      `RULES FOR THE REWRITE:\n` +
      `1. The name must be a descriptive snake_case verb-noun (e.g., summarize_webpage, send_morning_brief)\n` +
      `2. The description must be 1 clear sentence: "When [trigger], do [action]"\n` +
      `3. Instructions must have numbered steps with exact tool calls:\n` +
      `   - run_shell_command(command): for shell operations\n` +
      `   - read_file(path): for reading files\n` +
      `   - write_file(path, content): for writing files\n` +
      `   - web_crawl(url): for fetching web pages\n` +
      `4. Keep the frontmatter format: ---\\nname: ...\\ndescription: ...\\nopenclaw:\\n  emoji: ...\\n---\n` +
      `5. Keep the same core purpose — just make it clearer\n\n` +
      `Output ONLY the complete rewritten SKILL.md content. No explanation, no fences.`;

    const response = await this.provider.chat(
      [{ role: 'user', content: prompt }],
      this.config.defaultModel,
      { temperature: 0.3, maxTokens: 1024 },
    );

    const content = response.content.trim();

    // Must contain frontmatter markers
    if (!content.includes('---') || !content.includes('name:')) {
      log.engine.warn(`[SkillEvolver] Rewrite for "${skill.name}" missing frontmatter`);
      return null;
    }

    return content;
  }

  private async readSkillFile(sourcePath: string): Promise<string> {
    const { readFile } = await import('node:fs/promises');
    return readFile(sourcePath, 'utf-8');
  }

  private async writeSkillFile(sourcePath: string, content: string): Promise<void> {
    const dir = dirname(sourcePath);
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(sourcePath, content, 'utf-8');
  }
}
