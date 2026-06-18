/**
 * harness.ts — Ink TUI v2 test render wrapper.
 *
 * Wraps ink-testing-library render with automatic store reset and
 * a clean snapshot of state after mounting.
 */

import React from "react";
import { render } from "ink-testing-library";
import { resetStore, getStore } from "../state/store.js";
import type { UiState } from "../state/store.js";

export interface RenderResult {
  lastFrame: () => string | undefined;
  unmount: () => void;
  store: UiState;
}

export function renderWithStore<P extends object>(
  component: React.ComponentType<P>,
  props: P,
): RenderResult {
  resetStore();
  const result = render(React.createElement(component, props));
  return {
    lastFrame: result.lastFrame,
    unmount: result.unmount,
    store: getStore(),
  };
}

export { resetStore, getStore };
