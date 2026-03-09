/**
 * StackOwl — Skills Loader
 *
 * Loads skills from multiple directories with optional file watching.
 */

import { existsSync } from "node:fs";
import { watch, type FSWatcher } from "chokidar";
import { resolve } from "node:path";
import chalk from "chalk";
import { SkillsRegistry } from "./registry.js";
import type { SkillLoadOptions, SkillFilter, Skill } from "./types.js";

export class SkillsLoader {
  private registry: SkillsRegistry;
  private watchers: FSWatcher[] = [];
  private directories: string[] = [];
  private watchEnabled: boolean = false;
  private debounceMs: number = 250;
  private debounceTimer: NodeJS.Timeout | null = null;

  constructor() {
    this.registry = new SkillsRegistry();
  }

  /**
   * Get the underlying registry.
   */
  getRegistry(): SkillsRegistry {
    return this.registry;
  }

  /**
   * Load skills from configured directories.
   */
  async load(options: SkillLoadOptions): Promise<number> {
    this.directories = options.directories;
    this.watchEnabled = options.watch ?? false;
    this.debounceMs = options.watchDebounceMs ?? 250;

    let totalLoaded = 0;

    // Load from each directory (in order of precedence)
    for (const dir of this.directories) {
      const resolvedDir = resolve(dir);
      if (!existsSync(resolvedDir)) {
        console.log(
          chalk.dim(`[SkillsLoader] Directory not found: ${resolvedDir}`),
        );
        continue;
      }

      const loaded = await this.registry.loadFromDirectory(resolvedDir);
      console.log(
        chalk.dim(`[SkillsLoader] Loaded ${loaded} skills from ${resolvedDir}`),
      );
      totalLoaded += loaded;
    }

    // Set up file watching if enabled
    if (this.watchEnabled && totalLoaded > 0) {
      this.setupWatcher();
    }

    // Log ineligible skills
    const ineligible = this.registry.getIneligible();
    if (ineligible.length > 0) {
      console.log(
        chalk.yellow(
          `[SkillsLoader] ${ineligible.length} skills have unmet requirements:`,
        ),
      );
      for (const { skill, missing } of ineligible.slice(0, 5)) {
        console.log(chalk.yellow(`  - ${skill.name}: ${missing.join(", ")}`));
      }
      if (ineligible.length > 5) {
        console.log(chalk.yellow(`  ... and ${ineligible.length - 5} more`));
      }
    }

    return totalLoaded;
  }

  /**
   * Set up file watcher for hot-reload.
   */
  private setupWatcher(): void {
    const watchPaths = this.directories
      .map((d) => resolve(d))
      .filter(existsSync);

    if (watchPaths.length === 0) return;

    console.log(chalk.dim(`[SkillsLoader] Setting up file watcher...`));

    const watcher = watch(watchPaths, {
      ignored: /(^|[\/\\])\../,
      persistent: true,
      depth: 1,
      ignoreInitial: true,
    });

    this.debounceTimer = null;

    watcher.on("add", (path) => {
      if (path.endsWith("/SKILL.md")) {
        this.handleFileChange(path, "add");
      }
    });

    watcher.on("change", (path) => {
      if (path.endsWith("/SKILL.md")) {
        this.handleFileChange(path, "change");
      }
    });

    watcher.on("unlink", (path) => {
      if (path.endsWith("/SKILL.md")) {
        this.handleFileChange(path, "unlink");
      }
    });

    this.watchers.push(watcher);
  }

  /**
   * Handle file change with debounce.
   */
  private handleFileChange(
    path: string,
    event: "add" | "change" | "unlink",
  ): void {
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
    }

    this.debounceTimer = setTimeout(async () => {
      console.log(chalk.dim(`[SkillsLoader] File ${event}: ${path}`));

      // Reload all skills (simple approach)
      // For production, we'd want to do incremental updates
      const loaded = await this.registry.loadFromDirectory(
        path.replace(/\/[^/]+\/SKILL.md$/, ""),
      );
      if (loaded > 0) {
        console.log(chalk.green(`[SkillsLoader] Reloaded skill from ${path}`));
      }
    }, this.debounceMs);
  }

  /**
   * Stop all watchers.
   */
  async stop(): Promise<void> {
    for (const watcher of this.watchers) {
      await watcher.close();
    }
    this.watchers = [];
  }

  /**
   * Get eligible skills based on current environment.
   */
  getEligibleSkills(filter?: SkillFilter): Skill[] {
    return this.registry.getEligible(
      filter || {
        os: process.platform as NodeJS.Platform,
        bins: [], // Would need to check PATH
        env: process.env as Record<string, string>,
        config: {},
      },
    );
  }

  /**
   * Search skills by name or description.
   */
  search(query: string): import("./types.js").Skill[] {
    const q = query.toLowerCase();
    return this.registry
      .listAll()
      .filter(
        (skill) =>
          skill.name.toLowerCase().includes(q) ||
          skill.description.toLowerCase().includes(q),
      );
  }
}
