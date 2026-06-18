import type { Skill, SkillStep, CanonicalAction, UIElement } from "../memory/types.js";

export interface RecordingStep {
  action: CanonicalAction;
  target?: UIElement;
  verificationPassed?: boolean;
  duration?: number;
  error?: string;
}

export interface Recording {
  id: string;
  name: string;
  app: string;
  steps: RecordingStep[];
  createdAt: number;
}

export interface GeneralizationResult {
  skill: Omit<Skill, "id" | "createdAt" | "updatedAt" | "usageCount">;
  warnings: string[];
}

export class SkillComposer {
  private activeRecordings: Map<string, Recording> = new Map();

  startRecording(app: { bundleId: string; name: string }, name?: string): string {
    const id = `rec_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const recording: Recording = {
      id,
      name: name || `Recording ${new Date().toLocaleString()}`,
      app: app.bundleId,
      steps: [],
      createdAt: Date.now(),
    };
    this.activeRecordings.set(id, recording);
    return id;
  }

  recordStep(recordingId: string, step: RecordingStep): void {
    const recording = this.activeRecordings.get(recordingId);
    if (!recording) {
      console.warn(`[SkillComposer] Recording ${recordingId} not found`);
      return;
    }
    recording.steps.push(step);
  }

  async finishRecording(recordingId: string): Promise<Recording | null> {
    const recording = this.activeRecordings.get(recordingId);
    if (!recording) return null;

    this.activeRecordings.delete(recordingId);
    return recording;
  }

  cancelRecording(recordingId: string): void {
    this.activeRecordings.delete(recordingId);
  }

  generalize(recording: Recording, name?: string): GeneralizationResult {
    const warnings: string[] = [];

    if (recording.steps.length === 0) {
      warnings.push("Recording has no steps");
    }

    const generalizedSteps = this.generalizeSteps(recording.steps);
    const parameters = this.extractParameters(generalizedSteps);

    return {
      skill: {
        name: name || recording.name,
        description: `Generated from recording in ${recording.app}`,
        parameters,
        steps: generalizedSteps,
        prerequisites: [],
        successConditions: [],
        sourceApp: recording.app,
        targetApps: [recording.app],
        successRate: this.calculateSuccessRate(recording.steps),
      },
      warnings,
    };
  }

  private generalizeSteps(recordingSteps: RecordingStep[]): SkillStep[] {
    const generalized: SkillStep[] = [];

    for (let i = 0; i < recordingSteps.length; i++) {
      const step = recordingSteps[i];
      const generalizedStep = this.generalizeStep(step, i);
      generalized.push(generalizedStep);
    }

    return generalized;
  }

  private generalizeStep(recordingStep: RecordingStep, index: number): SkillStep {
    const action = recordingStep.action;
    const target = recordingStep.target;

    let targetSpec: SkillStep["target"];

    if (!target) {
      targetSpec = { type: "exact" };
    } else if (this.isParameterized(target, action)) {
      const paramName = this.suggestParamName(target, action);
      targetSpec = { type: "parameter", paramName };
    } else if (this.isFlexibleMatch(target)) {
      targetSpec = {
        type: "flexible",
        role: target.type,
        labelPattern: target.semantic.label,
      };
    } else {
      targetSpec = {
        type: "exact",
        role: target.type,
        label: target.semantic.label,
        bounds: target.bounds,
      };
    }

    return {
      id: `step_${index}`,
      action: action.type,
      target: targetSpec,
      parameters: this.extractStepParameters(action.params),
      verification: recordingStep.verificationPassed
        ? { type: "screenshot_match" }
        : undefined,
    };
  }

  private isParameterized(target: UIElement, action: CanonicalAction): boolean {
    if (action.params?.filePath) return true;
    if (action.params?.path) return true;
    if (action.params?.url) return true;

    if (target.visual.textOcr) {
      const text = target.visual.textOcr;
      if (text.includes("/") || text.includes("\\") || text.includes(".")) {
        return true;
      }
    }

    return false;
  }

  private isFlexibleMatch(target: UIElement): boolean {
    if (!target.semantic.label) return false;

    const label = target.semantic.label.toLowerCase();

    const menuIndicators = ["menu", "item", "button", "toggle"];
    const isMenuLike = menuIndicators.some((ind) => label.includes(ind));

    return isMenuLike && target.affordances.clickable;
  }

  private suggestParamName(target: UIElement, action: CanonicalAction): string {
    if (action.params?.filePath) return "filepath";
    if (action.params?.path) return "path";
    if (action.params?.url) return "url";

    const label = (target.semantic.label || target.visual.textOcr || "").toLowerCase();

    if (label.includes("file")) return "filename";
    if (label.includes("folder") || label.includes("directory")) return "folder";
    if (label.includes("image") || label.includes("photo")) return "imagePath";
    if (label.includes("text")) return "text";
    if (label.includes("name")) return "name";

    return "value";
  }

  private extractStepParameters(params: Record<string, unknown>): Record<string, unknown> {
    const extracted: Record<string, unknown> = {};

    for (const [key, value] of Object.entries(params)) {
      if (typeof value === "string" && (value.includes("/") || value.includes("\\"))) {
        continue;
      }
      if (typeof value === "string" && value.match(/^https?:\/\//)) {
        continue;
      }
      extracted[key] = value;
    }

    return extracted;
  }

  private extractParameters(steps: SkillStep[]): Skill["parameters"] {
    const params: Skill["parameters"] = [];
    const seen = new Set<string>();

    for (const step of steps) {
      if (step.target?.type === "parameter" && step.target.paramName) {
        const name = step.target.paramName;
        if (!seen.has(name)) {
          seen.add(name);
          params.push({
            name,
            type: this.inferParameterType(name),
            required: true,
          });
        }
      }
    }

    return params;
  }

  private inferParameterType(name: string): Skill["parameters"][0]["type"] {
    const lower = name.toLowerCase();
    if (lower.includes("path") || lower.includes("file") || lower.includes("folder")) {
      return "path";
    }
    if (lower.includes("url") || lower.includes("link")) return "string";
    if (lower.includes("count") || lower.includes("num")) return "number";
    if (lower.includes("enable") || lower.includes("toggle")) return "boolean";
    return "string";
  }

  private calculateSuccessRate(steps: RecordingStep[]): number {
    if (steps.length === 0) return 0;

    const passed = steps.filter((s) => s.verificationPassed !== false).length;
    return passed / steps.length;
  }

  getActiveRecordings(): Recording[] {
    return Array.from(this.activeRecordings.values());
  }
}

export const skillComposer = new SkillComposer();
