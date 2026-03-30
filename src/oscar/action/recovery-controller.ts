import type { CanonicalAction, CanonicalTarget, ScreenGraph, AppInfo } from "../types.js";
import type { DecomposedStep, ExecutionPlan } from "../intent/decomposer.js";

export type FailureType =
  | "element_not_found"
  | "action_failed"
  | "unexpected_dialog"
  | "timeout"
  | "app_crashed"
  | "permission_denied"
  | "unknown_error"
  | "verification_failed";

export interface FailureContext {
  type: FailureType;
  step: DecomposedStep;
  action: CanonicalAction;
  error?: string;
  alternatives?: string[];
  dialog?: {
    title?: string;
    message?: string;
    buttons: string[];
  };
  screenGraph?: ScreenGraph;
  attempts?: number;
}

export interface RecoveryResult {
  success: boolean;
  strategy: string;
  replacementAction?: CanonicalAction;
  dialogResponse?: string;
  restarted?: boolean;
  requiresUser?: boolean;
  message?: string;
  options?: string[];
  shouldRetry?: boolean;
}

export interface RecoveryStrategy {
  name: string;
  description: string;
  applicability: (failure: FailureContext) => number;
  execute: (failure: FailureContext, state: RecoveryState) => Promise<RecoveryResult>;
}

export interface RecoveryState {
  plan: ExecutionPlan;
  currentStepIndex: number;
  screenGraph?: ScreenGraph;
  activeApps: AppInfo[];
  checkpoint?: Checkpoint;
  stepHistory: StepAttempt[];
  recoveryCount: number;
}

export interface StepAttempt {
  stepId: string;
  action: CanonicalAction;
  result: "success" | "failed" | "recovered";
  error?: string;
  attempts: number;
  timestamp: number;
}

export interface Checkpoint {
  id: string;
  planId: string;
  stepIndex: number;
  timestamp: number;
  screenState: unknown;
  appStates: Map<string, AppState>;
}

export interface AppState {
  bundleId: string;
  openDocuments: string[];
  modified: boolean;
  cursorPosition: { x: number; y: number };
  panelStates: Record<string, boolean>;
}

export class RecoveryController {
  private strategies: RecoveryStrategy[] = [];
  private maxRecoveryAttempts = 3;

  constructor() {
    this.initializeStrategies();
  }

  private initializeStrategies(): void {
    this.strategies = [
      this.createRelocateRetryStrategy(),
      this.createAlternativePathStrategy(),
      this.createDialogHandlerStrategy(),
      this.createCheckpoinRestoreStrategy(),
      this.createEscalateUserStrategy(),
    ];
  }

  private createRelocateRetryStrategy(): RecoveryStrategy {
    return {
      name: "relocate_retry",
      description: "Re-locate target element and retry",
      applicability: (failure) => {
        if (failure.type === "element_not_found") return 0.9;
        if (failure.type === "verification_failed" && failure.screenGraph) return 0.7;
        return 0;
      },
      execute: async (failure, _state) => {
        const newTarget = await this.findSimilarElement(
          failure.action.target,
          failure.screenGraph
        );

        if (newTarget) {
          return {
            success: true,
            strategy: "relocate_retry",
            replacementAction: {
              ...failure.action,
              target: newTarget,
            },
            shouldRetry: true,
            message: `Re-located target element and retrying`,
          };
        }

        return {
          success: false,
          strategy: "relocate_retry",
          message: "Could not find similar element",
        };
      },
    };
  }

  private createAlternativePathStrategy(): RecoveryStrategy {
    return {
      name: "alternative_path",
      description: "Try alternative action path",
      applicability: (failure) => {
        if (failure.type === "action_failed" && failure.step.alternatives?.length) {
          return 0.8;
        }
        if (failure.type === "verification_failed") return 0.6;
        return 0;
      },
      execute: async (failure, _state) => {
        const alternatives = failure.step.alternatives || [];

        if (alternatives.length === 0) {
          return {
            success: false,
            strategy: "alternative_path",
            message: "No alternative actions available",
          };
        }

        const nextAlt = alternatives[0];

        return {
          success: true,
          strategy: "alternative_path",
          replacementAction: {
            ...failure.action,
            type: nextAlt as CanonicalAction["type"],
          },
          shouldRetry: true,
          message: `Trying alternative action: ${nextAlt}`,
        };
      },
    };
  }

  private createDialogHandlerStrategy(): RecoveryStrategy {
    return {
      name: "dialog_handler",
      description: "Handle unexpected dialogs",
      applicability: (failure) => {
        if (failure.type === "unexpected_dialog") return 0.95;
        if (failure.dialog) return 0.9;
        return 0;
      },
      execute: async (failure, _state) => {
        const dialog = failure.dialog;
        if (!dialog) {
          return {
            success: false,
            strategy: "dialog_handler",
            message: "No dialog information available",
          };
        }

        const response = this.decideDialogResponse(dialog);

        return {
          success: true,
          strategy: "dialog_handler",
          dialogResponse: response,
          message: `Responding to dialog with: ${response}`,
        };
      },
    };
  }

