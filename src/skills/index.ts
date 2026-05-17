/**
 * StackOwl — Skills Module
 *
 * StackOwl skill system with semantic routing,
 * usage tracking, and skill composition.
 */

export { SkillsLoader } from "./loader.js";
export { SkillsRegistry } from "./registry.js";
export { SkillParser, meetsRequirements } from "./parser.js";
export { ClawHubClient } from "./clawhub.js";
export { IntentRouter, type IntentMatch } from "./intent-router.js";
export { SkillTracker } from "./tracker.js";

export type {
  Skill,
  SkillMetadata,
  SkillInstall,
  SkillFilter,
  SkillLoadOptions,
  SkillUsageStats,
  SkillDependency,
  SkillComposition,
} from "./types.js";
