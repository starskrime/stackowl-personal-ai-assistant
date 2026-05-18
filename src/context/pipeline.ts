import { BudgetController, estimateTokens } from "./budget-controller.js";
import type { ContextCache } from "./cache.js";
import type { LayerHealthMonitor } from "./circuit-breaker.js";
import { DAGPlanner } from "./dag-planner.js";
import type {
  ContextLayer,
  ContextRequest,
  TriageSignals,
  ContextBuildTrace,
  ContextBuildTraceEntry,
} from "./layer.js";
import { log } from "../logger.js";

const LAYER_TIMEOUT_MS = 2_000;
const PIPELINE_TIMEOUT_MS = 5_000;

export class ContextPipeline {
  private readonly batches: ContextLayer[][];
  private readonly shortTermLayers = new Map<string, {
    content: string;
    priority: number;
    ttlTurns: number;
  }>();
  public lastRetrievedPelletIds: string[] = [];

  constructor(
    private readonly layers: ContextLayer[],
    private readonly cache: ContextCache,
    private readonly healthMonitor: LayerHealthMonitor,
    dagPlanner: DAGPlanner,
  ) {
    this.batches = dagPlanner.buildBatches(layers);
  }

  setShortTermLayer(
    key: string,
    content: string,
    opts: { priority: number; ttlTurns: number },
  ): void {
    this.shortTermLayers.set(key, { content, priority: opts.priority, ttlTurns: opts.ttlTurns });
  }

  /**
   * Drop a short-term layer (used by fact retraction — see FactRetractor).
   * Returns true when an entry was removed, false when no key matched.
   */
  removeShortTermLayer(key: string): boolean {
    return this.shortTermLayers.delete(key);
  }

  async run(
    request: ContextRequest,
    triage: TriageSignals,
    options?: { timeoutMs?: number; globalTokenCeiling?: number },
  ): Promise<{ output: string; trace: ContextBuildTrace }> {
    const budget = new BudgetController(options?.globalTokenCeiling ?? 8_000);
    const results = new Map<string, string>();
    const trace: ContextBuildTrace = [];
    const pipelineDeadline =
      Date.now() + (options?.timeoutMs ?? PIPELINE_TIMEOUT_MS);

    for (let batchIdx = 0; batchIdx < this.batches.length; batchIdx++) {
      const batch = this.batches[batchIdx];
      await Promise.all(
        batch.map((layer) =>
          this.executeLayer(
            layer,
            request,
            triage,
            results,
            budget,
            trace,
            batchIdx,
            pipelineDeadline,
          ),
        ),
      );
    }

    // Collect short-term layers that still have TTL > 0
    const shortTermEntries: Array<{ content: string; priority: number; key: string }> = [];
    for (const [key, stl] of this.shortTermLayers) {
      if (stl.ttlTurns > 0) {
        shortTermEntries.push({ content: stl.content, priority: stl.priority, key });
        stl.ttlTurns -= 1;
        if (stl.ttlTurns === 0) {
          this.shortTermLayers.delete(key);
        }
      }
    }

    const output = [
      ...[...this.layers]
        .sort((a, b) => a.priority - b.priority)
        .map((l) => ({ content: results.get(l.produces[0] ?? l.name) ?? "", priority: l.priority })),
      ...shortTermEntries,
    ]
      .sort((a, b) => a.priority - b.priority)
      .map((e) => e.content)
      .filter(Boolean)
      .join("\n");

    log.engine.info(
      `ContextPipeline: ${trace.length} layers, ${trace.filter((t) => t.fired).length} fired`,
    );

    this.lastRetrievedPelletIds = [];

    return { output, trace };
  }

  private async executeLayer(
    layer: ContextLayer,
    request: ContextRequest,
    triage: TriageSignals,
    results: Map<string, string>,
    budget: BudgetController,
    trace: ContextBuildTrace,
    batchIndex: number,
    deadline: number,
  ): Promise<void> {
    const start = Date.now();

    const skip = (skippedReason: ContextBuildTraceEntry["skippedReason"]) => {
      for (const key of layer.produces) results.set(key, "");
      trace.push({
        layerName: layer.name,
        priority: layer.priority,
        batchIndex,
        fired: false,
        cacheHit: false,
        tokensUsed: 0,
        durationMs: Date.now() - start,
        skippedReason,
      });
    };

    if (Date.now() > deadline) return skip("pipeline_timeout");

    const shouldFire = layer.alwaysInclude || layer.shouldFire(triage);
    if (!shouldFire) return skip("shouldFire=false");

    if (!layer.alwaysInclude && this.healthMonitor.shouldBypass(layer.name)) {
      return skip("circuit_open");
    }

    // Resolve deps map for this layer — typed as ReadonlyMap to match build() signature
    const deps: ReadonlyMap<string, string> = new Map<string, string>(
      layer.dependsOn.map((depKey) => [depKey, results.get(depKey) ?? ""]),
    );

    // Cache check
    const cacheKey = layer.getCacheKey?.(request, triage) ?? null;
    if (cacheKey !== null) {
      const cached = this.cache.get(layer.name, cacheKey);
      if (cached !== null) {
        const budgeted = budget.apply(layer.name, cached, layer.maxTokens);
        for (const key of layer.produces) results.set(key, budgeted);
        trace.push({
          layerName: layer.name,
          priority: layer.priority,
          batchIndex,
          fired: true,
          cacheHit: true,
          tokensUsed: estimateTokens(budgeted),
          durationMs: Date.now() - start,
        });
        return;
      }
    }

    try {
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      const output = await Promise.race([
        layer.build(request, triage, deps).finally(() => clearTimeout(timeoutId)),
        new Promise<string>((_, reject) => {
          timeoutId = setTimeout(() => reject(new Error("timeout")), LAYER_TIMEOUT_MS);
        }),
      ]);

      this.healthMonitor.getBreaker(layer.name).recordSuccess(Date.now() - start);
      const budgeted = budget.apply(layer.name, output, layer.maxTokens);

      if (cacheKey !== null && budgeted) {
        this.cache.set(
          layer.name,
          cacheKey,
          budgeted,
          layer.cacheTtlMs ?? 300_000,
          triage.effectiveUserId,
        );
      }

      for (const key of layer.produces) results.set(key, budgeted);
      trace.push({
        layerName: layer.name,
        priority: layer.priority,
        batchIndex,
        fired: true,
        cacheHit: false,
        tokensUsed: estimateTokens(budgeted),
        durationMs: Date.now() - start,
      });
    } catch (err) {
      this.healthMonitor.getBreaker(layer.name).recordFailure();
      for (const key of layer.produces) results.set(key, "");
      trace.push({
        layerName: layer.name,
        priority: layer.priority,
        batchIndex,
        fired: false,
        cacheHit: false,
        tokensUsed: 0,
        durationMs: Date.now() - start,
        skippedReason: `error: ${err instanceof Error ? err.message : String(err)}`,
      });
    }
  }
}
