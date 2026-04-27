/**
 * StackOwl — Specialized Owl Registry
 *
 * Loads specialized owl specs from workspace/owls/<Name>/specialized_owl.md files.
 * Each specialized owl is self-contained with its spec and credentials.
 */

import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SpecializedOwlSpec } from "./specialized-types.js";
import { parseSpecializedOwl } from "./specialized-parser.js";
import { log } from "../logger.js";

export class SpecializedOwlRegistry {
  private specs: Map<string, SpecializedOwlSpec> = new Map();

  async loadAll(workspacePath: string): Promise<void> {
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
      const specPath = join(owlsDir, entry, "specialized_owl.md");
      if (!existsSync(specPath)) continue;

      try {
        const raw = await readFile(specPath, "utf-8");
        const spec = parseSpecializedOwl(raw);
        spec.credentialsPath = join(owlsDir, entry, "credentials");
        this.specs.set(spec.name.toLowerCase(), spec);
        log.engine.info(`[SpecializedOwlRegistry] Loaded ${spec.name}`);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.engine.warn(`[SpecializedOwlRegistry] Failed to load ${entry}: ${msg}`);
      }
    }
  }

  get(name: string): SpecializedOwlSpec | undefined {
    return this.specs.get(name.toLowerCase());
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
}
