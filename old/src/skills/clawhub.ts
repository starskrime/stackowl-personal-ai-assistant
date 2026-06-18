/**
 * StackOwl — ClawHub Client
 *
 * Client for interacting with ClawHub (https://clawhub.ai)
 * to search, discover, and install OpenCLAW-compatible skills.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import chalk from "chalk";

export interface ClawHubSkill {
  slug: string;
  name: string;
  description: string;
  stars: number;
  downloads: number;
  tags: string[];
  author: string;
  latestVersion: string;
  updatedAt: string;
}

export interface ClawHubSearchResult {
  skills: ClawHubSkill[];
  total: number;
}

export interface ClawHubConfig {
  siteUrl: string;
  registryUrl: string;
}

const DEFAULT_CONFIG: ClawHubConfig = {
  siteUrl: "https://clawhub.ai",
  registryUrl: "https://wry-manatee-359.convex.site/api/v1",
};

export class ClawHubClient {
  private config: ClawHubConfig;

  constructor(config: Partial<ClawHubConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Search for skills on ClawHub.
   */
  async search(
    query: string,
    limit: number = 10,
  ): Promise<ClawHubSearchResult> {
    const url = `${this.config.registryUrl}/search?q=${encodeURIComponent(query)}&limit=${limit}`;

    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(
          `Search failed: ${response.status} ${response.statusText}`,
        );
      }

      const data = (await response.json()) as {
        results: Array<{
          slug: string;
          displayName: string;
          summary: string;
          score: number;
          updatedAt: number;
        }>;
      };
      const skills: ClawHubSkill[] = (data.results || []).slice(0, limit).map((r) => ({
        slug: r.slug,
        name: r.displayName,
        description: r.summary,
        stars: 0,
        downloads: 0,
        tags: [],
        author: "",
        latestVersion: "",
        updatedAt: new Date(r.updatedAt).toISOString(),
      }));
      return { skills, total: skills.length };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      throw new Error(`Failed to search ClawHub (${url}): ${msg}`);
    }
  }

  /**
   * Get detailed info about a specific skill.
   */
  async getSkill(slug: string): Promise<ClawHubSkill | null> {
    const url = `${this.config.registryUrl}/skills/${encodeURIComponent(slug)}`;

    try {
      const response = await fetch(url);
      if (response.status === 404) {
        return null;
      }
      if (!response.ok) {
        throw new Error(`Get skill failed: ${response.status}`);
      }

      return (await response.json()) as ClawHubSkill;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      throw new Error(`Failed to get skill: ${msg}`);
    }
  }

  /**
   * Download and install a skill from ClawHub.
   * slug may be "user/repo" or just "repo" — only the repo name is sent to the API.
   */
  async install(
    slug: string,
    targetDir: string,
    _version?: string,
  ): Promise<boolean> {
    // ClawHub API only uses the repo name, not the full "user/repo" slug
    const skillName = slug.includes("/") ? slug.split("/").pop()! : slug;
    const downloadUrl = `${this.config.registryUrl}/download?slug=${encodeURIComponent(skillName)}`;

    console.log(chalk.dim(`Downloading ${skillName} from ClawHub...`));

    const response = await fetch(downloadUrl);
    if (!response.ok) {
      throw new Error(
        `ClawHub download failed: ${response.status} ${response.statusText} — ${downloadUrl}`,
      );
    }

    const arrayBuffer = await response.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);

    const skillDir = join(targetDir, skillName);
    await mkdir(skillDir, { recursive: true });

    const zipPath = join(skillDir, "skill.zip");
    await writeFile(zipPath, buffer);
    await this.extractZip(zipPath, skillDir);

    const { unlink } = await import("node:fs/promises");
    await unlink(zipPath).catch(() => {});

    console.log(chalk.green(`✓ Installed ${skillName} to ${skillDir}`));
    return true;
  }

  /**
   * Extract zip file using system unzip command.
   */
  private async extractZip(zipPath: string, targetDir: string): Promise<void> {
    const { execSync } = await import("child_process");
    try {
      execSync(`unzip -o "${zipPath}" -d "${targetDir}"`, { stdio: "pipe" });
    } catch (err: any) {
      const msg = err.stderr?.toString() ?? err.message;
      throw new Error(`Zip extraction failed: ${msg}`);
    }
  }

  /**
   * List installed skills from lockfile.
   */
  async listInstalled(workdir: string): Promise<string[]> {
    const lockPath = join(workdir, ".clawhub", "lock.json");

    if (!existsSync(lockPath)) {
      return [];
    }

    try {
      const content = await readFile(lockPath, "utf-8");
      const lock = JSON.parse(content);
      return Object.keys(lock.skills || {});
    } catch {
      return [];
    }
  }
}
