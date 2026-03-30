import type {
  CanonicalAction,
  CanonicalTarget,
  AccessibilityElement,
  ScreenGraph,
  AppInfo,
  ResolvedStep,
  BoundingBox,
  ActionType,
} from "../../types.js";
import { macOSAdapter } from "../../platform/adapters/macos.js";
import * as fs from "fs";
import * as path from "path";

interface AppKnowledge {
  bundleId: string;
  version: string;
  actionMappings: Record<string, UIOperation[]>;
  shortcuts: Record<string, string>;
  patterns: {
    toolbarRegion?: BoundingBox;
    menuBarItems?: string[];
    statusBarLocation?: "top" | "bottom";
  };
  actionStats: Record<string, { attempts: number; successes: number }>;
}

interface UIOperation {
  type: "click" | "type" | "hotkey" | "menu" | "drag" | "invoke";
  target: { type: "label" | "role" | "index" | "path"; value: string };
  params?: Record<string, unknown>;
  requiresVerification?: boolean;
}

export class CanonicalActionResolver {
  private adapter = macOSAdapter;
  private appKnowledgeCache: Map<string, AppKnowledge> = new Map();
  private knowledgeDir = "./workspace/oscar/app-knowledge";

  constructor() {
    this.ensureKnowledgeDir();
    this.loadBuiltInKnowledge();
  }

  private ensureKnowledgeDir(): void {
    if (!fs.existsSync(this.knowledgeDir)) {
      fs.mkdirSync(this.knowledgeDir, { recursive: true });
    }
  }

  private loadBuiltInKnowledge(): void {
    this.registerAppKnowledge({
      bundleId: "com.apple.finder",
      version: "*",
      actionMappings: {
        "file.open": [
          { type: "menu", target: { type: "label", value: "Open" }, params: { menu: "File" } },
        ],
        "file.save": [
          { type: "hotkey", target: { type: "path", value: "cmd+s" } },
        ],
        "file.delete": [
          { type: "hotkey", target: { type: "path", value: "delete" } },
        ],
        "edit.select_all": [
          { type: "hotkey", target: { type: "path", value: "cmd+a" } },
        ],
        "navigate.backward": [
          { type: "hotkey", target: { type: "path", value: "cmd+[" } },
        ],
        "navigate.forward": [
          { type: "hotkey", target: { type: "path", value: "cmd+]" } },
        ],
      },
      shortcuts: {
        "file.open": "cmd+o",
        "file.save": "cmd+s",
        "edit.undo": "cmd+z",
        "edit.redo": "cmd+shift+z",
      },
      patterns: {
        menuBarItems: ["Apple", "File", "Edit", "View", "Go", "Window", "Help"],
      },
      actionStats: {},
    });

    this.registerAppKnowledge({
      bundleId: "com.apple.Safari",
      version: "*",
      actionMappings: {
        "navigate.reload": [
          { type: "hotkey", target: { type: "path", value: "cmd+r" } },
        { type: "click", target: { type: "role", value: "AXButton" }, params: { label: "Reload" } },
        ],
        "navigate.back": [
          { type: "hotkey", target: { type: "path", value: "cmd+[" } },
        ],
        "navigate.forward": [
          { type: "hotkey", target: { type: "path", value: "cmd+]" } },
        ],
        "view.zoom_in": [
          { type: "hotkey", target: { type: "path", value: "cmd+plus" } },
        ],
        "view.zoom_out": [
          { type: "hotkey", target: { type: "path", value: "cmd+minus" } },
        ],
      },
      shortcuts: {
        "navigate.reload": "cmd+r",
        "view.zoom_in": "cmd+plus",
        "view.zoom_out": "cmd+minus",
      },
      patterns: {
        toolbarRegion: { x: 0, y: 0, width: 1920, height: 50 },
      },
      actionStats: {},
    });

    this.registerAppKnowledge({
      bundleId: "com.adobe.Photoshop*",
      version: "*",
      actionMappings: {
        "tool.select": [
          { type: "click", target: { type: "role", value: "AXImage" } },
        ],
        "layer.delete": [
          { type: "hotkey", target: { type: "path", value: "delete" } },
          { type: "menu", target: { type: "label", value: "Delete" }, params: { menu: "Layer" } },
        ],
        "edit.undo": [
          { type: "hotkey", target: { type: "path", value: "cmd+z" } },
        ],
        "file.save": [
          { type: "hotkey", target: { type: "path", value: "cmd+s" } },
        ],
        "file.export": [
          { type: "menu", target: { type: "label", value: "Export As" }, params: { menu: "File" } },
        ],
        "selection.select_all": [
          { type: "hotkey", target: { type: "path", value: "cmd+a" } },
        ],
        "selection.deselect": [
          { type: "hotkey", target: { type: "path", value: "cmd+d" } },
        ],
      },
      shortcuts: {
        "edit.undo": "cmd+z",
        "edit.redo": "cmd+shift+z",
        "layer.delete": "delete",
      },
      patterns: {
        toolbarRegion: { x: 0, y: 0, width: 1920, height: 100 },
      },
      actionStats: {},
    });

    this.registerAppKnowledge({
      bundleId: "com.microsoft.Word",
      version: "*",
      actionMappings: {
        "file.save": [
          { type: "hotkey", target: { type: "path", value: "cmd+s" } },
        ],
        "file.new": [
          { type: "hotkey", target: { type: "path", value: "cmd+n" } },
        ],
        "edit.undo": [
          { type: "hotkey", target: { type: "path", value: "cmd+z" } },
        ],
        "edit.find": [
          { type: "hotkey", target: { type: "path", value: "cmd+f" } },
        ],
        "format.bold": [
          { type: "hotkey", target: { type: "path", value: "cmd+b" } },
        ],
        "format.italic": [
          { type: "hotkey", target: { type: "path", value: "cmd+i" } },
        ],
      },
      shortcuts: {
        "file.save": "cmd+s",
        "edit.undo": "cmd+z",
        "format.bold": "cmd+b",
        "format.italic": "cmd+i",
      },
      patterns: {
        toolbarRegion: { x: 0, y: 0, width: 1920, height: 75 },
        menuBarItems: ["File", "Edit", "View", "Insert", "Format", "Tools", "Table", "Window", "Help"],
      },
      actionStats: {},
    });

    this.registerAppKnowledge({
      bundleId: "com.apple.Terminal",
      version: "*",
      actionMappings: {
        "edit.copy": [
          { type: "hotkey", target: { type: "path", value: "cmd+c" } },
        ],
        "edit.paste": [
          { type: "hotkey", target: { type: "path", value: "cmd+v" } },
        ],
        "edit.select_all": [
          { type: "hotkey", target: { type: "path", value: "cmd+a" } },
        ],
        "window.new": [
          { type: "hotkey", target: { type: "path", value: "cmd+n" } },
        ],
      },
      shortcuts: {
        "edit.copy": "cmd+c",
        "edit.paste": "cmd+v",
      },
      patterns: {},
      actionStats: {},
    });
  }

