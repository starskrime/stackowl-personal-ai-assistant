import type { ScreenGraph, UIElement, CanonicalTarget, AppInfo } from "../types.js";

export interface ParsedIntent {
  verb: string;
  object: string;
  context: {
    file?: string;
    impliedApp?: string;
    expectedOutcome?: string;
    constraints?: string[];
  };
  confidence: number;
  reasoning: string;
}

export interface DecomposedStep {
  id: string;
  action: string;
  target?: CanonicalTarget;
  params: Record<string, unknown>;
  dependsOn: string[];
  verification?: VerificationSpec;
  alternatives?: string[];
  estimatedSuccess: number;
}

export interface VerificationSpec {
  type: "element_exists" | "element_focused" | "state_changed" | "screenshot_match" | "text_appeared";
  target?: CanonicalTarget;
  expected?: Record<string, unknown>;
  timeout?: number;
}

export interface ExecutionPlan {
  id: string;
  steps: DecomposedStep[];
  currentStep: number;
  status: "planned" | "running" | "paused" | "completed" | "failed";
  startedAt?: number;
  completedAt?: number;
  estimatedDuration?: number;
}

export class IntentParser {
  private verbSynonyms: Map<string, string[]> = new Map([
    ["clear", ["remove", "delete", "erase", "wipe", "eliminate"]],
    ["open", ["launch", "start", "load", "initiate"]],
    ["save", ["store", "write", "export", "backup"]],
    ["close", ["quit", "exit", "terminate", "shut"]],
    ["click", ["press", "select", "activate", "tap"]],
    ["type", ["enter", "input", "write", "fill"]],
    ["scroll", ["pan", "move", "slide", "navigate"]],
    ["find", ["search", "locate", "lookup", "discover"]],
    ["fix", ["repair", "debug", "resolve", "correct", "troubleshoot"]],
    ["debug", ["diagnose", "investigate", "troubleshoot", "fix"]],
    ["test", ["verify", "check", "validate", "run"]],
    ["remove", ["delete", "clear", "strip", "delete"]],
    ["crop", ["trim", "cut", "resize", "frame"]],
    ["export", ["save", "download", "output", "generate"]],
  ]);

  parse(input: string): ParsedIntent {
    const normalized = input.toLowerCase().trim();

    const verb = this.extractVerb(normalized);
    const object = this.extractObject(normalized);
    const context = this.extractContext(normalized, object);
    const confidence = this.calculateConfidence(verb, object, context);

    return {
      verb,
      object,
      context,
      confidence,
      reasoning: this.generateReasoning(verb, object, context),
    };
  }

  private extractVerb(input: string): string {
    const verbPatterns = [
      /^(help me|can you|could you|i want|i need|please)?\s*(clear|remove|open|save|close|click|type|scroll|find|fix|debug|test|launch|crop|export|delete)/i,
      /(clear|remove|open|save|close|click|type|scroll|find|fix|debug|test|launch|crop|export|delete)\s+(the|a|my|an)/i,
    ];

    for (const pattern of verbPatterns) {
      const match = input.match(pattern);
      if (match) {
        const verb = (match[2] || match[1] || "").toLowerCase();
        return this.normalizeVerb(verb);
      }
    }

    return this.inferVerbFromContext(input);
  }

  private normalizeVerb(verb: string): string {
    for (const [canonical, synonyms] of this.verbSynonyms) {
      if (synonyms.includes(verb)) {
        return canonical;
      }
    }
    return verb;
  }

  private extractObject(input: string): string {
    const prepositions = ["in", "on", "at", "to", "from", "with", "the", "a", "an", "my"];
    let object = input;

    for (const prep of prepositions) {
      const regex = new RegExp(`\\b${prep}\\b`, "gi");
      object = object.replace(regex, " ");
    }

    const actionWords = [
      "clear", "remove", "open", "save", "close", "click", "type", "scroll",
      "find", "fix", "debug", "test", "launch", "crop", "export", "delete",
      "help", "me", "please", "could", "you", "can", "i", "want", "need",
    ];

    object = object
      .split(/\s+/)
      .filter((word) => !actionWords.includes(word.toLowerCase()))
      .join(" ")
      .trim();

    return object || "unknown";
  }

