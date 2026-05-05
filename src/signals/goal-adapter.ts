import type { ContextSignal } from "../ambient/types.js";
import type { Goal } from "../goals/types.js";
import type { SubGoal } from "../engine/types.js";
import type { VerifyArgs } from "../tools/goal-verifier.js";

export function goalToSubGoal(goal: Goal): SubGoal {
  return {
    id: goal.id,
    description: goal.title,
    status: "in_progress",
    dependsOn: [],
  };
}

export function signalToVerifyArgs(
  signal: ContextSignal,
  goal: Goal,
  userMessage = "",
): VerifyArgs {
  const envelope = JSON.stringify({
    success: true,
    data: `[${signal.source}] ${signal.title}\n${signal.content}`,
  });
  return {
    toolName: "ambient_signal",
    toolArgs: { source: signal.source, priority: signal.priority },
    toolResult: envelope,
    subGoal: goalToSubGoal(goal),
    userMessage,
  };
}
