import type { Skill, SkillStep, CanonicalAction, UIElement } from "../memory/types.js";

export interface ReplayContext {
  app: string;
  elements: UIElement[];
}

export interface ReplayResult {
  success: boolean;
  stepsExecuted: number;
  totalSteps: number;
  errors: string[];
  failedStep?: number;
}

export interface ParameterBinding {
  [paramName: string]: unknown;
}

export class SkillReplayer {
  async replay(
    skill: Skill,
    context: ReplayContext,
    params: ParameterBinding = {}
  ): Promise<ReplayResult> {
    const errors: string[] = [];

    if (!this.validatePrerequisites(skill, context)) {
      return {
        success: false,
        stepsExecuted: 0,
        totalSteps: skill.steps.length,
        errors: ["Prerequisites not met"],
      };
    }

    for (let i = 0; i < skill.steps.length; i++) {
      const step = skill.steps[i];

      try {
        const boundStep = this.bindParameters(step, params);
        const resolved = await this.resolveTarget(boundStep, context);

        const action: CanonicalAction = {
          type: boundStep.action,
          target: resolved,
          params: boundStep.parameters || {},
          timestamp: Date.now(),
          traceId: `skill_${skill.id}_${i}`,
        };

        const result = await this.executeAction(action);

        if (!result.success) {
          errors.push(`Step ${i} failed: ${result.error}`);
          return {
            success: false,
            stepsExecuted: i,
            totalSteps: skill.steps.length,
            errors,
            failedStep: i,
          };
        }

        if (boundStep.verification) {
          const verified = await this.verifyStep(boundStep.verification, context);
          if (!verified) {
            errors.push(`Step ${i} verification failed`);
            return {
              success: false,
              stepsExecuted: i + 1,
              totalSteps: skill.steps.length,
              errors,
              failedStep: i,
            };
          }
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        errors.push(`Step ${i} error: ${message}`);
        return {
          success: false,
          stepsExecuted: i,
          totalSteps: skill.steps.length,
          errors,
          failedStep: i,
        };
      }
    }

    return {
      success: true,
      stepsExecuted: skill.steps.length,
      totalSteps: skill.steps.length,
      errors: [],
    };
  }

  private validatePrerequisites(skill: Skill, _context: ReplayContext): boolean {
    if (!skill.prerequisites || skill.prerequisites.length === 0) {
      return true;
    }

    return true;
  }

  private bindParameters(step: SkillStep, params: ParameterBinding): SkillStep {
    if (step.target?.type !== "parameter") {
      return step;
    }

    const paramName = step.target.paramName;
    const paramValue = params[paramName || ""];

    if (paramValue === undefined) {
      return step;
    }

    return {
      ...step,
      target: {
        type: "exact",
      },
      parameters: {
        ...step.parameters,
        [paramName || ""]: paramValue,
      },
    };
  }

  private async resolveTarget(
    step: SkillStep,
    context: ReplayContext
  ): Promise<CanonicalAction["target"]> {
    if (!step.target) {
      return {};
    }

    switch (step.target.type) {
      case "exact":
        return {
          accessibilityPath: step.target.label,
          semanticSelector: step.target.label
            ? { label: step.target.label }
            : undefined,
          visualRegion: step.target.bounds,
        };

      case "flexible": {
        const match = this.findFlexibleMatch(step.target, context.elements);
        if (match) {
          return {
            accessibilityPath: match.id,
            semanticSelector: {
              role: step.target.role,
              label: step.target.labelPattern,
            },
          };
        }
        throw new Error(`Could not find element matching ${step.target.role}: ${step.target.labelPattern}`);
      }

      case "parameter":
        return {
          semanticSelector: {
            label: String(step.parameters?.[step.target.paramName || ""] || ""),
          },
        };

      default:
        return {};
    }
  }

  private findFlexibleMatch(target: SkillStep["target"], elements: UIElement[]): UIElement | null {
    if (target?.type !== "flexible") return null;

    for (const elem of elements) {
      if (target.role && elem.type !== target.role) continue;

      if (target.labelPattern) {
        const label = elem.semantic.label || elem.visual.textOcr || "";
        if (label.toLowerCase().includes(target.labelPattern.toLowerCase())) {
          return elem;
        }
      }
    }

    return null;
  }

  private async executeAction(_action: CanonicalAction): Promise<{ success: boolean; error?: string }> {
    return { success: true };
  }

  private async verifyStep(
    verification: SkillStep["verification"],
    _context: ReplayContext
  ): Promise<boolean> {
    if (!verification) return true;

    switch (verification.type) {
      case "screenshot_match":
        return true;

      case "element_exists":
        return true;

      case "state_changed":
        return true;

      default:
        return true;
    }
  }

  async estimateDuration(skill: Skill): Promise<number> {
    const BASE_STEP_TIME = 1000;
    return skill.steps.length * BASE_STEP_TIME;
  }

  async getRequiredParams(skill: Skill): Promise<string[]> {
    return skill.parameters.filter((p) => p.required).map((p) => p.name);
  }

  async validateParams(skill: Skill, params: ParameterBinding): Promise<{ valid: boolean; missing: string[] }> {
    const missing: string[] = [];

    for (const param of skill.parameters) {
      if (param.required && params[param.name] === undefined) {
        missing.push(param.name);
      }
    }

    return {
      valid: missing.length === 0,
      missing,
    };
  }
}

export const skillReplayer = new SkillReplayer();
