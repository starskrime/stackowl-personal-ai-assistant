import type { ContextLayer } from "./layer.js";

export class CircularDependencyError extends Error {
  constructor(public cycle: string[]) {
    super(`Circular dependency detected: ${cycle.join(" → ")}`);
    this.name = "CircularDependencyError";
  }
}

export class DAGPlanner {
  buildBatches(layers: ContextLayer[]): ContextLayer[][] {
    // Map: produced-key → layer that produces it
    const producers = new Map<string, ContextLayer>();
    for (const layer of layers) {
      for (const key of layer.produces) {
        producers.set(key, layer);
      }
    }

    // In-degree map: how many unresolved deps each layer has
    const inDegree = new Map<string, number>();
    const dependants = new Map<string, string[]>(); // produced-key → layers that need it

    for (const layer of layers) {
      let deg = 0;
      for (const dep of layer.dependsOn) {
        if (producers.has(dep)) {
          deg++;
          const list = dependants.get(dep) ?? [];
          list.push(layer.name);
          dependants.set(dep, list);
        }
        // deps with no producer are treated as always-available (no blocking)
      }
      inDegree.set(layer.name, deg);
    }

    const byName = new Map(layers.map((l) => [l.name, l]));
    const batches: ContextLayer[][] = [];
    let remaining = new Set(layers.map((l) => l.name));

    while (remaining.size > 0) {
      const ready = [...remaining].filter((name) => inDegree.get(name) === 0);
      if (ready.length === 0) {
        // Cycle — report remaining names as cycle
        throw new CircularDependencyError([...remaining]);
      }
      const batch = ready
        .map((name) => byName.get(name)!)
        .sort((a, b) => a.priority - b.priority);
      batches.push(batch);

      for (const name of ready) {
        remaining.delete(name);
        const layer = byName.get(name)!;
        for (const produced of layer.produces) {
          for (const dependant of dependants.get(produced) ?? []) {
            inDegree.set(dependant, (inDegree.get(dependant) ?? 1) - 1);
          }
        }
      }
    }

    return batches;
  }
}
