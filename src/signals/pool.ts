import type {
  ContextSignal,
  ConsentMap,
  MeshState,
  SignalCollector,
  SignalSource,
} from "../ambient/types.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { GoalGraph } from "../goals/graph.js";
import type { GoalVerifier } from "../tools/goal-verifier.js";
import type { MemoryRepository } from "../memory/repository.js";
import type { SignalClassifier } from "./classifier.js";
import { log } from "../logger.js";

const PRIORITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export interface SignalPoolDeps {
  bus: GatewayEventBus;
  classifier: Pick<SignalClassifier, "classify">;
  verifier: GoalVerifier;
  goalGraph: GoalGraph;
  config: {
    maxSignals: number;
    enabledSources?: SignalSource[];
    consent: ConsentMap;
  };
  memoryRepo?: MemoryRepository;
  workspacePath: string;
}

export class SignalPool {
  private signals = new Map<string, ContextSignal>();
  private collectors: SignalCollector[] = [];
  private timers: ReturnType<typeof setInterval>[] = [];
  private started = false;

  constructor(private readonly deps: SignalPoolDeps) {}

  addCollector(c: SignalCollector): void {
    const enabled = this.deps.config.enabledSources;
    if (enabled && !enabled.includes(c.source)) {
      log.engine.debug(
        `[SignalPool] collector ${c.source} skipped — not in enabledSources`,
      );
      return;
    }
    this.collectors.push(c);
  }

  start(): void {
    if (this.started) return;
    this.started = true;
    log.engine.info(
      `[SignalPool] starting with ${this.collectors.length} collector(s)`,
    );
    for (const c of this.collectors) {
      if (c.mode === "push" && c.start) {
        c.start((signal) => {
          void this.injectSignal(signal);
        });
      } else if (c.mode === "poll" && c.collect && c.intervalMs) {
        void this.runPollCollector(c);
        this.timers.push(
          setInterval(() => void this.runPollCollector(c), c.intervalMs),
        );
      }
    }
  }

  stop(): void {
    if (!this.started) return;
    this.started = false;
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    for (const c of this.collectors) {
      if (c.mode === "push" && c.stop) c.stop();
    }
    log.engine.info("[SignalPool] stopped");
  }

  getState(): MeshState {
    const signals = [...this.signals.values()].sort(
      (a, b) =>
        (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3),
    );
    return {
      signals,
      lastUpdate: Date.now(),
      activeContext: this.toContextBlock(),
    };
  }

  hasHighPrioritySignals(): boolean {
    for (const s of this.signals.values()) {
      if (s.userSurfaceable && s.priority === "high") return true;
    }
    return false;
  }

  toContextBlock(maxSignals = 8): string {
    const surfaceable = [...this.signals.values()]
      .filter((s) => s.userSurfaceable)
      .sort(
        (a, b) =>
          (PRIORITY_ORDER[a.priority] ?? 3) -
          (PRIORITY_ORDER[b.priority] ?? 3),
      )
      .slice(0, maxSignals);
    if (surfaceable.length === 0) return "";
    const lines = surfaceable.map(
      (s) =>
        `  <signal source="${s.source}" priority="${s.priority}">${s.title}</signal>`,
    );
    return `<ambient_context updated="${new Date().toISOString()}">\n${lines.join("\n")}\n</ambient_context>`;
  }

  async injectSignal(_signal: ContextSignal): Promise<void> {
    // Implemented in Task 6 (admission gates) and Task 7 (verifier promotion)
  }

  private async runPollCollector(_c: SignalCollector): Promise<void> {
    // Implemented in Task 9
  }
}
