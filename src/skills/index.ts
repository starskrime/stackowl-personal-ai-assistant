/**
 * StackOwl — Skills Module
 *
 * OpenCLAW-compatible skill system.
 * Skills are instructions that teach the LLM how to accomplish tasks.
 */

export { SkillsLoader } from "./loader.js";
export { SkillsRegistry } from "./registry.js";
export { SkillParser, meetsRequirements } from "./parser.js";
export { ClawHubClient, SkillSelector } from "./clawhub.js";

export type {
  Skill,
  SkillMetadata,
  SkillInstall,
  SkillFilter,
  SkillLoadOptions,
} from "./types.js";