  private extractContext(input: string, _object: string): ParsedIntent["context"] {
    const context: ParsedIntent["context"] = {};

    const fileExtensions = [".psd", ".doc", ".docx", ".xls", ".xlsx", ".pdf", ".png", ".jpg", ".txt"];
    for (const ext of fileExtensions) {
      if (input.includes(ext)) {
        context.file = ext;
        break;
      }
    }

    const appPatterns: [RegExp, string][] = [
      [/photoshop|psd?/i, "photoshop"],
      [/word|docx?/i, "word"],
      [/excel|xlsx?|csv/i, "excel"],
      [/safari|chrome|browser/i, "browser"],
      [/terminal|cmd|shell/i, "terminal"],
      [/finder/i, "finder"],
      [/mail|email/i, "mail"],
      [/messages|imessage/i, "messages"],
      [/spotify|music/i, "music"],
      [/preview/i, "preview"],
    ];

    for (const [pattern, app] of appPatterns) {
      if (pattern.test(input)) {
        context.impliedApp = app;
        break;
      }
    }

    if (input.includes("background")) {
      context.expectedOutcome = "transparent_background";
    } else if (input.includes("debug") || input.includes("fix")) {
      context.expectedOutcome = "working_state";
    }

    if (input.includes("regression") || input.includes("test")) {
      context.constraints = ["no_data_loss", "reversible"];
    }

    return context;
  }

  private calculateConfidence(verb: string, object: string, context: ParsedIntent["context"]): number {
    let confidence = 0.5;

    if (verb && verb !== "unknown") confidence += 0.2;
    if (object && object !== "unknown") confidence += 0.15;
    if (context.impliedApp) confidence += 0.1;
    if (context.expectedOutcome) confidence += 0.05;

    return Math.min(1, confidence);
  }

  private generateReasoning(verb: string, _object: string, context: ParsedIntent["context"]): string {
    const parts: string[] = [];

    parts.push(`Verb "${verb}" identified from input`);

    if (context.impliedApp) {
      parts.push(`Implied app: ${context.impliedApp}`);
    }

    if (context.expectedOutcome) {
      parts.push(`Expected outcome: ${context.expectedOutcome}`);
    }

    return parts.join(". ");
  }

  private inferVerbFromContext(input: string): string {
    if (input.includes("debug") || input.includes("not working") || input.includes("error")) {
      return "debug";
    }
    if (input.includes("test") || input.includes("verify")) {
      return "test";
    }
    if (input.includes("fix") || input.includes("repair")) {
      return "fix";
    }
    if (input.includes("clear") || input.includes("remove background")) {
      return "clear";
    }
    if (input.includes("open") || input.includes("launch")) {
      return "open";
    }
    if (input.includes("save") || input.includes("export")) {
      return "save";
    }

    return "unknown";
  }
}

export class IntentDecomposer {
  private parser: IntentParser;
  private sasRegistry: Map<string, SemanticActionEntry> = new Map();

  constructor() {
    this.parser = new IntentParser();
    this.initializeBuiltInMappings();
  }

  private initializeBuiltInMappings(): void {
    this.registerAction("clear.background", {
      appPatterns: ["photoshop", "gimp", "preview"],
      steps: [
        { action: "tool.select", params: { tool: "magic_eraser" }, verification: { type: "element_focused", label: "Magic Eraser" } },
        { action: "canvas.click", params: { predicate: "background" }, verification: { type: "state_changed", expected: { attribute: "selection" } } },
        { action: "key.press", params: { key: "Delete" }, verification: { type: "state_changed", expected: { attribute: "transparency" } } },
      ],
      successRate: 0.85,
    });

    this.registerAction("debug.document", {
      appPatterns: ["word", "excel", "preview"],
      steps: [
        { action: "app.launch", params: { app: "${app}" } },
        { action: "file.open", params: { path: "${file}" }, verification: { type: "window_opened" } },
        { action: "dialog.respond", params: { button: "OK" }, verification: { type: "text_appeared", expected: { pattern: "error_dialog_visible" } } },
      ],
      successRate: 0.7,
    });

    this.registerAction("open.file", {
      appPatterns: ["*"],
      steps: [
        { action: "file.open", params: { path: "${filepath}" }, verification: { type: "window_opened" } },
      ],
      successRate: 0.95,
    });

    this.registerAction("test.regression", {
      appPatterns: ["browser"],
      steps: [
        { action: "browser.navigate", params: { url: "${url}" } },
        { action: "auth.login", params: { credentials: "${creds}" } },
        { action: "test.action", params: { steps: "${testSteps}" } },
        { action: "test.verify", params: { expected: "${expected}" } },
      ],
      successRate: 0.8,
    });
  }