  private createCheckpoinRestoreStrategy(): RecoveryStrategy {
    return {
      name: "checkpoint_restore",
      description: "Restore from checkpoint and retry",
      applicability: (failure) => {
        if (failure.type === "app_crashed") return 0.9;
        if (failure.type === "unknown_error" && failure.attempts && failure.attempts >= 2) {
          return 0.7;
        }
        return 0;
      },
      execute: async (_failure, state) => {
        if (!state.checkpoint) {
          return {
            success: false,
            strategy: "checkpoint_restore",
            message: "No checkpoint available to restore",
          };
        }

        return {
          success: true,
          strategy: "checkpoint_restore",
          restarted: true,
          message: "Restoring from checkpoint and retrying",
        };
      },
    };
  }

  private createEscalateUserStrategy(): RecoveryStrategy {
    return {
      name: "user_escalation",
      description: "Ask user for help",
      applicability: (failure) => {
        if (failure.type === "permission_denied") return 1.0;
        if (failure.type === "unknown_error") return 0.8;
        if (failure.attempts && failure.attempts >= this.maxRecoveryAttempts) return 1.0;
        return 0;
      },
      execute: async (failure, _state) => {
        const options = this.suggestUserOptions(failure);

        return {
          success: false,
          strategy: "user_escalation",
          requiresUser: true,
          message: this.formatFailureMessage(failure),
          options,
        };
      },
    };
  }

  async handleFailure(
    failure: FailureContext,
    state: RecoveryState
  ): Promise<RecoveryResult> {
    const scored = this.strategies.map((s) => ({
      strategy: s,
      score: s.applicability(failure),
    }));

    scored.sort((a, b) => b.score - a.score);

    const best = scored[0];

    if (best.score < 0.3) {
      return {
        success: false,
        strategy: "none",
        message: "No applicable recovery strategy",
      };
    }

    console.log(`[Recovery] Selected strategy: ${best.strategy.name} (score: ${best.score.toFixed(2)})`);

    const result = await best.strategy.execute(failure, state);

    return {
      ...result,
      strategy: best.strategy.name,
    };
  }

  private async findSimilarElement(
    target: CanonicalTarget | undefined,
    screenGraph?: ScreenGraph
  ): Promise<CanonicalTarget | null> {
    if (!screenGraph || !target) return null;

    if (target.semanticSelector?.label) {
      const elements = Array.from(screenGraph.elements.values());

      for (const elem of elements) {
        if (elem.semantic.label?.toLowerCase().includes(target.semanticSelector!.label!.toLowerCase())) {
          return {
            accessibilityPath: elem.id,
            semanticSelector: target.semanticSelector,
          };
        }
      }
    }

    return null;
  }

  private decideDialogResponse(dialog: FailureContext["dialog"]): string {
    if (!dialog) return "cancel";

    const message = dialog.message?.toLowerCase() || "";
    const title = dialog.title?.toLowerCase() || "";

    if (message.includes("save") || title.includes("save")) {
      if (dialog.buttons.some((b) => b.toLowerCase().includes("don't save"))) {
        return "don't save";
      }
    }

    if (message.includes("delete") || message.includes("remove")) {
      if (dialog.buttons.some((b) => b.toLowerCase().includes("cancel"))) {
        return "cancel";
      }
    }

    if (message.includes("error") || message.includes("warning")) {
      if (dialog.buttons.some((b) => b.toLowerCase().includes("ok"))) {
        return "ok";
      }
    }

    return dialog.buttons[0] || "cancel";
  }

  private suggestUserOptions(failure: FailureContext): string[] {
    const options: string[] = [];

    options.push("Try a different approach");
    options.push("Do it manually for this step");
    options.push("Cancel the entire task");

    if (failure.type === "permission_denied") {
      options.push("Grant permission and retry");
    }

    if (failure.type === "element_not_found") {
      options.push("Show me what's on screen");
    }

    return options;
  }

  private formatFailureMessage(failure: FailureContext): string {
    const stepName = failure.step.action;
    const errorType = failure.type;

    switch (errorType) {
      case "element_not_found":
        return `I couldn't find the element for "${stepName}". Would you like me to try a different approach?`;
      case "action_failed":
        return `The action "${stepName}" failed. ${failure.error || "Please help me proceed."}`;
      case "unexpected_dialog":
        return `A dialog appeared that I wasn't expecting. ${failure.dialog?.message || ""}`;
      case "timeout":
        return `The action "${stepName}" timed out. Should I retry or try a different approach?`;
      case "app_crashed":
        return `The application crashed during "${stepName}". Should I restart and try again?`;
      case "permission_denied":
        return `Permission was denied for "${stepName}". Please grant permission and I'll retry.`;
      default:
        return `Something went wrong with "${stepName}". ${failure.error || "Please help me proceed."}`;
    }
  }

  registerStrategy(strategy: RecoveryStrategy): void {
    this.strategies.push(strategy);
    this.strategies.sort((a, b) => {
      const aScore = a.applicability({ type: "unknown_error" } as FailureContext);
      const bScore = b.applicability({ type: "unknown_error" } as FailureContext);
      return bScore - aScore;
    });
  }

  getStrategies(): RecoveryStrategy[] {
    return [...this.strategies];
  }
}

export const recoveryController = new RecoveryController();
