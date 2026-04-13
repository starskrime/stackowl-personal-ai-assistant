import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Logger } from "../logger.js";
import type {
  ActionCategory,
  TrustDecision,
  TrustLevel,
  TrustScore,
  TrustThresholds,
} from "./types.js";

const log = new Logger("TRUST");

const DANGEROUS_COMMANDS = [
  "rm ",
  "rm\t",
  "rmdir",
  "kill ",
  "sudo ",
  "mkfs",
  "dd ",
  "chmod",
  "chown",
  "> /dev/",
  "shutdown",
  "reboot",
  "format ",
];

const TRUST_LEVELS: TrustLevel[] = [
  "supervised",
  "prompted",
  "trusted",
  "autonomous",
];

const DEFAULT_THRESHOLDS: TrustThresholds = {
  promptedAfter: 5,
  trustedAfter: 15,
  autonomousAfter: 50,
  denialPenalty: 3,
  decayDays: 14,
};

function makeDefaultScore(category: ActionCategory): TrustScore {
  return {
    category,
    level: "supervised",
    approvalCount: 0,
    denialCount: 0,
    totalExecutions: 0,
    successCount: 0,
    failureCount: 0,
    lastApproved: null,
    lastDenied: null,
    confidence: 0,
  };
}

export class TrustChain {
  private scores = new Map<ActionCategory, TrustScore>();
  private thresholds: TrustThresholds;
  private filePath: string;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(workspacePath: string, thresholds?: Partial<TrustThresholds>) {
    this.thresholds = { ...DEFAULT_THRESHOLDS, ...thresholds };
    this.filePath = join(workspacePath, "trust-scores.json");
  }

  async load(): Promise<void> {
    try {
      if (!existsSync(this.filePath)) {
        log.info("No trust scores file found, starting fresh");
        return;
      }

      const raw = readFileSync(this.filePath, "utf-8");
      const data = JSON.parse(raw) as TrustScore[];

      for (const score of data) {
        this.scores.set(score.category, score);
      }

      log.info(`Loaded trust scores for ${this.scores.size} categories`);
    } catch (err) {
      log.warn(`Failed to load trust scores: ${(err as Error).message}`);
    }
  }

  evaluate(category: ActionCategory, _toolName?: string): TrustDecision {
    const score = this.getOrCreate(category);

    const allowed = score.level === "trusted" || score.level === "autonomous";

    let reason: string;
    switch (score.level) {
      case "supervised":
        reason = `Action "${category}" requires explicit approval (${score.approvalCount}/${this.thresholds.promptedAfter} approvals to unlock prompted mode)`;
        break;
      case "prompted":
        reason = `Action "${category}" will prompt for confirmation (${score.approvalCount}/${this.thresholds.trustedAfter} approvals to unlock trusted mode)`;
        break;
      case "trusted":
        reason = `Action "${category}" is trusted based on ${score.approvalCount} prior approvals`;
        break;
      case "autonomous":
        reason = `Action "${category}" runs autonomously after ${score.approvalCount} successful approvals`;
        break;
    }

    return {
      category,
      level: score.level,
      allowed,
      reason,
      confidence: score.confidence,
    };
  }

  recordApproval(category: ActionCategory): void {
    const score = this.getOrCreate(category);
    score.approvalCount++;
    score.lastApproved = new Date().toISOString();
    this.updateLevel(score);
    this.debouncedSave();
  }

  recordDenial(category: ActionCategory): void {
    const score = this.getOrCreate(category);
    score.denialCount++;
    score.lastDenied = new Date().toISOString();
    this.updateLevel(score);
    this.debouncedSave();
  }

  recordOutcome(category: ActionCategory, success: boolean): void {
    const score = this.getOrCreate(category);
    score.totalExecutions++;
    if (success) {
      score.successCount++;
    } else {
      score.failureCount++;
    }
    this.debouncedSave();
  }

  getTrustLevel(category: ActionCategory): TrustLevel {
    return this.getOrCreate(category).level;
  }

  getAllScores(): TrustScore[] {
    return Array.from(this.scores.values());
  }

  applyDecay(): void {
    const now = Date.now();
    const decayMs = this.thresholds.decayDays * 24 * 60 * 60 * 1000;
    let changed = false;

    for (const score of this.scores.values()) {
      if (score.level === "supervised") continue;
      if (!score.lastApproved) continue;

      const lastApprovedMs = new Date(score.lastApproved).getTime();
      if (now - lastApprovedMs > decayMs) {
        const currentIdx = TRUST_LEVELS.indexOf(score.level);
        if (currentIdx > 0) {
          score.level = TRUST_LEVELS[currentIdx - 1];
          this.updateConfidence(score);
          changed = true;
          log.info(`Trust decayed for "${score.category}": now ${score.level}`);
        }
      }
    }

    if (changed) this.debouncedSave();
  }

