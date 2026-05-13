/**
 * StackOwl — Specialized Owl Registry
 *
 * Loads specialized owl specs from workspace/owls/<Name>/specialized_owl.md files.
 * Each specialized owl is self-contained with its spec and credentials.
 */

import { readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SpecializedOwlSpec } from "./specialized-types.js";
import type { OwlDNA } from "./persona.js";
import { parseSpecializedOwl } from "./specialized-parser.js";
import { log } from "../logger.js";

export class SpecializedOwlRegistry {
  private specs: Map<string, SpecializedOwlSpec> = new Map();
  private dnaMap: Map<string, OwlDNA> = new Map();

  async loadAll(workspacePath: string): Promise<void> {
    this.specs.clear();
    this.dnaMap.clear();
    const owlsDir = join(workspacePath, "owls");
    if (!existsSync(owlsDir)) {
      log.engine.info("[SpecializedOwlRegistry] No owls directory found");
      return;
    }

    let entries: string[];
    try {
      const dirEntries = await readdir(owlsDir, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return;
    }

    for (const entry of entries) {
      let specPath = join(owlsDir, entry, "helper.md");
      if (!existsSync(specPath)) {
        specPath = join(owlsDir, entry, "specialized_owl.md");
        if (!existsSync(specPath)) continue;
      }

      try {
        const raw = await readFile(specPath, "utf-8");
        const spec = parseSpecializedOwl(raw);
        spec.folderPath = join(owlsDir, entry);
        spec.credentialsPath = join(owlsDir, entry, "credentials");
        this.specs.set(spec.name.toLowerCase(), spec);

        const dnaPath = join(owlsDir, entry, "owl_dna.json");
        if (existsSync(dnaPath)) {
          try {
            const dnaRaw = await readFile(dnaPath, "utf-8");
            this.dnaMap.set(spec.name.toLowerCase(), JSON.parse(dnaRaw) as OwlDNA);
          } catch {
            // malformed dna file — skip silently
          }
        }

        log.engine.info(`[SpecializedOwlRegistry] Loaded ${spec.name}`);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.engine.warn(`[SpecializedOwlRegistry] Failed to load ${entry}: ${msg}`);
      }
    }
  }

  get(name: string): SpecializedOwlSpec | undefined {
    const lower = name.toLowerCase();
    // Exact match first
    const exact = this.specs.get(lower);
    if (exact) return exact;
    // Prefix match — allows @calc to resolve to "calculus"
    for (const [key, spec] of this.specs) {
      if (key.startsWith(lower)) return spec;
    }
    return undefined;
  }

  listAll(): SpecializedOwlSpec[] {
    return Array.from(this.specs.values());
  }

  getByExpertise(domain: string): SpecializedOwlSpec[] {
    const lower = domain.toLowerCase();
    return this.listAll().filter((spec) =>
      spec.expertise.some((e) => e.toLowerCase().includes(lower)),
    );
  }

  getByKeyword(keyword: string): SpecializedOwlSpec[] {
    const lower = keyword.toLowerCase();
    return this.listAll().filter((spec) =>
      spec.routingRules.keywords.some((k) => k.toLowerCase().includes(lower)),
    );
  }

  getDefault(): SpecializedOwlSpec | undefined {
    const coordinator = this.listAll().find((s) => s.type === "coordinator");
    return coordinator ?? this.listAll()[0];
  }

  listSpecialists(): SpecializedOwlSpec[] {
    return this.listAll().filter((s) => s.type === "specialist");
  }

  getDNA(owlName: string): OwlDNA | undefined {
    return this.dnaMap.get(owlName.toLowerCase());
  }

  async saveDNA(owlName: string, dna: OwlDNA): Promise<void> {
    const spec = this.get(owlName);
    if (!spec?.folderPath) return;
    const dnaPath = join(spec.folderPath, "owl_dna.json");
    await writeFile(dnaPath, JSON.stringify(dna, null, 2), "utf-8");
    this.dnaMap.set(owlName.toLowerCase(), dna);
  }

  registerSpec(spec: SpecializedOwlSpec): void {
    log.engine.debug("[SpecializedOwlRegistry] registerSpec", { name: spec.name, source: (spec as any).source });
    this.specs.set(spec.name.toLowerCase(), spec);
  }
}

// ─── Helper rebrand aliases (Element 17) ─────────────────────────
/** Alias for SpecializedOwlRegistry — use HelperRegistry in new code */
export type HelperRegistry = SpecializedOwlRegistry
