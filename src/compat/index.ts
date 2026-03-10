/**
 * StackOwl — OpenCLAW Compatibility Layer
 *
 * Provides adapters to use OpenCLAW tools, channels, and capabilities.
 * This allows StackOwl to leverage the broader OpenCLAW ecosystem.
 */

export { BrowserTool } from "./tools/browser.js";
export { WebSearchTool } from "./tools/web-search.js";
export { ToolProfileManager } from "./profiles.js";
export { DockerSandbox, executeWithSandbox } from "./sandbox.js";

export type {
  OpenCLAWTool,
  ToolExecutionResult,
  ToolProfile,
  SandboxConfig,
  OpenCLAWConfig,
} from "./types.js";