  formatStatus(): string {
    const scores = this.getAllScores();
    if (scores.length === 0) return "No trust data recorded yet.";

    const lines: string[] = ["Trust Chain Status:", ""];

    for (const score of scores.sort((a, b) =>
      a.category.localeCompare(b.category),
    )) {
      const nextLevel = this.getNextThreshold(score.level);
      const effectiveApprovals =
        score.approvalCount - score.denialCount * this.thresholds.denialPenalty;

      let progressBar: string;
      if (nextLevel === null) {
        progressBar = "[##########] MAX";
      } else {
        const filled = Math.max(
          0,
          Math.min(10, Math.floor((effectiveApprovals / nextLevel) * 10)),
        );
        const empty = 10 - filled;
        progressBar = `[${"#".repeat(filled)}${"-".repeat(empty)}] ${effectiveApprovals}/${nextLevel}`;
      }

      lines.push(
        `  ${score.category.padEnd(20)} ${score.level.padEnd(12)} ${progressBar}`,
      );
    }

    return lines.join("\n");
  }

  static classifyTool(toolName: string): ActionCategory {
    const name = toolName.toLowerCase();

    if (name === "read_file" || name === "readfile" || name === "read")
      return "file_read";
    if (
      name === "write_file" ||
      name === "writefile" ||
      name === "edit_file" ||
      name === "editfile"
    )
      return "file_write";
    if (name === "delete_file" || name === "deletefile") return "file_delete";

    if (name === "git_status" || name === "git_log" || name === "git_diff")
      return "git_read";
    if (
      name === "git_commit" ||
      name === "git_branch" ||
      name === "git_checkout" ||
      name === "git_merge"
    )
      return "git_write";
    if (name === "git_push") return "git_push";

    if (
      name === "web_crawl" ||
      name === "web_fetch" ||
      name === "duckduckgo_search"
    )
      return "web_fetch";
    if (name === "scrapling_fetch") return "web_scrape";

    if (name === "send_file") return "send_file";
    if (name === "send_message") return "send_message";

    if (name === "run_shell_command" || name === "shell" || name === "exec") {
      return "shell_safe"; // Caller should use classifyCommand for dangerous detection
    }

    return "shell_safe";
  }

  static classifyCommand(command: string): ActionCategory {
    const lower = command.toLowerCase().trim();
    for (const pattern of DANGEROUS_COMMANDS) {
      if (lower.includes(pattern)) return "shell_dangerous";
    }
    return "shell_safe";
  }

  private getOrCreate(category: ActionCategory): TrustScore {
    let score = this.scores.get(category);
    if (!score) {
      score = makeDefaultScore(category);
      this.scores.set(category, score);
    }
    return score;
  }

  private updateLevel(score: TrustScore): void {
    const effective =
      score.approvalCount - score.denialCount * this.thresholds.denialPenalty;

    if (effective >= this.thresholds.autonomousAfter) {
      score.level = "autonomous";
    } else if (effective >= this.thresholds.trustedAfter) {
      score.level = "trusted";
    } else if (effective >= this.thresholds.promptedAfter) {
      score.level = "prompted";
    } else {
      score.level = "supervised";
    }

    this.updateConfidence(score);
  }

  private updateConfidence(score: TrustScore): void {
    const effective = Math.max(
      0,
      score.approvalCount - score.denialCount * this.thresholds.denialPenalty,
    );
    const nextThreshold = this.getNextThreshold(score.level);
    if (nextThreshold === null) {
      score.confidence = 1;
    } else {
      score.confidence = Math.min(1, effective / nextThreshold);
    }
  }

  private getNextThreshold(level: TrustLevel): number | null {
    switch (level) {
      case "supervised":
        return this.thresholds.promptedAfter;
      case "prompted":
        return this.thresholds.trustedAfter;
      case "trusted":
        return this.thresholds.autonomousAfter;
      case "autonomous":
        return null;
    }
  }

  async save(): Promise<void> {
    this.persist();
  }

  private debouncedSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.persist(), 5_000);
  }

  private persist(): void {
    try {
      const data = Array.from(this.scores.values());
      writeFileSync(this.filePath, JSON.stringify(data, null, 2), "utf-8");
      log.debug(`Trust scores persisted (${data.length} categories)`);
    } catch (err) {
      log.warn(`Failed to persist trust scores: ${(err as Error).message}`);
    }
  }
}
