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
    const dependants = new Map<string, string[]>(); // produced-key → layer names that need it

    for (const layer of layers) {
      let deg = 0;
      for (const dep of new Set(layer.dependsOn)) {
        if (producers.has(dep)) {
          deg++;
          const list = dependants.get(dep) ?? [];
          list.push(layer.name);
          dependants.set(dep, list);
        }
        // deps with no producer are treated as always-available (empty string)
      }
      inDegree.set(layer.name, deg);
    }

    const byName = new Map<string, ContextLayer>(layers.map((l) => [l.name, l]));
    const batches: ContextLayer[][] = [];
    const remaining = new Set<string>(layers.map((l) => l.name));

    while (remaining.size > 0) {
      const ready = [...remaining].filter((name) => inDegree.get(name) === 0);
      if (ready.length === 0) {
        throw new CircularDependencyError([...remaining]);
      }
      const batch = ready
        .map((name) => {
          const layer = byName.get(name);
          if (!layer) throw new Error(`DAGPlanner: unknown layer name "${name}"`);
          return layer;
        })
        .sort((a, b) => a.priority - b.priority); // lower priority value = higher precedence
      batches.push(batch);

      for (const name of ready) {
        remaining.delete(name);
        const layer = byName.get(name);
        if (!layer) continue;
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
