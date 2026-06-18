import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class CrossSessionFactsLayer implements ContextLayer {
  name = "CrossSessionFactsLayer";
  priority = 35;
  maxTokens = 400;
  produces = ["cross_session_facts"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const userMemoryContext = (req.session as any).userMemoryContext as string | undefined;
    if (!userMemoryContext) return "";
    return `<cross_session_facts>\n${userMemoryContext}\n</cross_session_facts>`;
  }
}

export class OpenTasksLayer implements ContextLayer {
  name = "OpenTasksLayer";
  priority = 40;
  maxTokens = 300;
  produces = ["open_tasks"];
  dependsOn = ["digest"];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.hasActiveItems; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const tasks = (req.session as any).owlTasks as Array<{ title: string; status: string }> | undefined;
    const open = tasks?.filter((t) => t.status !== "complete").slice(0, 5) ?? [];
    if (!open.length) return "";
    const lines = ["<open_tasks>"];
    for (const task of open) {
      lines.push(`  <task status="${task.status}">${task.title}</task>`);
    }
    lines.push("</open_tasks>");
    return lines.join("\n");
  }
}

export class RelationshipContextLayer implements ContextLayer {
  name = "RelationshipContextLayer";
  priority = 45;
  maxTokens = 300;
  produces = ["relationship"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const relationship = (req.session as any).relationshipContext as string | undefined;
    if (!relationship) return "";
    return `<user_relationship>\n${relationship}\n</user_relationship>`;
  }
}
