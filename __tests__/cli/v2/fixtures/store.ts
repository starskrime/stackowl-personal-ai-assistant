/**
 * fixtures/store.ts — store helpers for TUI v2 unit tests.
 *
 * Provides a freshStore() factory that resets and returns a clean UiState
 * snapshot. Use this at the start of each test to guarantee isolation.
 */

import { resetStore, getStore, applyToStore } from "../../../../src/cli/v2/state/store.js";
import type { UiState } from "../../../../src/cli/v2/state/store.js";

/** Reset the Zustand store and return a clean state snapshot. */
export function freshStore(): UiState {
  resetStore();
  return getStore();
}

export { applyToStore, getStore };
