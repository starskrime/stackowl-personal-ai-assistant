/**
 * StackOwl — RewardEngine
 *
 * Computes a scalar reward in [-1.0, 1.0] for each completed ReAct loop.
 * Pure function — no LLM calls, no I/O. Reads only from structural signals
 * already present in the runtime (loop outcome, tool counts, synthesis result).
 *
 * Reward table:
 *   feedback_like           +0.50  (applied later via applyFeedback)
 *   feedback_dislike        -0.50  (applied later via applyFeedback)
 *   task_completed          +0.20  (response without loop exhaustion)
 *   loop_exhausted          -0.30  (hit MAX_TOOL_ITERATIONS)
 *   loop_broken_early       -0.20  (broke due to repeated tool failures)
 *   synthesis_success       +0.25  (a new tool/skill was synthesized OK)
 *   synthesis_failure       -0.20  (synthesis was attempted but failed)
 *   tool_success_bonus      +0.05 per unique successful tool (max +0.20)
 *   all_tools_failed        -0.30  (every tool call failed)
 *
 * Clamped to [-1.0, 1.0] by the TrajectoriesRepo.complete() call.
 */

export interface RewardSignals {
  /** True when the ReAct loop hit MAX_TOOL_ITERATIONS */
  loopExhausted: boolean;
  /** True when the loop broke early due to repeated tool failures */
  loopBrokenEarly: boolean;
  /** Number of successful tool calls this run */
  toolSuccessCount: number;
  /** Number of failed tool calls this run */
  toolFailureCount: number;
  /** True if synthesis was attempted and succeeded */
  synthesisSuccess?: boolean;
  /** True if synthesis was attempted and failed */
  synthesisFailure?: boolean;
}

export interface RewardResult {
  reward: number;
  breakdown: Record<string, number>;
  outcome: "success" | "failure" | "abandoned";
}

export class RewardEngine {
  compute(signals: RewardSignals): RewardResult {
    const breakdown: Record<string, number> = {};
    let reward = 0;

    if (signals.loopExhausted) {
      breakdown.loop_exhausted = -0.30;
      reward += breakdown.loop_exhausted;
    } else if (signals.loopBrokenEarly) {
      breakdown.loop_broken_early = -0.20;
      reward += breakdown.loop_broken_early;
    } else {
      breakdown.task_completed = +0.20;
      reward += breakdown.task_completed;
    }

    // All tools failed — strong negative signal
    const totalTools = signals.toolSuccessCount + signals.toolFailureCount;
    if (totalTools > 0 && signals.toolSuccessCount === 0) {
      breakdown.all_tools_failed = -0.30;
      reward += breakdown.all_tools_failed;
    } else if (signals.toolSuccessCount > 0) {
      // Bonus for each unique successful tool call, capped at 0.20
      const bonus = Math.min(signals.toolSuccessCount * 0.05, 0.20);
      breakdown.tool_success_bonus = bonus;
      reward += bonus;
    }

    if (signals.synthesisSuccess) {
      breakdown.synthesis_success = +0.25;
      reward += breakdown.synthesis_success;
    }
    if (signals.synthesisFailure) {
      breakdown.synthesis_failure = -0.20;
      reward += breakdown.synthesis_failure;
    }

    // Clamp
    reward = Math.max(-1.0, Math.min(1.0, reward));

    const outcome: RewardResult["outcome"] =
      signals.loopExhausted || signals.loopBrokenEarly
        ? signals.toolSuccessCount === 0
          ? "failure"
          : "abandoned"
        : "success";

    return { reward, breakdown, outcome };
  }
}