  registerAction(actionType: string, entry: SemanticActionEntry): void {
    this.sasRegistry.set(actionType, entry);
  }

  decompose(
    input: string,
    context: {
      screenGraph?: ScreenGraph;
      currentApp?: AppInfo;
      availableElements?: UIElement[];
    }
  ): ExecutionPlan {
    const parsedIntent = this.parser.parse(input);

    const actionType = this.matchActionType(parsedIntent);

    if (!actionType) {
      return this.createFallbackPlan(input, parsedIntent);
    }

    const entry = this.sasRegistry.get(actionType)!;

    return this.buildPlanFromEntry(actionType, entry, parsedIntent, context);
  }

  private matchActionType(parsed: ParsedIntent): string | null {
    const objectKeywords = parsed.object.toLowerCase().split(/\s+/);

    for (const [actionType, entry] of this.sasRegistry) {
      if (this.matchesActionType(objectKeywords, entry, parsed)) {
        return actionType;
      }
    }

    return null;
  }

  private matchesActionType(keywords: string[], entry: SemanticActionEntry, _parsed: ParsedIntent): boolean {
    const allKeywords = [
      ...keywords,
    ];

    for (const keyword of allKeywords) {
      if (entry.keywords?.some((k) => keyword.includes(k))) {
        return true;
      }
    }

    return false;
  }

  private buildPlanFromEntry(
    _actionType: string,
    entry: SemanticActionEntry,
    parsed: ParsedIntent,
    context: { currentApp?: AppInfo; screenGraph?: ScreenGraph }
  ): ExecutionPlan {
    const steps: DecomposedStep[] = [];
    let stepIndex = 0;

    for (const stepDef of entry.steps) {
      const stepId = `step_${stepIndex++}`;

      const resolvedParams = this.resolveParams(stepDef.params, parsed, context);

      const dependsOn = stepIndex > 0 ? [`step_${stepIndex - 2}`] : [];

      const verification = stepDef.verification ? {
        type: stepDef.verification.type as VerificationSpec["type"],
        target: stepDef.verification.target,
        expected: stepDef.verification.expected,
        timeout: stepDef.verification.timeout || 2000,
      } : undefined;

      steps.push({
        id: stepId,
        action: stepDef.action,
        params: resolvedParams,
        dependsOn,
        verification,
        alternatives: stepDef.alternatives,
        estimatedSuccess: entry.successRate || 0.8,
      });
    }

    return {
      id: `plan_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      steps,
      currentStep: 0,
      status: "planned",
    };
  }

  private resolveParams(
    params: Record<string, unknown>,
    parsed: ParsedIntent,
    context: { currentApp?: AppInfo; screenGraph?: ScreenGraph }
  ): Record<string, unknown> {
    const resolved: Record<string, unknown> = {};

    for (const [key, value] of Object.entries(params)) {
      if (typeof value === "string" && value.startsWith("${") && value.endsWith("}")) {
        const varName = value.slice(2, -1);

        switch (varName) {
          case "app":
            resolved[key] = parsed.context.impliedApp || context.currentApp?.name || "unknown";
            break;
          case "file":
            resolved[key] = parsed.context.file || "unknown";
            break;
          case "filepath":
            resolved[key] = parsed.object;
            break;
          default:
            resolved[key] = value;
        }
      } else {
        resolved[key] = value;
      }
    }

    return resolved;
  }

  private createFallbackPlan(input: string, parsed: ParsedIntent): ExecutionPlan {
    const stepId = `step_0`;

    return {
      id: `plan_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      steps: [
        {
          id: stepId,
          action: "user.confirm",
          params: {
            message: `I need clarification: "${input}"`,
            parsedIntent: parsed,
          },
          dependsOn: [],
          estimatedSuccess: 1,
        },
      ],
      currentStep: 0,
      status: "planned",
    };
  }
}

interface SemanticActionEntry {
  appPatterns: string[];
  keywords?: string[];
  steps: {
    action: string;
    params: Record<string, unknown>;
    verification?: {
      type: string;
      label?: string;
      target?: CanonicalTarget;
      expected?: Record<string, unknown>;
      timeout?: number;
    };
    alternatives?: string[];
  }[];
  successRate?: number;
}

export const intentDecomposer = new IntentDecomposer();
