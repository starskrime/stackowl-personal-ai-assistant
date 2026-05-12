import type { SessionRunner } from "../../sessions/runner.js";
import type { SessionStore } from "../../sessions/store.js";

let runnerRef: SessionRunner | null = null;
let storeRef: SessionStore | null = null;

/** Called from src/index.ts after the runner is created. */
export function attachSessions(runner: SessionRunner, store: SessionStore): void {
  runnerRef = runner;
  storeRef = store;
}

export function getRunner(): SessionRunner {
  if (!runnerRef) throw new Error("SessionRunner not attached — call attachSessions() at bootstrap");
  return runnerRef;
}

export function getStore(): SessionStore {
  if (!storeRef) throw new Error("SessionStore not attached — call attachSessions() at bootstrap");
  return storeRef;
}

export function isAttached(): boolean {
  return runnerRef !== null && storeRef !== null;
}
