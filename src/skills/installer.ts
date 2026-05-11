/**
 * StackOwl — Skill Installer
 *
 * Installs skills from GitHub URLs and local paths.
 * ClawHub installs are handled by ClawHubClient (clawhub.ts).
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, basename, resolve } from "node:path";
import { log } from "../logger.js";
import { sanitize, type SanitizationResult } from "./sanitizer.js";

export type InstallSource =
  | { type: "github"; rawUrl: string; skillName: string }
  | { type: "local"; localPath: string; skillName: string }
  | { type: "clawhub"; slug: string; skillName: string };

/**
 * Parse an install argument into a typed source descriptor.
 *
 * Supported formats:
 *   github:user/repo/path/to/skill
 *   github:user/repo/path/to/skill@branch
 *   ./relative/path/to/skill
 *   /absolute/path/to/skill
 *   clawhub:slug
 *   slug   (defaults to clawhub)
 */
export function parseInstallSource(input: string): InstallSource {
  if (input.startsWith("github:")) {
    const rest = input.slice("github:".length);
    const [pathPart, branch = "main"] = rest.split("@");
    const segments = pathPart.split("/");
    const user = segments[0];
    const repo = segments[1];
    const skillPath = segments.slice(2).join("/");
    const skillName = basename(skillPath);
    const rawUrl = `https://raw.githubusercontent.com/${user}/${repo}/${branch}/${skillPath}/SKILL.md`;
    return { type: "github", rawUrl, skillName };
  }

  if (input.startsWith("./") || input.startsWith("/")) {
    const localPath = resolve(input);
    const skillName = basename(localPath);
    return { type: "local", localPath, skillName };
  }

  if (input.startsWith("clawhub:")) {
    const slug = input.slice("clawhub:".length);
    return { type: "clawhub", slug, skillName: slug };
  }

  return { type: "clawhub", slug: input, skillName: input };
}

export class SkillInstaller {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  private logSanitizationResult(
    skillName: string,
    replacements: SanitizationResult["replacements"],
    flagged: SanitizationResult["flagged"],
  ): void {
    if (replacements.length > 0) {
      log.engine.info(
        `[Installer] Sanitized ${replacements.length} vendor reference(s) in "${skillName}"`,
        { replacements: replacements.map((r) => `${r.from} → ${r.to} (line ${r.line})`) },
      );
    }
    if (flagged.length > 0) {
      log.engine.warn(
        `[Installer] "${skillName}" has ${flagged.length} unresolved vendor token(s) — review recommended`,
        { flagged: flagged.map((f) => `line ${f.line}: ${f.token} — ${f.context}`) },
      );
    }
  }

  /**
   * Install a skill from a GitHub raw URL.
   */
  async fromGitHub(rawUrl: string, skillName: string): Promise<void> {
    log.engine.info(`[Installer] Downloading ${rawUrl}...`);

    const response = await fetch(rawUrl);
    if (!response.ok) {
      throw new Error(
        `GitHub fetch failed: ${response.status} ${response.statusText} — ${rawUrl}`,
      );
    }

    const rawContent = await response.text();
    const { content, replacements, flagged } = sanitize(rawContent);
    const destDir = join(this.workspacePath, "skills", skillName);
    const destPath = join(destDir, "SKILL.md");

    await mkdir(destDir, { recursive: true });
    await writeFile(destPath, content, "utf-8");
    this.logSanitizationResult(skillName, replacements, flagged);
    log.engine.info(`[Installer] Installed ${skillName} to ${destPath}`);
  }

  /**
   * Install a skill from a local directory path.
   */
  async fromLocal(sourcePath: string): Promise<void> {
    const resolved = resolve(sourcePath);
    const skillName = basename(resolved);
    const srcFile = join(resolved, "SKILL.md");

    if (!existsSync(srcFile)) {
      throw new Error(`SKILL.md not found at ${srcFile}`);
    }

    const destDir = join(this.workspacePath, "skills", skillName);
    const destFile = join(destDir, "SKILL.md");

    await mkdir(destDir, { recursive: true });
    const rawContent = await readFile(srcFile, "utf-8");
    const { content, replacements, flagged } = sanitize(rawContent);
    await writeFile(destFile, content, "utf-8");
    this.logSanitizationResult(skillName, replacements, flagged);
    log.engine.info(`[Installer] Copied ${skillName} from ${resolved} to ${destDir}`);
  }
}
