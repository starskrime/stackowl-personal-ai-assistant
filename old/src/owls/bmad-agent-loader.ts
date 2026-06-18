/**
 * StackOwl — BMAD Agent Loader
 *
 * Dynamically loads BMAD agents from the installed bmad-method npm package.
 * Scans src/bmm-skills/*\/customize.toml, filters for agent entries, and
 * converts them to SpecializedOwlSpec objects.
 *
 * NO hardcoded agent names or fields. When bmad-method upgrades and adds
 * new agents, they appear automatically on next startup.
 */

import { createRequire } from "node:module";
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, basename } from "node:path";
import type { SpecializedOwlSpec } from "./specialized-types.js";
import { log } from "../logger.js";

interface BmadTomlAgent {
  name?: string;
  title?: string;
  icon?: string;
  role?: string;
  identity?: string;
  communication_style?: string;
  principles?: string[];
}

interface BmadToml {
  agent?: BmadTomlAgent;
}

export interface BmadAgentLoaderOptions {
  packageName?: string;
}

export class BmadAgentLoader {
  private packageName: string;

  constructor(options: BmadAgentLoaderOptions = {}) {
    this.packageName = options.packageName ?? "bmad-method";
    log.engine.debug("BmadAgentLoader: init", { packageName: this.packageName });
  }

  async loadAll(): Promise<SpecializedOwlSpec[]> {
    log.engine.debug("BmadAgentLoader.loadAll: entry", { packageName: this.packageName });

    const bmadRoot = this.resolveBmadRoot();
    if (!bmadRoot) {
      log.engine.info("BmadAgentLoader.loadAll: package not found, skipping", { packageName: this.packageName });
      return [];
    }

    const skillsDir = join(bmadRoot, "src", "bmm-skills");
    if (!existsSync(skillsDir)) {
      log.engine.warn("BmadAgentLoader.loadAll: bmm-skills dir missing", { skillsDir });
      return [];
    }

    const tomlPaths = await this.findAgentTomls(skillsDir);
    log.engine.debug("BmadAgentLoader.loadAll: found TOML candidates", { count: tomlPaths.length });

    const specs: SpecializedOwlSpec[] = [];
    for (const tomlPath of tomlPaths) {
      try {
        const raw = await readFile(tomlPath, "utf-8");
        const parsed = this.parseToml(raw);
        if (!this.isAgentToml(parsed)) continue;
        const skillName = basename(join(tomlPath, ".."));
        const spec = this.toSpec(parsed.agent!, skillName);
        specs.push(spec);
        log.engine.info("BmadAgentLoader.loadAll: loaded agent", { name: spec.name, skill: skillName });
      } catch (err) {
        log.engine.warn("BmadAgentLoader.loadAll: failed to parse", { tomlPath, err: String(err) });
      }
    }

    log.engine.debug("BmadAgentLoader.loadAll: exit", { loaded: specs.length });
    return specs;
  }

  private resolveBmadRoot(): string | null {
    try {
      const req = createRequire(import.meta.url);
      const pkgPath = req.resolve(`${this.packageName}/package.json`);
      const root = join(pkgPath, "..");
      log.engine.debug("BmadAgentLoader.resolveBmadRoot: resolved", { root });
      return root;
    } catch {
      return null;
    }
  }

  private async findAgentTomls(skillsDir: string): Promise<string[]> {
    const paths: string[] = [];
    let categoryDirs: string[];
    try {
      const entries = await readdir(skillsDir, { withFileTypes: true });
      categoryDirs = entries.filter((e) => e.isDirectory()).map((e) => join(skillsDir, e.name));
    } catch {
      return [];
    }

    for (const catDir of categoryDirs) {
      let skillDirs: string[];
      try {
        const entries = await readdir(catDir, { withFileTypes: true });
        skillDirs = entries.filter((e) => e.isDirectory()).map((e) => join(catDir, e.name));
      } catch {
        continue;
      }
      for (const skillDir of skillDirs) {
        const tomlPath = join(skillDir, "customize.toml");
        if (existsSync(tomlPath)) paths.push(tomlPath);
      }
    }
    return paths;
  }

  private parseToml(raw: string): BmadToml {
    const req = createRequire(import.meta.url);
    const toml = req("@iarna/toml") as { parse: (s: string) => unknown };
    return toml.parse(raw) as BmadToml;
  }

  private isAgentToml(parsed: BmadToml): boolean {
    return (
      typeof parsed.agent?.name === "string" &&
      parsed.agent.name.length > 0 &&
      typeof parsed.agent?.title === "string" &&
      parsed.agent.title.length > 0
    );
  }

  private toSpec(agent: BmadTomlAgent, skillName: string): SpecializedOwlSpec {
    const name = agent.name!;
    const title = agent.title!;
    const icon = agent.icon ?? "🦉";
    const role = agent.role ?? title;
    const identity = agent.identity ?? "";
    const commStyle = agent.communication_style ?? "professional";
    const principles: string[] = Array.isArray(agent.principles) ? agent.principles : [];

    const additionalPromptParts = [
      identity ? `Identity: ${identity}` : "",
      commStyle ? `Communication style: ${commStyle}` : "",
      principles.length > 0 ? `Principles:\n${principles.map((p) => `- ${p}`).join("\n")}` : "",
    ].filter(Boolean);

    const expertise = this.extractExpertise(title, role);
    const keywords = this.extractKeywords(title, principles);

    return {
      name,
      type: "specialist",
      role,
      emoji: icon,
      personality: {
        challengeLevel: "medium",
        verbosity: "balanced",
        tone: commStyle.slice(0, 50),
      },
      expertise,
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords },
      skills: { allowed: [] },
      additionalPrompt: additionalPromptParts.join("\n\n"),
      source: "bmad",
      bmadSkillName: skillName,
    };
  }

  private extractExpertise(title: string, role: string): string[] {
    const words = `${title} ${role}`.split(/\s+/).filter((w) => w.length > 3);
    return [...new Set(words.map((w) => w.toLowerCase()))].slice(0, 8);
  }

  private extractKeywords(title: string, principles: string[]): string[] {
    const titleWords = title.split(/\s+/).filter((w) => w.length > 2).map((w) => w.toLowerCase());
    const principleWords = principles
      .flatMap((p) => p.split(/\s+/))
      .filter((w) => w.length > 4)
      .map((w) => w.toLowerCase().replace(/[^a-z]/g, ""))
      .filter(Boolean);
    return [...new Set([...titleWords, ...principleWords])].slice(0, 15);
  }
}
