import { Logger } from "../logger.js";
import type {
  AmbientRule,
  ContextSignal,
  MeshState,
  SignalCollector,
  SignalSource,
} from "./types.js";

const log = new Logger("AMBIENT");

const PRIORITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export class ContextMesh {
  private signals = new Map<string, ContextSignal>();
  private collectors: SignalCollector[] = [];
  private rules: AmbientRule[] = [];
  private timers: ReturnType<typeof setInterval>[] = [];
  private maxSignals: number;
  private enabledSources: SignalSource[] | null;
  private running = false;

  constructor(
    public readonly workspacePath: string,
    config?: { maxSignals?: number; enabledSources?: SignalSource[] },
  ) {
    this.maxSignals = config?.maxSignals ?? 50;
    this.enabledSources = config?.enabledSources ?? null;
  }

  addCollector(collector: SignalCollector): void {
    if (
      this.enabledSources &&
      !this.enabledSources.includes(collector.source)
    ) {
      log.debug(`Skipping collector for disabled source: ${collector.source}`);
      return;
    }
    this.collectors.push(collector);
  }

  addRule(rule: AmbientRule): void {
    this.rules.push(rule);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    log.info(
      `Context mesh starting with ${this.collectors.length} collector(s)`,
    );

    for (const collector of this.collectors) {
      // Run immediately, then on interval
      this.runCollector(collector);
      const timer = setInterval(
        () => this.runCollector(collector),
        collector.intervalMs,
      );
      this.timers.push(timer);
    }
  }

  stop(): void {
    if (!this.running) return;
    this.running = false;
    for (const timer of this.timers) {
      clearInterval(timer);
    }
    this.timers = [];
    log.info("Context mesh stopped");
  }

  getState(): MeshState {
    this.pruneExpired();
    const signals = Array.from(this.signals.values()).sort(
      (a, b) =>
        (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3),
    );

    return {
      signals,
      lastUpdate: Date.now(),
      activeContext: this.toContextBlock(),
    };
  }

  toContextBlock(maxSignals = 10): string {
    this.pruneExpired();
    const signals = Array.from(this.signals.values())
      .sort(
        (a, b) =>
          (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3),
      )
      .slice(0, maxSignals);

    if (signals.length === 0) return "";

    const now = new Date().toISOString();
    const lines = signals.map(
      (s) =>
        `  <signal source="${s.source}" priority="${s.priority}">${s.title}</signal>`,
    );

    return `<ambient_context updated="${now}">\n${lines.join("\n")}\n</ambient_context>`;
  }

  evaluateRules(): Array<{
    rule: AmbientRule;
    matchedSignals: ContextSignal[];
  }> {
    this.pruneExpired();
    const now = Date.now();
    const currentSignals = Array.from(this.signals.values());
    const triggered: Array<{
      rule: AmbientRule;
      matchedSignals: ContextSignal[];
    }> = [];

    for (const rule of this.rules) {
      if (rule.lastFired && now - rule.lastFired < rule.cooldownMs) {
        continue;
      }

      try {
        if (rule.condition(currentSignals)) {
          rule.lastFired = now;
          const matchedSignals = currentSignals.filter((s) =>
            rule.condition([s]),
          );
          triggered.push({ rule, matchedSignals });
        }
      } catch (err) {
        log.warn(
          `Rule "${rule.name}" evaluation failed: ${(err as Error).message}`,
        );
      }
    }

    return triggered;
  }

  injectSignal(signal: ContextSignal): void {
    this.signals.set(signal.id, signal);
    this.enforceLimit();
  }

  private async runCollector(collector: SignalCollector): Promise<void> {
    try {
      const signals = await collector.collect();
      // Remove old signals from this source
      for (const [id, existing] of this.signals) {
        if (existing.source === collector.source) {
          this.signals.delete(id);
        }
      }
      for (const signal of signals) {
        this.signals.set(signal.id, signal);
      }
      this.enforceLimit();
    } catch (err) {
      log.warn(
        `Collector ${collector.source} failed: ${(err as Error).message}`,
      );
    }
  }

  private pruneExpired(): void {
    const now = Date.now();
    for (const [id, signal] of this.signals) {
      if (signal.timestamp + signal.ttlMs < now) {
        this.signals.delete(id);
      }
    }
  }

  private enforceLimit(): void {
    if (this.signals.size <= this.maxSignals) return;

    const sorted = Array.from(this.signals.entries()).sort(
      (a, b) =>
        (PRIORITY_ORDER[b[1].priority] ?? 3) -
          (PRIORITY_ORDER[a[1].priority] ?? 3) ||
        a[1].timestamp - b[1].timestamp,
    );

    while (sorted.length > this.maxSignals) {
      const [id] = sorted.shift()!;
      this.signals.delete(id);
    }
  }
}
