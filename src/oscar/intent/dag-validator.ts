import type { ExecutionPlan, DecomposedStep } from "./decomposer.js";

export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
  warnings: ValidationWarning[];
  parallelizable: string[][];
  estimatedSuccess: number;
  criticalPath: string[];
}

export interface ValidationError {
  type: "cycle" | "missing_dependency" | "invalid_action" | "circular_dependency";
  nodeId?: string;
  message: string;
  affectingNodes?: string[];
}

export interface ValidationWarning {
  type: "low_confidence" | "no_verification" | "unverified_step" | "potential_flakiness";
  nodeId?: string;
  message: string;
}

export class DAGValidator {
  validate(plan: ExecutionPlan): ValidationResult {
    const errors: ValidationError[] = [];
    const warnings: ValidationWarning[] = [];
    let parallelizable: string[][] = [];

    const cycleResult = this.detectCycles(plan.steps);
    if (cycleResult.hasCycle) {
      errors.push({
        type: "cycle",
        nodeId: cycleResult.node,
        message: `Circular dependency detected involving step "${cycleResult.node}"`,
        affectingNodes: cycleResult.cycleNodes,
      });
    }

    for (const step of plan.steps) {
      if (step.dependsOn) {
        for (const depId of step.dependsOn) {
          if (!plan.steps.find((s) => s.id === depId)) {
            errors.push({
              type: "missing_dependency",
              nodeId: step.id,
              message: `Step "${step.id}" depends on "${depId}" which does not exist`,
            });
          }
        }
      }
    }

    for (const step of plan.steps) {
      if (!step.verification) {
        warnings.push({
          type: "no_verification",
          nodeId: step.id,
          message: `Step "${step.id}" has no verification condition`,
        });
      }

      if (step.estimatedSuccess < 0.5) {
        warnings.push({
          type: "low_confidence",
          nodeId: step.id,
          message: `Step "${step.id}" has low estimated success rate (${(step.estimatedSuccess * 100).toFixed(0)}%)`,
        });
      }
    }

    parallelizable = this.findParallelizableSteps(plan.steps);

    const criticalPath = this.findCriticalPath(plan.steps);

    const estimatedSuccess = this.calculateOverallSuccess(plan.steps);

    return {
      valid: errors.length === 0,
      errors,
      warnings,
      parallelizable,
      estimatedSuccess,
      criticalPath,
    };
  }

  private detectCycles(steps: DecomposedStep[]): { hasCycle: boolean; node?: string; cycleNodes?: string[] } {
    const adjacency = new Map<string, string[]>();
    const inDegree = new Map<string, number>();

    for (const step of steps) {
      adjacency.set(step.id, step.dependsOn || []);
      inDegree.set(step.id, 0);
    }

    for (const step of steps) {
      for (const _dep of step.dependsOn || []) {
        inDegree.set(step.id, (inDegree.get(step.id) || 0) + 1);
      }
    }

    const queue: string[] = [];
    for (const [node, degree] of inDegree) {
      if (degree === 0) queue.push(node);
    }

    const visited: string[] = [];

    while (queue.length > 0) {
      const node = queue.shift()!;
      visited.push(node);

      for (const [stepId, deps] of adjacency) {
        if (deps.includes(node)) {
          const newDegree = (inDegree.get(stepId) || 0) - 1;
          inDegree.set(stepId, newDegree);
          if (newDegree === 0) {
            queue.push(stepId);
          }
        }
      }
    }

    if (visited.length !== steps.length) {
      const cycleNodes = steps.filter((s) => !visited.includes(s.id)).map((s) => s.id);
      return {
        hasCycle: true,
        node: cycleNodes[0],
        cycleNodes,
      };
    }

    return { hasCycle: false };
  }

  private findParallelizableSteps(steps: DecomposedStep[]): string[][] {
    const stages: string[][] = [];
    const completed = new Set<string>();
    const remaining = new Set(steps.map((s) => s.id));

    while (remaining.size > 0) {
      const currentStage: string[] = [];

      for (const step of steps) {
        if (!remaining.has(step.id)) continue;

        const deps = step.dependsOn || [];
        const allDepsCompleted = deps.every((dep) => completed.has(dep));

        if (allDepsCompleted) {
          currentStage.push(step.id);
        }
      }

      if (currentStage.length === 0 && remaining.size > 0) {
        break;
      }

      stages.push(currentStage);

      for (const id of currentStage) {
        remaining.delete(id);
        completed.add(id);
      }
    }

    return stages;
  }

  private findCriticalPath(steps: DecomposedStep[]): string[] {
    const stages = this.findParallelizableSteps(steps);
    const stageMap = new Map<string, number>();

    for (let i = 0; i < stages.length; i++) {
      for (const nodeId of stages[i]) {
        stageMap.set(nodeId, i);
      }
    }

    let maxDuration = 0;
    let maxPath: string[] = [];

    const memo = new Map<string, { duration: number; path: string[] }>();
    const visited = new Set<string>();

    function dfs(stepId: string): { duration: number; path: string[] } {
      if (memo.has(stepId)) {
        return memo.get(stepId)!;
      }

      if (visited.has(stepId)) {
        return { duration: 0, path: [] };
      }
      visited.add(stepId);

      const step = steps.find((s) => s.id === stepId);
      if (!step) {
        visited.delete(stepId);
        return { duration: 0, path: [] };
      }

      const deps = step.dependsOn || [];
      if (deps.length === 0) {
        const result = { duration: 1 / Math.max(step.estimatedSuccess, 0.1), path: [stepId] };
        memo.set(stepId, result);
        visited.delete(stepId);
        return result;
      }

      let maxDep = { duration: 0, path: [] as string[] };
      for (const depId of deps) {
        const depResult = dfs(depId);
        if (depResult.duration > maxDep.duration) {
          maxDep = depResult;
        }
      }

      const result = {
        duration: maxDep.duration + 1 / Math.max(step.estimatedSuccess, 0.1),
        path: [...maxDep.path, stepId],
      };
      memo.set(stepId, result);
      visited.delete(stepId);
      return result;
    }

    for (const step of steps) {
      const result = dfs(step.id);
      if (result.duration > maxDuration) {
        maxDuration = result.duration;
        maxPath = result.path;
      }
    }

    return maxPath;
  }

  private calculateOverallSuccess(steps: DecomposedStep[]): number {
    if (steps.length === 0) return 1;

    const criticalPath = this.findCriticalPath(steps);
    const criticalSteps = steps.filter((s) => criticalPath.includes(s.id));

    let overall = 1;
    for (const step of criticalSteps) {
      overall *= step.estimatedSuccess;
    }

    return Math.max(0.1, overall);
  }

  optimize(plan: ExecutionPlan): ExecutionPlan {
    const optimized = this.reorderForParallelism(plan);

    return {
      ...optimized,
      steps: optimized.steps.map((step, idx) => ({
        ...step,
        id: `step_${idx}`,
        dependsOn: idx > 0 ? [`step_${idx - 1}`] : [],
      })),
    };
  }

  private reorderForParallelism(plan: ExecutionPlan): ExecutionPlan {
    const stages = this.findParallelizableSteps(plan.steps);
    const reorderedSteps: DecomposedStep[] = [];

    for (const stage of stages) {
      const stageSteps = plan.steps.filter((s) => stage.includes(s.id));
      reorderedSteps.push(...stageSteps);
    }

    return {
      ...plan,
      steps: reorderedSteps,
    };
  }

  estimateDuration(plan: ExecutionPlan): number {
    const stages = this.findParallelizableSteps(plan.steps);
    return stages.length;
  }
}

export const dagValidator = new DAGValidator();