  registerAppKnowledge(knowledge: AppKnowledge): void {
    this.appKnowledgeCache.set(knowledge.bundleId, knowledge);
  }

  async resolve(
    actionType: string,
    target: CanonicalTarget,
    app: AppInfo,
    _context?: ScreenGraph
  ): Promise<ResolvedStep[]> {
    const knowledge = this.findKnowledge(app.bundleId);

    if (knowledge) {
      const mapped = this.resolveFromKnowledge(actionType, knowledge);
      if (mapped.length > 0) {
        return mapped;
      }
    }

    const resolved: ResolvedStep[] = [];

    if (target.semanticSelector) {
      const semanticResolved = await this.resolveViaSemantic(target, actionType);
      if (semanticResolved) {
        resolved.push(semanticResolved);
      }
    }

    if (target.visualRegion) {
      resolved.push({
        action: {
          type: "click",
          target,
          params: {},
          timestamp: Date.now(),
          traceId: generateTraceId(),
        },
        confidence: 0.6,
        alternatives: [],
      });
    }

    resolved.push({
      action: {
        type: "click",
        target,
        params: {},
        timestamp: Date.now(),
        traceId: generateTraceId(),
      },
      confidence: 0.4,
      alternatives: [],
    });

    return resolved;
  }

  private resolveFromKnowledge(actionType: string, knowledge: AppKnowledge): ResolvedStep[] {
    const mappings = knowledge.actionMappings[actionType];
    if (!mappings || mappings.length === 0) return [];

    return mappings.map((op) => {
      const action = this.operationToAction(op);
      return {
        action,
        confidence: this.calculateConfidence(actionType, knowledge),
        alternatives: mappings.slice(1).map((op) => this.operationToAction(op)),
      };
    });
  }

  private operationToAction(op: UIOperation): CanonicalAction {
    const params: Record<string, unknown> = { ...op.params };

    switch (op.target.type) {
      case "label":
        params.label = op.target.value;
        break;
      case "role":
        params.role = op.target.value;
        break;
      case "index":
        params.index = parseInt(op.target.value);
        break;
      case "path":
        params.path = op.target.value;
        break;
    }

    return {
      type: op.type === "menu" ? "click" : op.type,
      target: {
        semanticSelector: {
          label: op.target.type === "label" ? op.target.value : undefined,
          role: op.target.type === "role" ? op.target.value : undefined,
          index: op.target.type === "index" ? parseInt(op.target.value) : undefined,
        },
      },
      params,
      timestamp: Date.now(),
      traceId: generateTraceId(),
    };
  }

  private async resolveViaSemantic(
    target: CanonicalTarget,
    actionType: string
  ): Promise<ResolvedStep | null> {
    if (!target.semanticSelector) return null;

    const selector = target.semanticSelector;
    const state = await this.adapter.getAccessibilityTree();

    for (const [_id, elem] of state.elements) {
      if (selector.role && elem.role !== selector.role) continue;
      if (selector.label && !elem.label?.includes(selector.label)) continue;

      return {
        action: {
          type: this.inferActionType(actionType, elem),
          target: {
            accessibilityPath: elem.path,
            semanticSelector: selector,
          },
          params: {},
          timestamp: Date.now(),
          traceId: generateTraceId(),
        },
        confidence: 0.85,
        alternatives: [],
      };
    }

    return null;
  }

