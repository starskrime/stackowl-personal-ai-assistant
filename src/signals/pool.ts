import type {
  ContextSignal,
  ConsentMap,
  MeshState,
  SignalCollector,
  SignalSource,
} from "../ambient/types.js";
import { DEFAULT_CONSENT } from "../ambient/types.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { GoalGraph } from "../goals/graph.js";
import type { GoalVerifier } from "../tools/goal-verifier.js";
import type { MemoryRepository } from "../memory/repository.js";
import type { SignalClassifier } from "./classifier.js";
import { signalToVerifyArgs } from "./goal-adapter.js";
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

  async injectSignal(signal: ContextSignal): Promise<void> {
    // Gate 1: consent (no router cost for denied sources)
    const consent = this.deps.config.consent;
    const allowed = consent[signal.source] ?? DEFAULT_CONSENT[signal.source];
    if (!allowed) return;

    // Gate 2: enabledSources (no router cost for disabled sources)
    const enabled = this.deps.config.enabledSources;
    if (enabled && !enabled.includes(signal.source)) return;

    // Stage 1: cheap-tier classifier prefilter
    const { keep, confidence } = await this.deps.classifier.classify(signal);
    if (!keep) return;

    let priority = signal.priority;
    if (confidence >= 0.9) priority = "high";
    else if (confidence >= 0.7) priority = "medium";
    else priority = "low";

    const admitted: ContextSignal = {
      ...signal,
      priority,
      userSurfaceable: false,
    };
    this.signals.set(admitted.id, admitted);
    this.enforceLimit();
    this.deps.bus.emit({ type: "signal:emitted", signal: admitted } as any);

    // Stage 2: only verify high-priority signals against active goal
    if (priority !== "high") return;
    const goal = this.deps.goalGraph.getTopPriority();
    if (!goal) return;

    try {
      const verifyArgs = signalToVerifyArgs(admitted, goal);
      const result = await this.deps.verifier.verify(verifyArgs);
      if (result.verdict === "ADVANCES") {
        admitted.userSurfaceable = true;
        this.signals.set(admitted.id, admitted);
        this.deps.bus.emit({
          type: "signal:promoted",
          signal: admitted,
          goal: { id: goal.id, title: goal.title },
          rationale: result.reason,
          verdict: "ADVANCES",
        } as any);
      } else {
        this.deps.bus.emit({
          type: "signal:suppressed",
          signal: admitted,
          verdict: result.verdict,
        } as any);
      }
    } catch (err) {
      log.engine.warn(
        `[SignalPool] verifier failed: ${(err as Error).message}`,
      );
      // Signal stays in pool; will be retried on heartbeat sweep.
    }
  }

  async heartbeatTick(): Promise<void> {
    const now = Date.now();
    for (const [id, s] of this.signals) {
      if (s.timestamp + s.ttlMs < now) {
        this.signals.delete(id);
        this.deps.bus.emit({
          type: "signal:expired",
          signal: s,
          reason: "ttl",
        } as any);
      }
    }
    const goal = this.deps.goalGraph.getTopPriority();
    if (!goal) return;
    const candidates = [...this.signals.values()]
      .filter(
        (s) =>
          !s.userSurfaceable &&
          (s.priority === "medium" || s.priority === "high"),
      )
      .slice(0, 5);
    for (const s of candidates) {
      try {
        const result = await this.deps.verifier.verify(
          signalToVerifyArgs(s, goal),
        );
        if (result.verdict === "ADVANCES") {
          s.userSurfaceable = true;
          this.signals.set(s.id, s);
          this.deps.bus.emit({
            type: "signal:promoted",
            signal: s,
            goal: { id: goal.id, title: goal.title },
            rationale: result.reason,
            verdict: "ADVANCES",
          } as any);
        }
      } catch (err) {
        log.engine.warn(
          `[SignalPool] heartbeat verify failed: ${(err as Error).message}`,
        );
      }
    }
  }

  private enforceLimit(): void {
    const max = this.deps.config.maxSignals;
    if (this.signals.size <= max) return;
    const sorted = [...this.signals.values()].sort(
      (a, b) =>
        (PRIORITY_ORDER[b.priority] ?? 3) -
          (PRIORITY_ORDER[a.priority] ?? 3) || a.timestamp - b.timestamp,
    );
    while (sorted.length > max) {
      const evicted = sorted.shift()!;
      this.signals.delete(evicted.id);
      this.deps.bus.emit({
        type: "signal:expired",
        signal: evicted,
        reason: "evicted",
      } as any);
    }
  }

  private async runPollCollector(_c: SignalCollector): Promise<void> {
    // Implemented in Task 9
  }
}
