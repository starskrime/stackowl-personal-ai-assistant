/**
 * StackOwl — Specialized Owl Parser
 *
 * Parses specialized_owl.md files into SpecializedOwlSpec objects.
 */

import matter from "gray-matter";
import type {
  SpecializedOwlSpec,
  SpecializedPersonality,
  SpecializedModel,
  SpecializedPermissions,
  SpecializedRoutingRules,
  SpecializedSkills,
} from "./specialized-types.js";

export function parseSpecializedOwl(content: string): SpecializedOwlSpec {
  const { data } = matter(content);

  const personality: SpecializedPersonality = {
    challengeLevel: (data.challengeLevel as SpecializedPersonality["challengeLevel"]) ?? "medium",
    verbosity: (data.verbosity as SpecializedPersonality["verbosity"]) ?? "balanced",
    tone: (data.tone as string) ?? "neutral",
  };

  const model: SpecializedModel = {
    provider: (data.provider as string) ?? "openai",
    model: (data.model as string) ?? "gpt-4",
    maxTokens: data.maxTokens as number | undefined,
  };

  const permissions: SpecializedPermissions = {
    allowedTools: Array.isArray(data.allowedTools) ? data.allowedTools : [],
    deniedTools: Array.isArray(data.deniedTools) ? data.deniedTools : [],
    capabilityConstraints: Array.isArray(data.capabilityConstraints)
      ? data.capabilityConstraints
      : [],
  };

  const routingRules: SpecializedRoutingRules = {
    keywords: Array.isArray(data.keywords) ? data.keywords : [],
  };

  const skills: SpecializedSkills = {
    allowed: Array.isArray(data.allowedSkills) ? data.allowedSkills : [],
  };

  return {
    name: (data.name as string) ?? "Unknown",
    role: (data.role as string) ?? "",
    emoji: (data.emoji as string) ?? "🦉",
    personality,
    expertise: Array.isArray(data.domains) ? data.domains : [],
    model,
    permissions,
    routingRules,
    skills,
  };
}
