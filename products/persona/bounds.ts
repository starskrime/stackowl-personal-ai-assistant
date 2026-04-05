/**
 * Persona Engine — Trait Bounds System
 *
 * Prevents persona traits from drifting outside acceptable ranges.
 * Applied after every evolution pass and every decay cycle.
 */

import type {
  PersonaTraits,
  TraitBounds,
  ChallengeLevel,
  TeachingStyle,
  RiskTolerance,
} from "./types.js";

const NUMERIC_TRAITS = [
  "warmth",
  "formality",
  "humor",
  "verbosity",
  "proactivity",
  "empathy",
  "directness",
  "creativity",
] as const;

/**
 * Apply bounds to a traits object. Returns a new object with all traits clamped.
 */
export function applyBounds(
  traits: PersonaTraits,
  bounds: TraitBounds,
): PersonaTraits {
  const result = { ...traits, domainExpertise: { ...traits.domainExpertise } };

  // Clamp numeric traits
  for (const key of NUMERIC_TRAITS) {
    const bound = bounds[key] as [number, number] | undefined;
    if (bound) {
      const [min, max] = bound;
      result[key] = Math.max(min, Math.min(max, result[key]));
    } else {
      // Default clamp to 0–1
      result[key] = Math.max(0, Math.min(1, result[key]));
    }
  }

  // Clamp discrete traits to allowed values
  if (bounds.challengeLevel && bounds.challengeLevel.length > 0) {
    if (!bounds.challengeLevel.includes(result.challengeLevel)) {
      // Pick the closest allowed value
      result.challengeLevel = clampDiscrete(
        result.challengeLevel,
        bounds.challengeLevel,
        CHALLENGE_ORDER,
      ) as ChallengeLevel;
    }
  }

  if (bounds.teachingStyle && bounds.teachingStyle.length > 0) {
    if (!bounds.teachingStyle.includes(result.teachingStyle)) {
      result.teachingStyle = bounds.teachingStyle[0];
    }
  }

  if (bounds.riskTolerance && bounds.riskTolerance.length > 0) {
    if (!bounds.riskTolerance.includes(result.riskTolerance)) {
      result.riskTolerance = clampDiscrete(
        result.riskTolerance,
        bounds.riskTolerance,
        RISK_ORDER,
      ) as RiskTolerance;
    }
  }

  // Domain expertise always 0–1
  for (const domain of Object.keys(result.domainExpertise)) {
    result.domainExpertise[domain] = Math.max(
      0,
      Math.min(1, result.domainExpertise[domain]),
    );
  }

  return result;
}

/**
 * Compute a diff showing which traits drifted from base.
 */
export function computeDrift(
  base: PersonaTraits,
  current: PersonaTraits,
): Array<{ trait: string; from: number | string; to: number | string; magnitude: number }> {
  const changes: Array<{ trait: string; from: number | string; to: number | string; magnitude: number }> = [];

  for (const key of NUMERIC_TRAITS) {
    const diff = Math.abs(current[key] - base[key]);
    if (diff >= 0.05) {
      changes.push({ trait: key, from: base[key], to: current[key], magnitude: diff });
    }
  }

  if (current.challengeLevel !== base.challengeLevel) {
    changes.push({ trait: "challengeLevel", from: base.challengeLevel, to: current.challengeLevel, magnitude: 1 });
  }
  if (current.teachingStyle !== base.teachingStyle) {
    changes.push({ trait: "teachingStyle", from: base.teachingStyle, to: current.teachingStyle, magnitude: 1 });
  }
  if (current.riskTolerance !== base.riskTolerance) {
    changes.push({ trait: "riskTolerance", from: base.riskTolerance, to: current.riskTolerance, magnitude: 1 });
  }

  return changes.sort((a, b) => b.magnitude - a.magnitude);
}

/**
 * Apply weekly decay toward base traits.
 * Prevents stale user-specific drift from dominating.
 */
export function applyDecay(
  traits: PersonaTraits,
  baseTraits: PersonaTraits,
  decayRate: number,
  weeksSinceLastEvolved: number,
): PersonaTraits {
  if (decayRate <= 0 || weeksSinceLastEvolved <= 0) return traits;

  const factor = Math.min(0.5, decayRate * weeksSinceLastEvolved);
  const result = { ...traits, domainExpertise: { ...traits.domainExpertise } };

  for (const key of NUMERIC_TRAITS) {
    result[key] = result[key] + (baseTraits[key] - result[key]) * factor;
  }

  return result;
}

// ─── Helpers ──────────────────────────────────────────────────────────────

const CHALLENGE_ORDER = ["low", "medium", "high", "relentless"];
const RISK_ORDER = ["cautious", "moderate", "aggressive"];

function clampDiscrete(value: string, allowed: string[], order: string[]): string {
  const valueIdx = order.indexOf(value);
  const allowedIndices = allowed.map((v) => order.indexOf(v)).filter((i) => i >= 0).sort((a, b) => a - b);
  if (allowedIndices.length === 0) return allowed[0];

  // Pick closest allowed value in the ordering
  let closest = allowedIndices[0];
  let minDist = Math.abs(valueIdx - allowedIndices[0]);
  for (const idx of allowedIndices) {
    const dist = Math.abs(valueIdx - idx);
    if (dist < minDist) {
      minDist = dist;
      closest = idx;
    }
  }

  return order[closest] ?? allowed[0];
}
