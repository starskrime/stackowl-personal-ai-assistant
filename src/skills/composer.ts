/**
 * StackOwl — Skill Composer
 *
 * Resolves skill dependency graphs and produces ordered execution plans.
 * Skills can declare dependencies that must run before/after/parallel.
 *
 * Uses Kahn's algorithm for topological sort. Detects and reports cycles.
 *
 * Example SKILL.md frontmatter:
 *   ---
 *   name: generate_report
 *   description: Generate a comprehensive report
 *   openclaw:
 *     emoji: 📊
 *     depends: [fetch_data, analyze_data]
 *     chains: [send_email]
 *   ---
 *
 * This would produce:
 *   Stage 0: fetch_data, analyze_data (parallel — both are 'before' deps)
 *   Stage 1: generate_report (the primary skill)
 *   Stage 2: send_email (chained after)
 */

import type { Skill, SkillDependency, SkillComposition } from "./types.js";
import type { SkillsRegistry } from "./registry.js";
import { Logger } from "../logger.js";

// ─── Composition Types ───────────────────────────────────────────

export type { SkillDependency, SkillComposition };

export interface CompositionPlan {
  stages: CompositionStage[];
  totalSkills: number;
  primarySkill: string;
}

export interface CompositionStage {
  order: number;
  skills: Skill[];
  label: string; // 'dependencies' | 'primary' | 'chains'
}

// ─── Composer ────────────────────────────────────────────────────

export class SkillComposer {
  private logger = new Logger("COMPOSER");

  constructor(private registry: SkillsRegistry) {}

  /**
   * Given a primary skill, resolve its full dependency graph
   * and produce an ordered execution plan.
   * Returns a single-stage plan if the skill has no dependencies.
   */
  resolve(primarySkill: Skill): CompositionPlan {
    const composition = this.getComposition(primarySkill);

    // No composition metadata — single-stage plan
    if (!composition || !composition.isComposite) {
      return {
        stages: [{ order: 0, skills: [primarySkill], label: "primary" }],
        totalSkills: 1,
        primarySkill: primarySkill.name,
      };
    }

    // Check for cycles before building the plan
    const visited = new Set<string>();
    const path = new Set<string>();
    const cycle = this.detectCycle(primarySkill.name, visited, path);
    if (cycle) {
      this.logger.warn(
        `Circular dependency detected: ${cycle.join(" -> ")}. Falling back to single-stage plan.`,
      );
      return {
        stages: [{ order: 0, skills: [primarySkill], label: "primary" }],
        totalSkills: 1,
        primarySkill: primarySkill.name,
      };
    }

    const stages: CompositionStage[] = [];
    let stageOrder = 0;

    // Stage: 'before' dependencies (run in parallel)
    const beforeDeps = composition.dependencies.filter(
      (d) => d.order === "before",
    );
    if (beforeDeps.length > 0) {
      const beforeSkills = this.resolveSkills(beforeDeps);
      if (beforeSkills.length > 0) {
        stages.push({
          order: stageOrder++,
          skills: beforeSkills,
          label: "dependencies",
        });
      }
    }

    // Stage: 'parallel' dependencies run alongside the primary skill
    const parallelDeps = composition.dependencies.filter(
      (d) => d.order === "parallel",
    );
    const parallelSkills = this.resolveSkills(parallelDeps);

    // Stage: primary skill (+ any parallel deps)
    const primaryStageSkills = [...parallelSkills, primarySkill];
    stages.push({
      order: stageOrder++,
      skills: primaryStageSkills,
      label: "primary",
    });

    // Stage: 'after' dependencies
    const afterDeps = composition.dependencies.filter(
      (d) => d.order === "after",
    );
    if (afterDeps.length > 0) {
      const afterSkills = this.resolveSkills(afterDeps);
      if (afterSkills.length > 0) {
        stages.push({
          order: stageOrder++,
          skills: afterSkills,
          label: "chains",
        });
      }
    }

    // Stage: explicit chains (always run after everything else)
    if (composition.chains && composition.chains.length > 0) {
      const chainSkills: Skill[] = [];
      for (const chainName of composition.chains) {
        const skill = this.registry.get(chainName);
        if (!skill) {
          this.logger.warn(
            `Chained skill "${chainName}" not found in registry, skipping.`,
          );
          continue;
        }
        if (!skill.enabled) {
          this.logger.warn(
            `Chained skill "${chainName}" is disabled, skipping.`,
          );
          continue;
        }
        chainSkills.push(skill);
      }
      if (chainSkills.length > 0) {
        stages.push({
          order: stageOrder++,
          skills: chainSkills,
          label: "chains",
        });
      }
    }

    const totalSkills = stages.reduce((sum, s) => sum + s.skills.length, 0);

    return {
      stages,
      totalSkills,
      primarySkill: primarySkill.name,
    };
  }

