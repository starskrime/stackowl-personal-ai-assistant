import type {
  AccessibilityState,
  AccessibilityDelta,
  VerificationCondition,
  VerificationResult,
  CanonicalAction,
} from "../types.js";
import { macOSAdapter } from "../platform/adapters/macos.js";

interface WindowInfo {
  id: string;
  title: string;
}

interface ElementInfo {
  id: string;
  label?: string;
  role?: string;
  state?: Record<string, boolean>;
  value?: string;
}

export class VerificationMicroEngine {
  private adapter = macOSAdapter;
  private pollInterval = 10;
  private defaultTimeout = 2000;

  async verify(
    action: CanonicalAction,
    conditions: VerificationCondition[]
  ): Promise<VerificationResult> {
    const timeout = this.getTimeout(conditions);
    const startTime = Date.now();

    let beforeState: AccessibilityState | null = null;
    let attempts = 0;

    while (Date.now() - startTime < timeout) {
      attempts++;

      if (!beforeState) {
        beforeState = await this.adapter.getAccessibilityTree();
      }

      const result = await this.adapter.executeAction(action);

      if (!result.success) {
        return {
          success: false,
          attempts,
          error: result.error,
        };
      }

      await this.delay(this.pollInterval);

      const verifyResult = await this.checkConditions(conditions);

      if (verifyResult.success) {
        return {
          success: true,
          attempts,
        };
      }

      const afterState = await this.adapter.getAccessibilityTree();
      const delta = this.computeDelta(beforeState, afterState);

      if (this.isTerminalFailure(delta, conditions)) {
        return {
          success: false,
          attempts,
          error: "Terminal failure detected",
          delta: delta[0],
        };
      }

      beforeState = afterState;
    }

    return {
      success: false,
      attempts,
      error: "Verification timeout",
    };
  }

  async verifyStateChange(
    beforeState: AccessibilityState,
    condition: VerificationCondition
  ): Promise<VerificationResult> {
    const timeout = condition.timeout || this.defaultTimeout;
    const startTime = Date.now();
    let attempts = 0;

    while (Date.now() - startTime < timeout) {
      attempts++;
      const currentState = await this.adapter.getAccessibilityTree();
      const delta = this.computeDelta(beforeState, currentState);

      if (this.matchesCondition(delta, condition, currentState)) {
        return { success: true, attempts };
      }

      await this.delay(this.pollInterval);
    }

    return {
      success: false,
      attempts,
      error: "State change not detected within timeout",
    };
  }

  computeDelta(before: AccessibilityState, after: AccessibilityState): AccessibilityDelta[] {
    const deltas: AccessibilityDelta[] = [];

    if (before.windows.length !== after.windows.length) {
      const added = after.windows.filter(
        (w: WindowInfo) => !before.windows.some((bw: WindowInfo) => bw.id === w.id)
      );
      const removed = before.windows.filter(
        (w: WindowInfo) => !after.windows.some((aw: WindowInfo) => aw.id === w.id)
      );

      for (const w of added) {
        deltas.push({ type: "window_opened", elementLabel: w.title });
      }
      for (const w of removed) {
        deltas.push({ type: "window_closed", elementLabel: w.title });
      }
    }

    const beforeIds = new Set(Array.from(before.elements.keys()));
    const afterIds = new Set(Array.from(after.elements.keys()));

    for (const id of afterIds) {
      if (!beforeIds.has(id)) {
        const elem = after.elements.get(id) as ElementInfo | undefined;
        deltas.push({
          type: "element_added",
          elementId: id,
          elementLabel: elem?.label,
        });
      }
    }

    for (const id of beforeIds) {
      if (!afterIds.has(id)) {
        const elem = before.elements.get(id) as ElementInfo | undefined;
        deltas.push({
          type: "element_removed",
          elementId: id,
          elementLabel: elem?.label,
        });
      }
    }

    for (const id of afterIds) {
      if (beforeIds.has(id)) {
        const beforeElem = before.elements.get(id) as ElementInfo;
        const afterElem = after.elements.get(id) as ElementInfo;

        const delta = this.detectElementChanges(beforeElem, afterElem, id);
        if (delta) {
          deltas.push(delta);
        }
      }
    }

    if (before.focusedElement !== after.focusedElement) {
      const elem = after.elements.get(after.focusedElement || "") as ElementInfo | undefined;
      deltas.push({
        type: "element_focused",
        elementId: after.focusedElement,
        elementLabel: elem?.label,
      });
    }

    return deltas;
  }

