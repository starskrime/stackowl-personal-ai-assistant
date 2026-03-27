/**
 * StackOwl — Workflow Chain Store
 *
 * Persists, loads, and searches workflow definitions.
 */

import { readFile, writeFile, mkdir, readdir } from "node:fs/promises";
import { join } from "node:path";
import { existsSync } from "node:fs";
import type { WorkflowDefinition } from "./types.js";
import { log } from "../logger.js";

export class WorkflowChainStore {
  private workflows = new Map<string, WorkflowDefinition>();
  private dirPath: string;

  constructor(workspacePath: string) {
    this.dirPath = join(workspacePath, "workflows");
  }

  async load(): Promise<void> {
    try {
      if (!existsSync(this.dirPath)) return;
      const files = await readdir(this.dirPath);
      for (const file of files) {
        if (!file.endsWith(".json")) continue;
        try {
          const raw = await readFile(join(this.dirPath, file), "utf-8");
          const wf: WorkflowDefinition = JSON.parse(raw);
          this.workflows.set(wf.id, wf);
        } catch (err) {
          log.engine.warn(`[WorkflowChain] Failed to load ${file}: ${err}`);
        }
      }
      log.engine.info(
        `[WorkflowChain] Loaded ${this.workflows.size} workflows`,
      );
    } catch (err) {
      log.engine.warn(`[WorkflowChain] Failed to load directory: ${err}`);
    }
  }

  async save(workflow: WorkflowDefinition): Promise<void> {
    try {
      if (!existsSync(this.dirPath)) {
        await mkdir(this.dirPath, { recursive: true });
      }
      const filePath = join(this.dirPath, `${workflow.id}.json`);
      await writeFile(filePath, JSON.stringify(workflow, null, 2), "utf-8");
      this.workflows.set(workflow.id, workflow);
    } catch (err) {
      log.engine.warn(`[WorkflowChain] Failed to save ${workflow.id}: ${err}`);
    }
  }

  get(id: string): WorkflowDefinition | undefined {
    return this.workflows.get(id);
  }

  list(): WorkflowDefinition[] {
    return [...this.workflows.values()];
  }

  listByTag(tag: string): WorkflowDefinition[] {
    return this.list().filter((w) => w.tags.includes(tag));
  }

  /**
   * Find workflows matching a trigger phrase.
   */
  matchTrigger(text: string): WorkflowDefinition | undefined {
    const lower = text.toLowerCase().trim();
    for (const wf of this.workflows.values()) {
      for (const trigger of wf.triggers) {
        if (lower.includes(trigger.toLowerCase())) {
          return wf;
        }
      }
    }
    return undefined;
  }

  async remove(id: string): Promise<void> {
    this.workflows.delete(id);
    const filePath = join(this.dirPath, `${id}.json`);
    try {
      const { unlink } = await import("node:fs/promises");
      if (existsSync(filePath)) await unlink(filePath);
    } catch {
      // ignore
    }
  }

  size(): number {
    return this.workflows.size;
  }
}
