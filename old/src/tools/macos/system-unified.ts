/**
 * StackOwl — macOS Unified System Tool
 *
 * Dispatches to pluggable spotlight, focus_mode, notifications, and system_info implementations.
 * Only exposed to LLM sessions on Darwin (macOS) via platforms: ["darwin"].
 *
 * Supported actions:
 *   spotlight    — search files and apps via Spotlight
 *   focus_mode   — enable/disable macOS Focus modes
 *   notifications — send macOS notifications
 *   system_info  — retrieve CPU, memory, battery and other system stats
 */

import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

export interface MacosSystemDeps {
  spotlight?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  focus_mode?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  notifications?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  system_info?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}

export function createMacosSystemTool(deps: MacosSystemDeps): ToolImplementation {
  return {
    definition: {
      name: "macos_system",
      description:
        "macOS system tools: action:spotlight (search files/apps), action:focus_mode (enable/disable focus), " +
        "action:notifications (send notification), action:system_info (CPU, memory, battery). " +
        "Example: {action:'spotlight', query:'resume.pdf'}",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: spotlight, focus_mode, notifications, system_info",
            enum: ["spotlight", "focus_mode", "notifications", "system_info"],
          },
          query: {
            type: "string",
            description: "Search query (for action:spotlight)",
          },
          mode: {
            type: "string",
            description: "Focus mode name (for action:focus_mode)",
          },
          enabled: {
            type: "boolean",
            description: "Enable/disable (for action:focus_mode)",
          },
          title: {
            type: "string",
            description: "Notification title (for action:notifications)",
          },
          message: {
            type: "string",
            description: "Notification message (for action:notifications)",
          },
        },
        required: ["action"],
      },
      platforms: ["darwin"],
      capabilities: ["macos_spotlight", "macos_focus", "macos_notifications", "macos_system_info"],
    },
    category: "macos" as any,
    execute: async (args, context) => {
      const action = args["action"] as string;
      const key = action as keyof MacosSystemDeps;
      log.tool.debug("macos_system.execute: entry", { action });

      const impl = deps[key];

      if (!impl) {
        log.tool.debug("macos_system.execute: action not configured", { action, available: Object.keys(deps) });
        return JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "ACTION_NOT_SUPPORTED",
            message: `macOS system action '${action}' is not configured.`,
            suggestion: `Available actions: spotlight, focus_mode, notifications, system_info`,
          },
        });
      }

      log.tool.debug("macos_system.execute: dispatching to impl", { action });
      const result = await impl(args, context);
      log.tool.debug("macos_system.execute: exit", { success: true, action, resultLen: result.length });
      return result;
    },
  };
}
