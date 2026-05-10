import type { StackOwlConfig } from "../config/loader.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { ToolOutcomeStore } from "../tools/outcome-store.js";
import type { CapabilityScanner, ScanResult } from "./capability-scanner.js";

// ─── Types ────────────────────────────────────────────────────────

export interface IdleEngineConfig {
  /** Minutes of user inactivity before "idle" mode activates. Default: 15 */
  idleThresholdMinutes: number;
  /** How often the idle cycle checks for work. Default: 5 */
  cycleLengthMinutes: number;
  /** Which activity types are enabled */
  enabled: {
    capabilityExploration: boolean;
    anticipatoryResearch: boolean;
    toolOutcomeReview: boolean;
    knowledgeRefresh: boolean;
  };
}

const DEFAULT_CONFIG: IdleEngineConfig = {
  idleThresholdMinutes: 15,
  cycleLengthMinutes: 5,
  enabled: {
    capabilityExploration: true,
    anticipatoryResearch: true,
    toolOutcomeReview: true,
    knowledgeRefresh: true,
  },
};

export interface IdleActivityResult {
  activity: string;
  success: boolean;
  artifacts?: string[];
  durationMs?: number;
}

export interface IdleEngineCallbacks {
  onResult: (result: IdleActivityResult) => void;
  capabilityScanner?: CapabilityScanner;
  learningOrchestrator?: LearningOrchestrator;
  db?: MemoryDatabase;
  toolOutcomeStore?: ToolOutcomeStore;
}

// ─── IdleActivityEngine ───────────────────────────────────────────

export class IdleActivityEngine {
  private config: IdleEngineConfig;
  private callbacks: IdleEngineCallbacks;
  private lastUserActivity: number = Date.now();
  private timer: ReturnType<typeof setInterval> | null = null;
  private recentResults: IdleActivityResult[] = [];
  private running = false;

  constructor(
    private readonly stackConfig: StackOwlConfig,
    callbacks: IdleEngineCallbacks,
    idleConfig?: Partial<IdleEngineConfig>,
  ) {
    this.callbacks = callbacks;
    this.config = {
      ...DEFAULT_CONFIG,
      ...idleConfig,
      enabled: { ...DEFAULT_CONFIG.enabled, ...(idleConfig?.enabled ?? {}) },
    };
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    const intervalMs = this.config.cycleLengthMinutes * 60_000;
    this.timer = setInterval(() => this.tick(), intervalMs);
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  onUserActivity(): void {
    this.lastUserActivity = Date.now();
  }

  isIdle(): boolean {
    const elapsedMs = Date.now() - this.lastUserActivity;
    return elapsedMs >= this.config.idleThresholdMinutes * 60_000;
  }

  getRecentResults(limit = 10): IdleActivityResult[] {
    return this.recentResults.slice(-limit);
  }

  private async tick(): Promise<void> {
    if (!this.isIdle()) return;
    const activity = this.pickNextActivity();
    if (!activity) return;

    const result = await this.runActivity(activity);
    this.recentResults.push(result);
    if (this.recentResults.length > 100) this.recentResults.shift();
    this.callbacks.onResult(result);
  }

  private pickNextActivity(): string | null {
    if (!this.isIdle()) return null;

    const { enabled } = this.config;
    const { capabilityScanner, learningOrchestrator, toolOutcomeStore } =
      this.callbacks;

    if (enabled.capabilityExploration && capabilityScanner) return "capability_exploration";
    if (enabled.anticipatoryResearch && learningOrchestrator) return "anticipatory_research";
    if (enabled.toolOutcomeReview && toolOutcomeStore) return "tool_outcome_review";
    if (enabled.knowledgeRefresh && learningOrchestrator) return "knowledge_refresh";

    return null;
  }

  private async runActivity(activity: string): Promise<IdleActivityResult> {
    const start = Date.now();
    try {
      switch (activity) {
        case "capability_exploration":
          return await this.runCapabilityExploration();
        case "anticipatory_research":
          return await this.runAnticipatoryResearch();
        case "tool_outcome_review":
          return await this.runToolOutcomeReview();
        case "knowledge_refresh":
          return await this.runKnowledgeRefresh();
        default:
          return { activity, success: false, durationMs: Date.now() - start };
      }
    } catch {
      return { activity, success: false, durationMs: Date.now() - start };
    }
  }

  private async runCapabilityExploration(): Promise<IdleActivityResult> {
    if (!this.callbacks.capabilityScanner) {
      return { activity: "capability_exploration", success: false };
    }
    const result: ScanResult = this.callbacks.capabilityScanner.scan();
    return {
      activity: "capability_exploration",
      success: true,
      artifacts: result.gaps.map((g: any) => g.name),
    };
  }

  private async runAnticipatoryResearch(): Promise<IdleActivityResult> {
    if (!this.callbacks.learningOrchestrator) {
      return { activity: "anticipatory_research", success: false };
    }
    const failureDensityTopics = this.callbacks.db
      ? (this.callbacks.db.trajectories.getFailureDensityTopics(7, 2) ?? [])
      : [];
    await this.callbacks.learningOrchestrator.runProactiveSession({
      failureDensityTopics,
      maxTopics: 3,
    });
    return { activity: "anticipatory_research", success: true };
  }

  private async runToolOutcomeReview(): Promise<IdleActivityResult> {
    if (!this.callbacks.toolOutcomeStore) {
      return { activity: "tool_outcome_review", success: false };
    }
    const patterns = this.callbacks.toolOutcomeStore.getTopPatterns();
    const lowSuccessTools = patterns
      .filter((p: any) => (p.successRate ?? 1) < 0.5)
      .map((p: any) => p.requestType ?? p.name ?? String(p));
    return {
      activity: "tool_outcome_review",
      success: true,
      artifacts: lowSuccessTools,
    };
  }

  private async runKnowledgeRefresh(): Promise<IdleActivityResult> {
    if (!this.callbacks.learningOrchestrator) {
      return { activity: "knowledge_refresh", success: false };
    }
    await this.callbacks.learningOrchestrator.runProactiveSession({ maxTopics: 1 });
    return { activity: "knowledge_refresh", success: true };
  }
}
