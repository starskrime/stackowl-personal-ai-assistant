/**
 * StackOwl — Skill Reloadable Adapter
 *
 * Wraps a SKILL.md file as a ReloadableModule for incremental hot-reload.
 */

import { existsSync } from "node:fs";
import type { ReloadableModule, ModuleSnapshot } from "../types.js";
import type { SkillsRegistry } from "../../skills/registry.js";
import type { SkillParser } from "../../skills/parser.js";
import type { Skill } from "../../skills/types.js";

export class SkillReloadable implements ReloadableModule {
  readonly kind = "skill" as const;
  readonly dependsOn: string[] = [];
  version = 0;
  private currentSkill: Skill | null = null;

  constructor(
    readonly id: string,
    readonly filePath: string,
    private skillsRegistry: SkillsRegistry,
    private parser: SkillParser,
  ) {}

  async validate(): Promise<boolean> {
    if (!existsSync(this.filePath)) return false;

    try {
      // SkillParser.parse(filePath) reads the file and returns a Skill
      const skill = await this.parser.parse(this.filePath);
      return !!skill && !!skill.name;
    } catch {
      return false;
    }
  }

  async load(): Promise<void> {
    const skill = await this.parser.parse(this.filePath);
    if (!skill) {
      throw new Error(`Failed to parse skill from ${this.filePath}`);
    }
    this.skillsRegistry.register(skill);
    this.currentSkill = skill;
  }

  async unload(): Promise<void> {
    if (this.currentSkill) {
      this.skillsRegistry.unregister(this.currentSkill.name);
      this.currentSkill = null;
    }
  }

  snapshot(): ModuleSnapshot {
    return {
      moduleId: this.id,
      version: this.version,
      state: this.currentSkill ? { ...this.currentSkill } : null,
      timestamp: Date.now(),
    };
  }

  async restore(snapshot: ModuleSnapshot): Promise<void> {
    const savedSkill = snapshot.state as Skill | null;
    if (savedSkill) {
      this.skillsRegistry.register(savedSkill);
      this.currentSkill = savedSkill;
    }
    this.version = snapshot.version;
  }
}
