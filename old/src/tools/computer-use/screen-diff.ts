/**
 * StackOwl — Screen State Diffing
 *
 * Compares two ScreenState snapshots to detect what changed after an action.
 * Used by the Action Planner to verify that actions had the intended effect.
 *
 * No AI model needed — pure structural comparison of AX tree elements.
 */

import type { ScreenState, ScreenElement } from "./screen-reader.js";

// ─── Types ───────────────────────────────────────────────────────────────────

export interface ScreenDiff {
  /** Whether any meaningful change was detected */
  hasChanges: boolean;
  /** Human-readable summary of changes */
  summary: string;
  /** App changed (e.g., switched from Finder to Safari) */
  appChanged: boolean;
  /** Window title changed */
  titleChanged: boolean;
  /** New interactive elements appeared */
  newElements: string[];
  /** Interactive elements that disappeared */
  removedElements: string[];
  /** Elements whose values changed (text fields, etc.) */
  changedValues: { label: string; oldValue: string; newValue: string }[];
  /** Net change in interactive element count */
  interactiveCountDelta: number;
}

// ─── Diff Logic ──────────────────────────────────────────────────────────────

/**
 * Compare two screen states and produce a structured diff.
 * Designed to be fast and token-efficient — the summary is what gets
 * injected into the model's context.
 */
export function diffScreenStates(
  before: ScreenState,
  after: ScreenState,
): ScreenDiff {
  const appChanged = before.app !== after.app;
  const titleChanged = before.windowTitle !== after.windowTitle;

  // Flatten interactive elements from both states
  const beforeMap = flattenInteractive(before.elements);
  const afterMap = flattenInteractive(after.elements);

  // Find new, removed, and changed elements
  const newElements: string[] = [];
  const removedElements: string[] = [];
  const changedValues: { label: string; oldValue: string; newValue: string }[] =
    [];

  // Check what's new or changed
  for (const [key, afterEl] of afterMap) {
    const beforeEl = beforeMap.get(key);
    if (!beforeEl) {
      newElements.push(afterEl.label || afterEl.role);
    } else if (
      afterEl.value !== beforeEl.value &&
      (afterEl.value || beforeEl.value)
    ) {
      changedValues.push({
        label: afterEl.label || afterEl.role,
        oldValue: beforeEl.value || "",
        newValue: afterEl.value || "",
      });
    }
  }

  // Check what's gone
  for (const [key] of beforeMap) {
    if (!afterMap.has(key)) {
      const el = beforeMap.get(key)!;
      removedElements.push(el.label || el.role);
    }
  }

  const interactiveCountDelta =
    after.interactiveCount - before.interactiveCount;

  const hasChanges =
    appChanged ||
    titleChanged ||
    newElements.length > 0 ||
    removedElements.length > 0 ||
    changedValues.length > 0;

  // Build summary
  const parts: string[] = [];
  if (appChanged) parts.push(`App: ${before.app} → ${after.app}`);
  if (titleChanged)
    parts.push(`Window: "${before.windowTitle}" → "${after.windowTitle}"`);
  if (newElements.length > 0)
    parts.push(
      `New: ${newElements.slice(0, 5).join(", ")}${newElements.length > 5 ? ` (+${newElements.length - 5} more)` : ""}`,
    );
  if (removedElements.length > 0)
    parts.push(
      `Gone: ${removedElements.slice(0, 5).join(", ")}${removedElements.length > 5 ? ` (+${removedElements.length - 5} more)` : ""}`,
    );
  if (changedValues.length > 0) {
    for (const cv of changedValues.slice(0, 3)) {
      parts.push(
        `Changed: ${cv.label} "${truncate(cv.oldValue, 30)}" → "${truncate(cv.newValue, 30)}"`,
      );
    }
  }
  if (interactiveCountDelta !== 0) {
    parts.push(
      `Interactive elements: ${interactiveCountDelta > 0 ? "+" : ""}${interactiveCountDelta}`,
    );
  }

  return {
    hasChanges,
    summary: parts.length > 0 ? parts.join("\n") : "No visible changes",
    appChanged,
    titleChanged,
    newElements,
    removedElements,
    changedValues,
    interactiveCountDelta,
  };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

interface FlatElement {
  role: string;
  label: string;
  value?: string;
  x: number;
  y: number;
}

/**
 * Flatten the element tree into a map keyed by "role:label@region"
 * for fast comparison. Only interactive and text-bearing elements.
 */
function flattenInteractive(
  elements: ScreenElement[],
  out: Map<string, FlatElement> = new Map(),
): Map<string, FlatElement> {
  for (const el of elements) {
    if (el.interactable || el.value) {
      // Key by role + label + approximate position (quantized to 50px grid to
      // tolerate minor layout shifts)
      const qx = Math.round(el.position.x / 50) * 50;
      const qy = Math.round(el.position.y / 50) * 50;
      const key = `${el.role}:${el.label}@${qx},${qy}`;
      out.set(key, {
        role: el.role,
        label: el.label,
        value: el.value,
        x: el.position.x,
        y: el.position.y,
      });
    }
    if (el.children) {
      flattenInteractive(el.children, out);
    }
  }
  return out;
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 3) + "...";
}
