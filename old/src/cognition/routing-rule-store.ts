import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export interface RoutingRule {
  id: string;                      // uuid or hash
  failingTool: string;             // e.g. "web_search"
  intentPattern: string;           // keyword pattern, e.g. "price amazon"
  suggestedAlternatives: string[]; // e.g. ["web_fetch", "live_browser"]
  appliedAt: number;               // Date.now()
  version: number;                 // increments on rollback
  disabled: boolean;               // true when rolled back
  observationCount: number;        // invocations monitored since creation
  successCount: number;            // satisfied verdicts since creation
}

export class RoutingRuleStore {
  private rules: Map<string, RoutingRule> = new Map();
  private filePath: string;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "routing-rules.json");
    this.load();
  }

  private load(): void {
    if (!existsSync(this.filePath)) return;
    try {
      const data = JSON.parse(readFileSync(this.filePath, "utf8")) as RoutingRule[];
      for (const rule of data) this.rules.set(rule.id, rule);
      log.engine.debug("routing-rule-store.loaded", { count: this.rules.size });
    } catch (err) {
      log.engine.warn("routing-rule-store.load-failed", err);
    }
  }

  private save(): void {
    try {
      const dir = join(this.filePath, "..");
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(this.filePath, JSON.stringify([...this.rules.values()], null, 2));
    } catch (err) {
      log.engine.warn("routing-rule-store.save-failed", err);
    }
  }

  upsert(rule: RoutingRule): void {
    this.rules.set(rule.id, rule);
    this.save();
    log.engine.info("routing-rule.upserted", {
      id: rule.id,
      failingTool: rule.failingTool,
      disabled: rule.disabled,
      version: rule.version,
    });
  }

  getActive(): RoutingRule[] {
    return [...this.rules.values()].filter((r) => !r.disabled);
  }

  getById(id: string): RoutingRule | undefined {
    return this.rules.get(id);
  }

  /** Build a hint string for active rules matching the user intent. */
  buildHint(userIntent: string): string {
    const intent = userIntent.toLowerCase();
    const matching = this.getActive().filter((r) =>
      r.intentPattern.split(" ").some((kw) => intent.includes(kw.toLowerCase()))
    );
    if (matching.length === 0) return "";

    const lines = matching.map(
      (r) =>
        `- Avoid \`${r.failingTool}\` (repeated failures). Try: ${r.suggestedAlternatives.map((a) => `\`${a}\``).join(", ")}.`
    );
    return `⚡ Learned routing hints (from past failures):\n${lines.join("\n")}`;
  }

  _getAllForTest(): RoutingRule[] {
    return [...this.rules.values()];
  }
}
