import { log } from "../logger.js";
import type { RoutingRuleStore, RoutingRule } from "./routing-rule-store.js";
import type { EdgeAccumulator } from "../tools/cortex/edge-accumulator.js";

const MONITOR_WINDOW = 5;         // invocations to watch
const REGRESSION_THRESHOLD = 0.4; // roll back if success rate falls below this

export class RoutingRuleMonitor {
  constructor(
    private store: RoutingRuleStore,
    private edgeAccumulator?: EdgeAccumulator,
  ) {}

  /**
   * Record a tool invocation outcome for all active rules that match.
   * If success rate is below threshold after MONITOR_WINDOW observations, roll back.
   */
  recordOutcome(failingTool: string, _intentSnippet: string, satisfied: boolean): void {
    const activeRules = this.store.getActive().filter(
      (r) => r.failingTool === failingTool || r.suggestedAlternatives.includes(failingTool)
    );

    for (const rule of activeRules) {
      const updated: RoutingRule = {
        ...rule,
        observationCount: rule.observationCount + 1,
        successCount: rule.successCount + (satisfied ? 1 : 0),
      };

      if (updated.observationCount >= MONITOR_WINDOW) {
        const successRate = updated.successCount / updated.observationCount;
        if (successRate < REGRESSION_THRESHOLD) {
          log.engine.warn("routing-rule.regression-detected", {
            id: rule.id,
            failingTool: rule.failingTool,
            successRate,
            observationCount: updated.observationCount,
          });
          updated.disabled = true;
          updated.version = (rule.version ?? 1) + 1;

          // Feed regression back into EdgeAccumulator
          if (this.edgeAccumulator) {
            this.edgeAccumulator.observe({
              fromTool: rule.failingTool,
              toTool: rule.suggestedAlternatives[0] ?? "unknown",
              capabilityTag: rule.intentPattern,
              success: false,
              durationMs: 0,
            });
          }

          log.engine.info("routing-rule.rolled-back", {
            id: rule.id,
            failingTool: rule.failingTool,
            version: updated.version,
          });
        }
      }

      this.store.upsert(updated);
    }
  }
}
