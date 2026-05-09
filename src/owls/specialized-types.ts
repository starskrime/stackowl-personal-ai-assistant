/**
 * StackOwl — Specialized Owl Types
 *
 * Type definitions for folder-based specialized owl specifications.
 */

export interface SpecializedPersonality {
  challengeLevel: "low" | "medium" | "high" | "relentless";
  verbosity: "concise" | "balanced" | "verbose";
  tone: string;
}

export interface SpecializedModel {
  provider: string;
  model: string;
  maxTokens?: number;
}

export interface SpecializedPermissions {
  allowedTools: string[];
  deniedTools: string[];
  capabilityConstraints: string[];
}

export interface SpecializedRoutingRules {
  keywords: string[];
}

export interface SpecializedSkills {
  allowed: string[];
}

export interface SpecializedOwlSpec {
  name: string;
  type: "coordinator" | "specialist";
  role: string;
  emoji: string;
  personality: SpecializedPersonality;
  expertise: string[];
  model: SpecializedModel;
  permissions: SpecializedPermissions;
  routingRules: SpecializedRoutingRules;
  skills: SpecializedSkills;
  additionalPrompt: string;
  folderPath?: string;
  credentialsPath?: string;
}

// ─── Helper rebrand aliases (Element 17) ─────────────────────────
/** Alias for SpecializedOwlSpec — use HelperSpec in new code */
export type HelperSpec = SpecializedOwlSpec
