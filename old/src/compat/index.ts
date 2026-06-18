/**
 * StackOwl — StackOwl compatibility layer
 *
 * Provides adapters to use StackOwl tools, channels, and capabilities.
 * This allows StackOwl to leverage the broader StackOwl ecosystem.
 */

export { BrowserTool } from "./tools/browser.js";
export { ToolProfileManager } from "./profiles.js";
export { DockerSandbox, executeWithSandbox } from "./sandbox.js";

export type {
  StackOwlTool,
  ToolExecutionResult,
  ToolProfile,
  SandboxConfig,
  StackOwlConfig,
} from "./types.js";
