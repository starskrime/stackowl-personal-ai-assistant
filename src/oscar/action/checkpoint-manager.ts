import type { ScreenGraph, AppInfo } from "../types.js";
import type { ExecutionPlan } from "../intent/decomposer.js";
import type { Checkpoint, AppState } from "./recovery-controller.js";

export interface StateDiff {
  type: "added" | "removed" | "modified";
  path: string[];
  oldValue?: unknown;
  newValue?: unknown;
}

export interface IncrementalCheckpoint {
  id: string;
  planId: string;
  stepIndex: number;
  timestamp: number;
  diffs: StateDiff[];
  baseCheckpointId?: string;
}

export class CheckpointManager {
  private checkpoints: Map<string, Checkpoint> = new Map();
  private incrementalDiffs: Map<string, IncrementalCheckpoint[]> = new Map();
  private lastCheckpoint: Checkpoint | null = null;
  private maxCheckpoints = 10;

  create(
    plan: ExecutionPlan,
    stepIndex: number,
    state: {
      screenGraph?: ScreenGraph;
      activeApps?: AppInfo[];
    }
  ): Checkpoint {
    const checkpoint: Checkpoint = {
      id: `cp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      planId: plan.id,
      stepIndex,
      timestamp: Date.now(),
      screenState: state.screenGraph ? this.serializeScreenGraph(state.screenGraph) : null,
      appStates: new Map(
        (state.activeApps || []).map((app) => [
          app.bundleId,
          this.captureAppState(app),
        ])
      ),
    };

    if (this.lastCheckpoint) {
      const diffs = this.computeDiffs(this.lastCheckpoint, checkpoint);
      if (diffs.length > 0) {
        const incremental: IncrementalCheckpoint = {
          id: checkpoint.id,
          planId: checkpoint.planId,
          stepIndex,
          timestamp: checkpoint.timestamp,
          diffs,
          baseCheckpointId: this.lastCheckpoint.id,
        };
        this.storeIncrementalDiff(incremental);
      }
    }

    this.checkpoints.set(checkpoint.id, checkpoint);
    this.lastCheckpoint = checkpoint;

    this.pruneOldCheckpoints();

    console.log(`[Checkpoint] Created checkpoint ${checkpoint.id} at step ${stepIndex}`);
    return checkpoint;
  }

  get(checkpointId: string): Checkpoint | undefined {
    return this.checkpoints.get(checkpointId);
  }

  getLatest(): Checkpoint | null {
    return this.lastCheckpoint;
  }

  getForStep(planId: string, stepIndex: number): Checkpoint | undefined {
    for (const cp of this.checkpoints.values()) {
      if (cp.planId === planId && cp.stepIndex === stepIndex) {
        return cp;
      }
    }
    return undefined;
  }

  restore(checkpointId: string): {
    screenGraph?: ScreenGraph;
    appStates: AppInfo[];
  } | null {
    const checkpoint = this.checkpoints.get(checkpointId);
    if (!checkpoint) {
      console.warn(`[Checkpoint] Not found: ${checkpointId}`);
      return null;
    }

    return {
      screenGraph: checkpoint.screenState
        ? this.deserializeScreenGraph(checkpoint.screenState)
        : undefined,
      appStates: Array.from(checkpoint.appStates.values()).map((state) => ({
        bundleId: state.bundleId,
        name: state.bundleId.split(".").pop() || state.bundleId,
        pid: 0,
      })),
    };
  }

  restoreLatest(): {
    screenGraph?: ScreenGraph;
    appStates: AppInfo[];
  } | null {
    if (!this.lastCheckpoint) return null;
    return this.restore(this.lastCheckpoint.id);
  }

  async restoreAndApply(
    checkpointId: string,
    target: {
      restoreScreen?: (state: ScreenGraph) => Promise<void>;
      restoreApp?: (bundleId: string, state: AppState) => Promise<void>;
    }
  ): Promise<boolean> {
    const restored = this.restore(checkpointId);
    if (!restored) return false;

    if (restored.screenGraph && target.restoreScreen) {
      await target.restoreScreen(restored.screenGraph);
    }

    for (const app of restored.appStates) {
      const state = this.lastCheckpoint?.appStates.get(app.bundleId);
      if (state && target.restoreApp) {
        await target.restoreApp(app.bundleId, state);
      }
    }

    console.log(`[Checkpoint] Restored checkpoint ${checkpointId}`);
    return true;
  }

  getCheckpointSize(checkpoint: Checkpoint): number {
    let size = 0;

    if (checkpoint.screenState) {
      size += JSON.stringify(checkpoint.screenState).length;
    }

    if (checkpoint.appStates) {
      for (const state of checkpoint.appStates.values()) {
        size += JSON.stringify(state).length;
      }
    }

    return size;
  }

  getTotalSize(): number {
    let total = 0;
    for (const cp of this.checkpoints.values()) {
      total += this.getCheckpointSize(cp);
    }
    for (const diffs of this.incrementalDiffs.values()) {
      for (const diff of diffs) {
        total += JSON.stringify(diff).length;
      }
    }
    return total;
  }

  private serializeScreenGraph(graph: ScreenGraph): unknown {
    return {
      id: graph.id,
      timestamp: graph.timestamp,
      resolution: graph.resolution,
      elementCount: graph.elements.size,
      regions: graph.regions.map((r) => ({
        type: r.type,
        bounds: r.bounds,
      })),
    };
  }

  private deserializeScreenGraph(data: unknown): ScreenGraph {
    interface SerializedScreenGraph {
      id: string;
      timestamp: number;
      resolution: { width: number; height: number };
      regions: Array<{ type: string; bounds: { x: number; y: number; width: number; height: number } }>;
    }
    const state = data as SerializedScreenGraph;
    return {
      id: state.id,
      timestamp: state.timestamp,
      resolution: state.resolution,
      elements: new Map(),
      regions: state.regions.map((r, i) => ({
        id: `region_${i}`,
        type: r.type as "toolbar" | "sidebar" | "content" | "dialog" | "menu" | "statusbar" | "unknown",
        bounds: r.bounds,
        elements: [],
      })),
      focus: {
        app: "",
        element: null,
        cursor: { x: 0, y: 0 },
      },
    };
  }

  private captureAppState(app: AppInfo): AppState {
    return {
      bundleId: app.bundleId,
      openDocuments: [],
      modified: false,
      cursorPosition: { x: 0, y: 0 },
      panelStates: {},
    };
  }

  private computeDiffs(before: Checkpoint, after: Checkpoint): StateDiff[] {
    const diffs: StateDiff[] = [];

    if (before.screenState !== after.screenState) {
      diffs.push({
        type: "modified",
        path: ["screenState"],
        oldValue: before.screenState,
        newValue: after.screenState,
      });
    }

    const beforeApps = new Set(before.appStates.keys());
    const afterApps = new Set(after.appStates.keys());

    for (const appId of afterApps) {
      if (!beforeApps.has(appId)) {
        diffs.push({
          type: "added",
          path: ["appStates", appId],
          newValue: after.appStates.get(appId),
        });
      }
    }

    for (const appId of beforeApps) {
      if (!afterApps.has(appId)) {
        diffs.push({
          type: "removed",
          path: ["appStates", appId],
          oldValue: before.appStates.get(appId),
        });
      }
    }

    for (const appId of beforeApps) {
      if (afterApps.has(appId)) {
        const beforeState = before.appStates.get(appId)!;
        const afterState = after.appStates.get(appId)!;

        if (JSON.stringify(beforeState) !== JSON.stringify(afterState)) {
          diffs.push({
            type: "modified",
            path: ["appStates", appId],
            oldValue: beforeState,
            newValue: afterState,
          });
        }
      }
    }

    return diffs;
  }

  private storeIncrementalDiff(incremental: IncrementalCheckpoint): void {
    const key = incremental.baseCheckpointId || incremental.id;
    if (!this.incrementalDiffs.has(key)) {
      this.incrementalDiffs.set(key, []);
    }
    this.incrementalDiffs.get(key)!.push(incremental);
  }

  getIncrementalDiffs(baseId: string): IncrementalCheckpoint[] {
    return this.incrementalDiffs.get(baseId) || [];
  }

  reconstructFromDiffs(baseId: string): Checkpoint | null {
    const base = this.checkpoints.get(baseId);
    if (!base) return null;

    const diffs = this.getIncrementalDiffs(baseId);
    if (diffs.length === 0) return base;

    let current = { ...base };

    for (const diff of diffs) {
      current = this.applyDiffs(current, diff.diffs);
    }

    return current;
  }

  private applyDiffs(state: Checkpoint, diffs: StateDiff[]): Checkpoint {
    const result = { ...state };

    for (const diff of diffs) {
      this.applyDiffToState(result, diff);
    }

    return result;
  }

  private applyDiffToState(state: Checkpoint, diff: StateDiff): void {
    if (diff.path.length === 0) return;

    let current: Record<string, unknown> = state as unknown as Record<string, unknown>;

    for (let i = 0; i < diff.path.length - 1; i++) {
      const key = diff.path[i];
      if (!(key in current)) {
        current[key] = {};
      }
      current = current[key] as Record<string, unknown>;
    }

    const lastKey = diff.path[diff.path.length - 1];

    switch (diff.type) {
      case "added":
      case "modified":
        current[lastKey] = diff.newValue;
        break;
      case "removed":
        delete current[lastKey];
        break;
    }
  }

  private pruneOldCheckpoints(): void {
    if (this.checkpoints.size <= this.maxCheckpoints) return;

    const sorted = Array.from(this.checkpoints.entries()).sort(
      (a, b) => a[1].timestamp - b[1].timestamp
    );

    const toRemove = sorted.slice(0, this.checkpoints.size - this.maxCheckpoints);

    for (const [id] of toRemove) {
      this.checkpoints.delete(id);
      this.incrementalDiffs.delete(id);
    }
  }

  clear(): void {
    this.checkpoints.clear();
    this.incrementalDiffs.clear();
    this.lastCheckpoint = null;
    console.log("[Checkpoint] All checkpoints cleared");
  }

  getStats(): {
    count: number;
    totalSizeBytes: number;
    latestTimestamp?: number;
  } {
    return {
      count: this.checkpoints.size,
      totalSizeBytes: this.getTotalSize(),
      latestTimestamp: this.lastCheckpoint?.timestamp,
    };
  }
}

export const checkpointManager = new CheckpointManager();
