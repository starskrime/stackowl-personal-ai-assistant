/**
 * StackOwl — Skills Registry
 *
 * Manages loaded skills and provides filtering/lookup.
 */

import { readdir } from "node:fs/promises";
import { SkillParser, meetsRequirements } from "./parser.js";
import type { Skill, SkillFilter } from "./types.js";

export class SkillsRegistry {
  private skills: Map<string, Skill> = new Map();
  private parser: SkillParser;

  constructor() {
    this.parser = new SkillParser();
  }

  /**
   * Add a skill to the registry.
   */
  register(skill: Skill): void {
    this.skills.set(skill.name.toLowerCase(), skill);
  }

  /**
   * Remove a skill by name.
   */
  unregister(name: string): boolean {
    return this.skills.delete(name.toLowerCase());
  }

  /**
   * Get a skill by name.
   */
  get(name: string): Skill | undefined {
    return this.skills.get(name.toLowerCase());
  }

  /**
   * List all registered skills.
   */
  listAll(): Skill[] {
    return Array.from(this.skills.values());
  }

  /**
   * List enabled skills.
   */
  listEnabled(): Skill[] {
    return this.listAll().filter((s) => s.enabled);
  }

  /**
   * Get reactive (behavioral) skills for a specific owl.
   * Returns skills where conditions.length > 0 and relevantOwls includes owlName or "*".
   */
  getBehavioral(owlName: string): Skill[] {
    const name = owlName.toLowerCase();
    return this.listEnabled().filter((skill) => {
      if (!skill.conditions || skill.conditions.length === 0) return false;
      const owls = skill.relevantOwls ?? ["*"];
      return owls.some((o) => o === "*" || o.toLowerCase() === name);
    });
  }

  /**
   * Get skills that meet the given requirements.
   */
  getEligible(filter: SkillFilter): Skill[] {
    return this.listEnabled().filter((skill) => {
      // Always include if 'always' flag is set
      if (skill.metadata.openclaw?.always) {
        return true;
      }

      const result = meetsRequirements(skill, {
        os: filter.os,
        bins: filter.bins,
        env: filter.env,
        config: filter.config,
      });

      return result.satisfied;
    });
  }

  /**
   * Get skills that are missing requirements.
   */
  getIneligible(): { skill: Skill; missing: string[] }[] {
    const result: { skill: Skill; missing: string[] }[] = [];

    for (const skill of this.listEnabled()) {
      if (skill.metadata.openclaw?.always) {
        continue;
      }

      const check = meetsRequirements(skill, {
        os: process.platform as NodeJS.Platform,
        bins: [], // Would need to check PATH
        env: process.env as Record<string, string>,
        config: {},
      });

      if (!check.satisfied) {
        result.push({ skill, missing: check.missing });
      }
    }

    return result;
  }

  /**
   * Enable a skill.
   */
  enable(name: string): boolean {
    const skill = this.get(name);
    if (!skill) return false;
    skill.enabled = true;
    return true;
  }

  /**
   * Disable a skill.
   */
  disable(name: string): boolean {
    const skill = this.get(name);
    if (!skill) return false;
    skill.enabled = false;
    return true;
  }

  /**
   * Load skills from a directory.
   */
  async loadFromDirectory(dirPath: string): Promise<number> {
    let entries: string[];
    try {
      const dirEntries = await readdir(dirPath, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return 0;
    }

    let loaded = 0;
    for (const entry of entries) {
      const mdPath = `${dirPath}/${entry}/SKILL.md`;
      try {
        const skill = await this.parser.parse(mdPath);
        this.register(skill);
        loaded++;
      } catch (error) {
        // Silently skip invalid skills
        const msg = error instanceof Error ? error.message : String(error);
        console.log(`[SkillsRegistry] Skipped ${entry}: ${msg}`);
      }
    }

    return loaded;
  }

  /**
   * Format skills for LLM context injection.
   */
  formatForContext(skills: Skill[]): string {
    if (skills.length === 0) {
      return "";
    }

    const lines: string[] = ["<skills>"];

    for (const skill of skills) {
      lines.push(`<skill>`);
      lines.push(`<name>${skill.name}</name>`);
      lines.push(`<description>${skill.description}</description>`);
      lines.push(`<instructions>${skill.instructions}</instructions>`);
      lines.push(`</skill>`);
    }

    lines.push("</skills>");
    return lines.join("\n");
  }

  /**
   * Format a single skill for LLM context.
   */
  formatForContextSingle(skill: Skill): string {
    return `<skill>\n<name>${skill.name}</name>\n<description>${skill.description}</description>\n<instructions>${skill.instructions}</instructions>\n</skill>`;
  }
}
