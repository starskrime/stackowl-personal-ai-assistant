/**
 * StackOwl — CapabilityRegistry (Defect 3 Fix)
 *
 * Singleton module where subsystems declare their status (FULL/DEGRADED/OFFLINE) at boot.
 * Enables two integration points:
 *   1. Boot log emits a capability.snapshot JSONL record so degraded states are visible.
 *   2. System prompt builder reads the registry and injects degradation notices.
 *
 * Module-level singleton (not a class instance) — functions operate over a shared Map.
 */

import { log } from "../logger.js";

export type CapabilityStatus = "FULL" | "DEGRADED" | "OFFLINE";

export interface CapabilityEntry {
  name: string;
  status: CapabilityStatus;
  reason?: string;
  registeredAt: number; // Date.now()
}

export interface CapabilitySnapshot {
  event: "capability.snapshot";
  capabilities: CapabilityEntry[];
  degradedCount: number;
  fullCount: number;
}

// ─── Module-level singleton store ────────────────────────────────────────────

const registry = new Map<string, CapabilityEntry>();

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Register or update a capability's status.
 * Logs at warn level if status is DEGRADED or OFFLINE.
 */
export function registerCapability(
  name: string,
  status: CapabilityStatus,
  reason?: string,
): void {
  const entry: CapabilityEntry = {
    name,
    status,
    reason,
    registeredAt: Date.now(),
  };
  registry.set(name, entry);

  if (status === "DEGRADED" || status === "OFFLINE") {
    log.engine.warn("capability.registered.degraded", { name, status, reason });
  }
}

/**
 * Retrieve the status entry for a named capability.
 * Returns undefined if the capability has not been registered.
 */
export function getCapability(name: string): CapabilityEntry | undefined {
  return registry.get(name);
}

/**
 * Return all registered capability entries.
 */
export function getAllCapabilities(): CapabilityEntry[] {
  return Array.from(registry.values());
}

/**
 * Return only entries where status is not FULL (i.e., DEGRADED or OFFLINE).
 */
export function getDegradedCapabilities(): CapabilityEntry[] {
  return Array.from(registry.values()).filter((e) => e.status !== "FULL");
}

/**
 * Build a degradation notice string suitable for injection into the system prompt.
 * Returns an empty string when all registered capabilities are FULL.
 */
export function buildDegradationPrompt(): string {
  const degraded = getDegradedCapabilities();
  if (degraded.length === 0) return "";

  const lines = degraded.map((e) => {
    const suffix = e.reason ? ` — ${e.reason}` : "";
    return `- ${e.name}: ${e.status}${suffix}`;
  });

  return [
    "⚠️ Degraded subsystems (tell the user if these affect your response):",
    ...lines,
  ].join("\n");
}

/**
 * Produce a JSONL-ready snapshot record of all registered capabilities.
 * Suitable for passing directly to log.engine.info("capability.snapshot", snap).
 */
export function snapshotLog(): CapabilitySnapshot {
  const all = getAllCapabilities();
  const fullCount = all.filter((e) => e.status === "FULL").length;
  const degradedCount = all.filter((e) => e.status !== "FULL").length;

  return {
    event: "capability.snapshot",
    capabilities: all,
    degradedCount,
    fullCount,
  };
}

// ─── Test helper ─────────────────────────────────────────────────────────────

/**
 * Reset all registered capabilities. FOR TESTING USE ONLY.
 * Call in beforeEach to ensure test isolation.
 */
export function _resetForTest(): void {
  registry.clear();
}
