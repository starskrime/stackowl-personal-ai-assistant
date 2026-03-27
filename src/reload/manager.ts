/**
 * StackOwl — Hot Reload Manager
 *
 * Orchestrates hot-reloading of tracked modules:
 * 1. Watches file changes via chokidar
 * 2. Computes reload order via dependency graph
 * 3. Snapshots current state for rollback
 * 4. Validates → loads → restores on failure
 */

import { watch, type FSWatcher } from "chokidar";
import type { ReloadableModule, ReloadEvent, ModuleSnapshot } from "./types.js";
import { DependencyGraph } from "./graph.js";
import type { EventBus } from "../events/bus.js";
import { log } from "../logger.js";

export class HotReloadManager {
  private graph: DependencyGraph;
  private snapshots = new Map<string, ModuleSnapshot>();
  private watcher: FSWatcher | null = null;
  private fileToModule = new Map<string, string>(); // filePath → moduleId
  private debounceTimers = new Map<string, NodeJS.Timeout>();
  private debounceMs: number;

  constructor(
    private eventBus: EventBus,
    debounceMs: number = 250,
  ) {
    this.graph = new DependencyGraph();
    this.debounceMs = debounceMs;
  }

  /**
   * Track a reloadable module. Registers it in the dependency graph
   * and maps its file path for file watching.
   */
  track(module: ReloadableModule): void {
    this.graph.register(module);
    this.fileToModule.set(module.filePath, module.id);
    log.engine.debug(
      `[HotReload] Tracking ${module.kind}:${module.id} → ${module.filePath}`,
    );
  }

  /**
   * Stop tracking a module.
   */
  untrack(moduleId: string): void {
    const module = this.graph.get(moduleId);
    if (module) {
      this.fileToModule.delete(module.filePath);
    }
    this.graph.unregister(moduleId);
    this.snapshots.delete(moduleId);
  }

  /**
   * Start watching file paths of all tracked modules.
   */
  startWatching(): void {
    const filePaths = [...this.fileToModule.keys()];
    if (filePaths.length === 0) {
      log.engine.info("[HotReload] No modules tracked, skipping watch setup");
      return;
    }

    log.engine.info(`[HotReload] Watching ${filePaths.length} module file(s)`);

    this.watcher = watch(filePaths, {
      persistent: true,
      ignoreInitial: true,
    });

    this.watcher.on("change", (path) => this.onFileChanged(path));
    this.watcher.on("unlink", (path) => this.onFileRemoved(path));
  }

  /**
   * Stop watching all files.
   */
  async stopWatching(): Promise<void> {
    if (this.watcher) {
      await this.watcher.close();
      this.watcher = null;
    }
    for (const timer of this.debounceTimers.values()) {
      clearTimeout(timer);
    }
    this.debounceTimers.clear();
  }

  /**
   * Reload a specific module and its dependents.
   * Returns reload events for each affected module.
   */
  async reload(moduleId: string): Promise<ReloadEvent[]> {
    const reloadOrder = this.graph.getReloadOrder(moduleId);
    const events: ReloadEvent[] = [];

    this.eventBus.emit("reload:started" as any, {
      moduleId,
      affectedModules: reloadOrder,
    });

    for (const id of reloadOrder) {
      const module = this.graph.get(id);
      if (!module) continue;

      const startTime = Date.now();
      const event: ReloadEvent = {
        moduleId: id,
        kind: module.kind,
        action: "reload",
        success: false,
        rolledBack: false,
        durationMs: 0,
      };

      // Snapshot before reload
      const snapshot = module.snapshot();
      this.snapshots.set(id, snapshot);

      try {
        // Validate new version
        const valid = await module.validate();
        if (!valid) {
          event.error = "Validation failed";
          event.durationMs = Date.now() - startTime;
          events.push(event);

          this.eventBus.emit("reload:failed" as any, {
            moduleId: id,
            error: "Validation failed",
          });
          continue;
        }

        // Unload old version
        await module.unload();

        // Load new version
        await module.load();
        module.version++;

        event.success = true;
        event.durationMs = Date.now() - startTime;
        events.push(event);

        log.engine.info(
          `[HotReload] Reloaded ${module.kind}:${id} v${module.version} (${event.durationMs}ms)`,
        );
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : String(err);
        event.error = errorMsg;
        event.durationMs = Date.now() - startTime;

        // Rollback
        try {
          await module.restore(snapshot);
          event.rolledBack = true;
          log.engine.warn(
            `[HotReload] Rolled back ${module.kind}:${id} after error: ${errorMsg}`,
          );

          this.eventBus.emit("reload:rolledback" as any, {
            moduleId: id,
            error: errorMsg,
          });
        } catch (rollbackErr) {
          log.engine.error(
            `[HotReload] Rollback failed for ${id}: ${rollbackErr instanceof Error ? rollbackErr.message : String(rollbackErr)}`,
          );
        }

        events.push(event);

        this.eventBus.emit("reload:failed" as any, {
          moduleId: id,
          error: errorMsg,
        });
      }
    }

    this.eventBus.emit("reload:completed" as any, {
      moduleId,
      events,
    });

    return events;
  }

  /**
   * Handle file change event (debounced).
   */
  private onFileChanged(filePath: string): void {
    const moduleId = this.fileToModule.get(filePath);
    if (!moduleId) return;

    // Debounce
    const existing = this.debounceTimers.get(moduleId);
    if (existing) clearTimeout(existing);

    this.debounceTimers.set(
      moduleId,
      setTimeout(async () => {
        this.debounceTimers.delete(moduleId);
        log.engine.info(`[HotReload] File changed: ${filePath}`);
        await this.reload(moduleId);
      }, this.debounceMs),
    );
  }

  /**
   * Handle file removal.
   */
  private onFileRemoved(filePath: string): void {
    const moduleId = this.fileToModule.get(filePath);
    if (!moduleId) return;

    log.engine.warn(
      `[HotReload] File removed: ${filePath} (module: ${moduleId})`,
    );
    this.untrack(moduleId);
  }

  /**
   * Get the dependency graph (for inspection/debugging).
   */
  getGraph(): DependencyGraph {
    return this.graph;
  }

  /**
   * List all tracked modules.
   */
  listTracked(): string[] {
    return this.graph.listAll();
  }
}