  private inferActionType(semanticAction: string, elem: AccessibilityElement): ActionType {
    if (elem.role === "AXButton" || elem.role === "AXMenuItem") {
      return "click";
    }
    if (elem.role === "AXTextField" || elem.role === "AXTextArea") {
      return "type";
    }
    if (elem.role === "AXScrollArea") {
      return "scroll";
    }

    if (semanticAction.includes("click")) return "click";
    if (semanticAction.includes("type") || semanticAction.includes("edit")) return "type";
    if (semanticAction.includes("scroll")) return "scroll";
    if (semanticAction.includes("hotkey")) return "hotkey";

    return "click";
  }

  private calculateConfidence(actionType: string, knowledge: AppKnowledge): number {
    const stats = knowledge.actionStats[actionType];
    if (!stats || stats.attempts === 0) return 0.7;

    const successRate = stats.successes / stats.attempts;
    const recencyBonus = stats.attempts > 10 ? 0.1 : 0;

    return Math.min(0.95, successRate * 0.9 + recencyBonus);
  }

  private findKnowledge(bundleId: string): AppKnowledge | null {
    if (this.appKnowledgeCache.has(bundleId)) {
      return this.appKnowledgeCache.get(bundleId)!;
    }

    for (const [pattern, knowledge] of this.appKnowledgeCache) {
      if (this.matchesBundleId(bundleId, pattern)) {
        return knowledge;
      }
    }

    const loaded = this.loadKnowledgeFromDisk(bundleId);
    if (loaded) {
      this.appKnowledgeCache.set(bundleId, loaded);
      return loaded;
    }

    return null;
  }

  private matchesBundleId(bundleId: string, pattern: string): boolean {
    if (pattern.endsWith("*")) {
      const prefix = pattern.slice(0, -1);
      return bundleId.startsWith(prefix);
    }
    return bundleId === pattern;
  }

  private loadKnowledgeFromDisk(bundleId: string): AppKnowledge | null {
    const safeName = bundleId.replace(/[^a-zA-Z0-9]/g, "_");
    const filePath = path.join(this.knowledgeDir, `${safeName}.json`);

    if (fs.existsSync(filePath)) {
      try {
        const data = fs.readFileSync(filePath, "utf-8");
        return JSON.parse(data);
      } catch {}
    }

    return null;
  }

  saveKnowledge(knowledge: AppKnowledge): void {
    this.appKnowledgeCache.set(knowledge.bundleId, knowledge);

    const safeName = knowledge.bundleId.replace(/[^a-zA-Z0-9]/g, "_");
    const filePath = path.join(this.knowledgeDir, `${safeName}.json`);

    fs.writeFileSync(filePath, JSON.stringify(knowledge, null, 2));
  }

  recordActionResult(
    bundleId: string,
    actionType: string,
    success: boolean
  ): void {
    let knowledge = this.findKnowledge(bundleId);

    if (!knowledge) {
      knowledge = {
        bundleId,
        version: "*",
        actionMappings: {},
        shortcuts: {},
        patterns: {},
        actionStats: {},
      };
      this.appKnowledgeCache.set(bundleId, knowledge);
    }

    if (!knowledge.actionStats[actionType]) {
      knowledge.actionStats[actionType] = { attempts: 0, successes: 0 };
    }

    knowledge.actionStats[actionType].attempts++;
    if (success) {
      knowledge.actionStats[actionType].successes++;
    }
  }

  async resolveHotkey(shortcut: string, app: AppInfo): Promise<CanonicalAction> {
    const knowledge = this.findKnowledge(app.bundleId);

    if (knowledge?.shortcuts) {
      for (const [_action, keys] of Object.entries(knowledge.shortcuts)) {
        if (keys === shortcut) {
          return {
            type: "hotkey",
            target: { appBundleId: app.bundleId },
            params: { key: shortcut },
            timestamp: Date.now(),
            traceId: generateTraceId(),
          };
        }
      }
    }

    const parts = shortcut.toLowerCase().split("+");
    const modifiers: string[] = [];
    let key = "";

    for (const part of parts) {
      switch (part.trim()) {
        case "cmd":
        case "command":
          modifiers.push("command");
          break;
        case "ctrl":
        case "control":
          modifiers.push("control");
          break;
        case "opt":
        case "option":
        case "alt":
          modifiers.push("option");
          break;
        case "shift":
          modifiers.push("shift");
          break;
        default:
          key = part.trim();
      }
    }

    return {
      type: "hotkey",
      target: { appBundleId: app.bundleId },
      params: { key, modifiers },
      timestamp: Date.now(),
      traceId: generateTraceId(),
    };
  }
}

function generateTraceId(): string {
  return `trace_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export const actionResolver = new CanonicalActionResolver();