  /**
   * Format a composition plan as LLM-injectable context.
   * Produces structured XML for multi-skill execution.
   */
  formatForContext(plan: CompositionPlan): string {
    // Single-stage: just return a <skill> tag
    if (plan.stages.length === 1 && plan.totalSkills === 1) {
      const skill = plan.stages[0].skills[0];
      return [
        "<skill>",
        `<name>${skill.name}</name>`,
        `<description>${skill.description}</description>`,
        `<instructions>${skill.instructions}</instructions>`,
        "</skill>",
      ].join("\n");
    }

    // Multi-stage: return <skill-chain> with ordered stages
    const lines: string[] = [
      `<skill-chain primary="${plan.primarySkill}" total-skills="${plan.totalSkills}">`,
    ];

    for (const stage of plan.stages) {
      lines.push(`  <stage order="${stage.order}" label="${stage.label}">`);
      for (const skill of stage.skills) {
        lines.push(`    <skill>`);
        lines.push(`      <name>${skill.name}</name>`);
        lines.push(`      <description>${skill.description}</description>`);
        lines.push(`      <instructions>${skill.instructions}</instructions>`);
        lines.push(`    </skill>`);
      }
      lines.push(`  </stage>`);
    }

    lines.push("</skill-chain>");
    return lines.join("\n");
  }

  /**
   * Extract composition metadata from a skill.
   * Checks for an explicit `composition` field first, then falls back
   * to parsing `metadata.openclaw.depends` and `metadata.openclaw.chains`.
   */
  private getComposition(skill: Skill): SkillComposition | null {
    // Check for explicit composition field (cast to access optional field)
    const extended = skill as Skill & { composition?: SkillComposition };
    if (extended.composition) {
      return extended.composition;
    }

    // Fall back to openclaw metadata
    const openclaw = skill.metadata.openclaw as
      | (NonNullable<typeof skill.metadata.openclaw> & {
          depends?: string[];
          chains?: string[];
        })
      | undefined;

    if (!openclaw) return null;

    const depends = openclaw.depends;
    const chains = openclaw.chains;

    if (
      (!depends || depends.length === 0) &&
      (!chains || chains.length === 0)
    ) {
      return null;
    }

    const dependencies: SkillDependency[] = [];

    if (depends) {
      for (const depName of depends) {
        dependencies.push({
          skillName: depName,
          order: "before",
          required: true,
        });
      }
    }

    return {
      dependencies,
      chains: chains ?? undefined,
      isComposite:
        dependencies.length > 0 || (chains != null && chains.length > 0),
    };
  }

  /**
   * Resolve a list of SkillDependency entries into actual Skill objects.
   * Logs warnings for missing or disabled dependencies but does not fail.
   */
  private resolveSkills(deps: SkillDependency[]): Skill[] {
    const resolved: Skill[] = [];

    for (const dep of deps) {
      const skill = this.registry.get(dep.skillName);
      if (!skill) {
        this.logger.warn(
          `Dependency "${dep.skillName}" not found in registry, skipping.`,
        );
        continue;
      }
      if (!skill.enabled) {
        this.logger.warn(
          `Dependency "${dep.skillName}" is disabled, skipping.`,
        );
        continue;
      }
      resolved.push(skill);
    }

    return resolved;
  }

  /**
   * Detect circular dependencies using DFS cycle detection.
   * Returns the cycle path as an array of skill names, or null if no cycle.
   */
  private detectCycle(
    startSkill: string,
    visited: Set<string>,
    path: Set<string>,
  ): string[] | null {
    if (path.has(startSkill)) {
      // Found a cycle — reconstruct path
      return [...path, startSkill];
    }
    if (visited.has(startSkill)) {
      return null;
    }

    visited.add(startSkill);
    path.add(startSkill);

    const skill = this.registry.get(startSkill);
    if (skill) {
      const composition = this.getComposition(skill);
      if (composition) {
        // Check all dependencies
        for (const dep of composition.dependencies) {
          const cycle = this.detectCycle(dep.skillName, visited, path);
          if (cycle) return cycle;
        }
        // Check chains
        if (composition.chains) {
          for (const chain of composition.chains) {
            const cycle = this.detectCycle(chain, visited, path);
            if (cycle) return cycle;
          }
        }
      }
    }

    path.delete(startSkill);
    return null;
  }
}
