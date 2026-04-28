import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import matter from "gray-matter";
import type { InstinctSpec } from "./types.js";
import { log } from "../logger.js";

export class InstinctRegistry {
  private cache: Map<string, InstinctSpec[]> = new Map();

  async loadForOwl(owlsDir: string, owlName: string): Promise<void> {
    const instinctsDir = join(owlsDir, owlName, "instincts");
    if (!existsSync(instinctsDir)) {
      this.cache.set(owlName, []);
      return;
    }

    let files: string[];
    try {
      files = (await readdir(instinctsDir)).filter((f) => f.endsWith(".md"));
    } catch {
      this.cache.set(owlName, []);
      return;
    }

    const instincts: InstinctSpec[] = [];
    for (const file of files) {
      try {
        const raw = await readFile(join(instinctsDir, file), "utf-8");
        const { data } = matter(raw);
        if (data.name && data.description && data.constraint) {
          instincts.push({
            name: String(data.name),
            description: String(data.description),
            constraint: String(data.constraint),
            owlName,
          });
        }
      } catch (err) {
        log.engine.warn(`[InstinctRegistry] Failed to parse ${file}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    this.cache.set(owlName, instincts);
    log.engine.info(`[InstinctRegistry] Loaded ${instincts.length} instincts for ${owlName}`);
  }

  get(owlName: string): InstinctSpec[] {
    return this.cache.get(owlName) ?? [];
  }

  clear(owlName: string): void {
    this.cache.delete(owlName);
  }
}