  private detectElementChanges(
    before: ElementInfo,
    after: ElementInfo,
    id: string
  ): AccessibilityDelta | null {
    if (before.state && after.state) {
      for (const [key, val] of Object.entries(after.state)) {
        if (before.state[key] !== val) {
          return {
            type: "attribute_changed",
            elementId: id,
            attributeName: key,
            oldValue: before.state[key],
            newValue: val,
          };
        }
      }
    }

    if (before.value !== after.value) {
      return {
        type: "attribute_changed",
        elementId: id,
        attributeName: "value",
        oldValue: before.value,
        newValue: after.value,
      };
    }

    if (before.label !== after.label) {
      return {
        type: "attribute_changed",
        elementId: id,
        attributeName: "label",
        oldValue: before.label,
        newValue: after.label,
      };
    }

    return null;
  }

  private matchesCondition(
    delta: AccessibilityDelta[],
    condition: VerificationCondition,
    currentState: AccessibilityState
  ): boolean {
    switch (condition.type) {
      case "element_exists":
        if (condition.target?.semanticSelector) {
          return this.elementMatchesSelector(currentState, condition.target.semanticSelector);
        }
        return delta.some((d) => d.type === "element_added");

      case "element_focused":
        return delta.some((d) => d.type === "element_focused");

      case "window_opened":
        if (condition.expected?.title) {
          return delta.some(
            (d) =>
              d.type === "window_opened" &&
              d.elementLabel?.includes(condition.expected?.title as string)
          );
        }
        return delta.some((d) => d.type === "window_opened");

      case "window_closed":
        return delta.some((d) => d.type === "window_closed");

      case "text_appeared":
        if (condition.expected?.text) {
          const elements = Array.from(currentState.elements.values()) as ElementInfo[];
          return elements.some(
            (elem) =>
              elem.label?.includes(condition.expected?.text as string) ||
              elem.value?.includes(condition.expected?.text as string)
          );
        }
        return false;

      case "state_changed":
        if (condition.expected) {
          return delta.some((d) => {
            if (d.type !== "attribute_changed") return false;
            for (const [key, val] of Object.entries(condition.expected || {})) {
              if (d.attributeName === key && d.newValue === val) {
                return true;
              }
            }
            return false;
          });
        }
        return delta.some((d) => d.type === "attribute_changed");

      default:
        return false;
    }
  }

  private elementMatchesSelector(
    state: AccessibilityState,
    selector: { role?: string; label?: string; index?: number }
  ): boolean {
    const matches: string[] = [];

    for (const [id, elem] of state.elements) {
      const e = elem as ElementInfo;
      if (selector.role && e.role !== selector.role) continue;
      if (selector.label && !e.label?.includes(selector.label)) continue;
      matches.push(id);
    }

    if (selector.index !== undefined) {
      return matches.length > selector.index;
    }

    return matches.length > 0;
  }

  private isTerminalFailure(
    delta: AccessibilityDelta[],
    conditions: VerificationCondition[]
  ): boolean {
    for (const d of delta) {
      if (
        d.type === "element_removed" &&
        conditions.some((c) => c.type === "element_exists")
      ) {
        const targetRemoved = conditions.some((c) =>
          c.target?.semanticSelector?.label &&
          d.elementLabel?.includes(c.target.semanticSelector.label)
        );

        if (targetRemoved) {
          return true;
        }
      }
    }
    return false;
  }

  private async checkConditions(conditions: VerificationCondition[]): Promise<{ success: boolean }> {
    for (const condition of conditions) {
      const result = await this.adapter.verify([condition]);
      if (result[0]?.success) {
        return { success: true };
      }
    }
    return { success: false };
  }

  private getTimeout(conditions: VerificationCondition[]): number {
    const timeouts = conditions
      .map((c) => c.timeout || this.defaultTimeout)
      .filter(Boolean);
    return Math.max(...timeouts, this.defaultTimeout);
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async verifyWithRetry(
    action: CanonicalAction,
    condition: VerificationCondition,
    maxRetries = 3
  ): Promise<VerificationResult> {
    let lastResult: VerificationResult = { success: false, error: "No attempts" };

    for (let i = 0; i < maxRetries; i++) {
      lastResult = await this.verify(action, [condition]);

      if (lastResult.success) {
        return lastResult;
      }

      await this.delay(100 * (i + 1));
    }

    return lastResult;
  }

  createVerificationCondition(
    type: VerificationCondition["type"],
    params?: {
      target?: { semanticSelector?: { role?: string; label?: string; index?: number } };
      expected?: Record<string, unknown>;
      timeout?: number;
    }
  ): VerificationCondition {
    return {
      type,
      target: params?.target,
      expected: params?.expected,
      timeout: params?.timeout,
    };
  }
}

export const verificationEngine = new VerificationMicroEngine();
