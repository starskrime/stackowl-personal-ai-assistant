/**
 * StackOwl — Owl Registry
 *
 * Loads owl personas from OWL.md files and manages DNA state.
 * Handles both built-in and custom owls.
 */

import { readdir, readFile, mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import matter from "gray-matter";
import type {
  OwlPersona,
  OwlDNA,
  OwlInstance,
  ChallengeLevel,
} from "./persona.js";
import { createDefaultDNA } from "./persona.js";

export class OwlRegistry {
  private owls: Map<string, OwlInstance> = new Map();
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  /**
   * Load all owls from built-in defaults and workspace custom owls.
   */
  async loadAll(): Promise<void> {
    // Load built-in owls from src/owls/defaults/
    const defaultsDir = new URL("./defaults/", import.meta.url).pathname;
    if (existsSync(defaultsDir)) {
      await this.loadFromDirectory(defaultsDir);
    }

    // Load custom owls from workspace/owls/
    const customDir = join(this.workspacePath, "owls");
    if (existsSync(customDir)) {
      await this.loadFromDirectory(customDir);
    }
  }

  /**
   * Load owls from a directory. Each subdirectory should contain an OWL.md file.
   */
  private async loadFromDirectory(dirPath: string): Promise<void> {
    let entries: string[];
    try {
      const dirEntries = await readdir(dirPath, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return; // Directory doesn't exist or isn't readable
    }

    for (const entry of entries) {
      const owlMdPath = join(dirPath, entry, "OWL.md");
      if (!existsSync(owlMdPath)) continue;

      try {
        const persona = await this.parseOwlMd(owlMdPath);
        const dna = await this.loadOrCreateDNA(persona);
        this.owls.set(persona.name.toLowerCase(), { persona, dna });
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(
          `[OwlRegistry] Failed to load owl from ${owlMdPath}: ${msg}`,
        );
      }
    }
  }

  /**
   * Parse an OWL.md file into an OwlPersona.
   */
  private async parseOwlMd(filePath: string): Promise<OwlPersona> {
    const raw = await readFile(filePath, "utf-8");
    const { data, content } = matter(raw);

    if (!data.name || typeof data.name !== "string") {
      throw new Error(`OWL.md missing required "name" field: ${filePath}`);
    }
    if (!data.type || typeof data.type !== "string") {
      throw new Error(`OWL.md missing required "type" field: ${filePath}`);
    }

    return {
      name: data.name as string,
      type: data.type as string,
      emoji: (data.emoji as string) ?? "🦉",
      challengeLevel: (data.challenge_level as ChallengeLevel) ?? "medium",
      specialties: Array.isArray(data.specialties) ? data.specialties : [],
      traits: Array.isArray(data.traits) ? data.traits : [],
      systemPrompt: content.trim(),
      sourcePath: filePath,
    };
  }

  /**
   * Load existing DNA from workspace or create default DNA.
   */
  private async loadOrCreateDNA(persona: OwlPersona): Promise<OwlDNA> {
    const dnaDir = join(this.workspacePath, "owls", persona.name.toLowerCase());
    const dnaPath = join(dnaDir, "owl_dna.json");

    if (existsSync(dnaPath)) {
      try {
        const raw = await readFile(dnaPath, "utf-8");
        return JSON.parse(raw) as OwlDNA;
      } catch {
        console.warn(
          `[OwlRegistry] Corrupt DNA for ${persona.name}, creating fresh.`,
        );
      }
    }

    return createDefaultDNA(persona.name.toLowerCase(), persona.challengeLevel);
  }

  /**
   * Save an owl's DNA to the workspace.
   */
  async saveDNA(owlName: string): Promise<void> {
    const instance = this.owls.get(owlName.toLowerCase());
    if (!instance) {
      throw new Error(`[OwlRegistry] Owl "${owlName}" not found.`);
    }

    const dnaDir = join(this.workspacePath, "owls", owlName.toLowerCase());
    await mkdir(dnaDir, { recursive: true });

    const dnaPath = join(dnaDir, "owl_dna.json");
    await writeFile(dnaPath, JSON.stringify(instance.dna, null, 2), "utf-8");
  }

  /**
   * Get an owl instance by name.
   */
  get(name: string): OwlInstance | undefined {
    return this.owls.get(name.toLowerCase());
  }

  /**
   * Get the default owl (first registered, or 'archimedes').
   */
  getDefault(): OwlInstance {
    // Noctua (Executive Assistant) is always the default — she's the user's primary contact
    const noctua = this.owls.get("noctua");
    if (noctua) return noctua;

    // Fallback to archimedes if noctua somehow missing
    const archimedes = this.owls.get("archimedes");
    if (archimedes) return archimedes;

    const first = this.owls.values().next();
    if (first.done) {
      throw new Error("[OwlRegistry] No owls loaded.");
    }
    return first.value;
  }

  /**
   * List all loaded owls.
   */
  listOwls(): OwlInstance[] {
    return Array.from(this.owls.values());
  }

  /**
   * Get owls by specialty match.
   */
  getBySpecialty(keyword: string): OwlInstance[] {
    const lower = keyword.toLowerCase();
    return this.listOwls().filter((owl) =>
      owl.persona.specialties.some((s) => s.toLowerCase().includes(lower)),
    );
  }

  /**
   * Reset an owl's DNA to defaults.
   */
  async resetDNA(owlName: string): Promise<void> {
    const instance = this.owls.get(owlName.toLowerCase());
    if (!instance) {
      throw new Error(`[OwlRegistry] Owl "${owlName}" not found.`);
    }

    instance.dna = createDefaultDNA(
      instance.persona.name.toLowerCase(),
      instance.persona.challengeLevel,
    );
    await this.saveDNA(owlName);
  }
}
