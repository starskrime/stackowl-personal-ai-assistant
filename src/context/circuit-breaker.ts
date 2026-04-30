import type { ContextBuildTrace } from "./layer.js";

type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

const WINDOW = 20;
const ERROR_RATE_THRESHOLD = 0.4;
const LATENCY_P95_THRESHOLD = 1_800;
const COOLDOWN_MS = 60_000;

export class LayerCircuitBreaker {
  private window: Array<{ success: boolean; latencyMs: number }> = [];
  private _state: CircuitState = "CLOSED";
  private openedAt: number | null = null;

  get state(): CircuitState {
    if (this._state === "OPEN" && this.openedAt !== null) {
      if (Date.now() - this.openedAt >= COOLDOWN_MS) {
        this._state = "HALF_OPEN";
      }
    }
    return this._state;
  }

  shouldBypass(): boolean {
    return this.state === "OPEN";
  }

  recordSuccess(latencyMs: number): void {
    this.window.push({ success: true, latencyMs });
    if (this.window.length > WINDOW) this.window.shift();
    // Trigger state getter to handle OPEN→HALF_OPEN transition before checking
    const currentState = this.state;
    if (currentState === "HALF_OPEN") {
      this._state = "CLOSED";
      this.openedAt = null;
      this.window = [{ success: true, latencyMs }];
      return;
    }
    this.evaluate();
  }

  recordFailure(): void {
    this.window.push({ success: false, latencyMs: 9999 });
    if (this.window.length > WINDOW) this.window.shift();
    if (this._state === "HALF_OPEN") {
      this._state = "OPEN";
      this.openedAt = Date.now();
    } else {
      this.evaluate();
    }
  }

  private evaluate(): void {
    if (this.window.length < 5) return;
    const errorRate =
      this.window.filter((e) => !e.success).length / this.window.length;
    const latencies = this.window
      .map((e) => e.latencyMs)
      .sort((a, b) => a - b);
    const p95 = latencies[Math.floor(latencies.length * 0.95)] ?? 0;
    if (errorRate > ERROR_RATE_THRESHOLD || p95 > LATENCY_P95_THRESHOLD) {
      this._state = "OPEN";
      this.openedAt = Date.now();
    }
  }
}

export class LayerHealthMonitor {
  private breakers = new Map<string, LayerCircuitBreaker>();

  getBreaker(layerName: string): LayerCircuitBreaker {
    let cb = this.breakers.get(layerName);
    if (!cb) {
      cb = new LayerCircuitBreaker();
      this.breakers.set(layerName, cb);
    }
    return cb;
  }

  shouldBypass(layerName: string): boolean {
    return this.getBreaker(layerName).shouldBypass();
  }

  getReport(): Record<string, { state: string; errorRate: number }> {
    const out: Record<string, { state: string; errorRate: number }> = {};
    for (const [name, cb] of this.breakers) {
      out[name] = { state: cb.state, errorRate: 0 };
    }
    return out;
  }
}

// Minimal event emitter interface to avoid importing full EventBus before Task 8
interface MinimalEventEmitter {
  emit(event: string, payload: unknown): void;
}

export class ContextQualityScore {
  constructor(private eventBus?: MinimalEventEmitter) {}

  compute(trace: ContextBuildTrace, totalLayers: number): number {
    if (totalLayers === 0) return 1;
    const fired = trace.filter((e) => e.fired);
    const signalRatio = fired.length / totalLayers;
    const totalTokens = trace.reduce((s, e) => s + e.tokensUsed, 0);
    // Normalize token efficiency relative to layers that fired
    const expectedTokensPerLayer = 100;
    const expectedTotal = fired.length * expectedTokensPerLayer;
    const tokenEfficiency =
      expectedTotal > 0 ? Math.min(1, totalTokens / expectedTotal) : 0.5;
    const score = signalRatio * 0.4 + tokenEfficiency * 0.3 + 0.3;
    const clamped = Math.min(1, Math.max(0, score));
    if (clamped < 0.6 && this.eventBus) {
      (this.eventBus as { emit(e: string, p: unknown): void }).emit(
        "context:quality_degraded",
        { score: clamped, trace },
      );
    }
    return clamped;
  }
}
