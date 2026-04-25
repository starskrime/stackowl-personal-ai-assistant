/**
 * StackOwl — Skills Migrator
 *
 * One-time migration: copies INSTINCT.md files in workspace/instincts/
 * to SKILL.md files in workspace/skills/.
 * Non-destructive — originals are left in place.
 */

import { readdir, mkdir, copyFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export class SkillsMigrator {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  async migrate(): Promise<number> {
    const instinctsDir = join(this.workspacePath, "instincts");
    if (!existsSync(instinctsDir)) return 0;

    const skillsDir = join(this.workspacePath, "skills");

    let entries: string[];
    try {
      const dirEntries = await readdir(instinctsDir, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return 0;
    }

    let migrated = 0;
    for (const entry of entries) {
      const instinctPath = join(instinctsDir, entry, "INSTINCT.md");
      if (!existsSync(instinctPath)) continue;

      const skillName = entry.replace(/-/g, "_");
      const destDir = join(skillsDir, skillName);
      const destPath = join(destDir, "SKILL.md");

      try {
        await mkdir(destDir, { recursive: true });
        await copyFile(instinctPath, destPath);
        log.engine.info(
          `[Migrator] instincts/${entry}/INSTINCT.md → skills/${skillName}/SKILL.md`,
        );
        migrated++;
      } catch (err) {
        log.engine.warn(
          `[Migrator] Failed to migrate ${entry}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return migrated;
  }
}
