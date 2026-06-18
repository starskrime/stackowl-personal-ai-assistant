/**
 * StackOwl — macOS Unified Communications Tool
 *
 * Dispatches to pluggable mail, contacts, and iMessage implementations.
 * Only exposed to LLM sessions on Darwin (macOS) via platforms: ["darwin"].
 *
 * Supported actions:
 *   mail      — read/send email via Apple Mail
 *   contacts  — look up contacts in macOS Contacts
 *   imessage  — send/read iMessages
 */

import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

export interface MacosCommsDeps {
  mail?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  contacts?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  imessage?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}

export function createMacosCommsTool(deps: MacosCommsDeps): ToolImplementation {
  return {
    definition: {
      name: "macos_comms",
      description:
        "macOS communications: action:mail (read/send email), action:contacts (look up contacts), " +
        "action:imessage (send/read iMessages). " +
        "Example: {action:'mail', operation:'read', count:5}",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: mail, contacts, imessage",
            enum: ["mail", "contacts", "imessage"],
          },
          operation: {
            type: "string",
            description: "Sub-operation (read, send, search, etc.)",
          },
          query: {
            type: "string",
            description: "Search query or contact name",
          },
          to: {
            type: "string",
            description: "Recipient (for send operations)",
          },
          body: {
            type: "string",
            description: "Message body (for send operations)",
          },
          count: {
            type: "number",
            description: "Number of items to retrieve",
          },
        },
        required: ["action"],
      },
      platforms: ["darwin"],
      capabilities: ["macos_mail", "macos_contacts", "macos_imessage"],
    },
    category: "macos" as any,
    execute: async (args, context) => {
      const action = args["action"] as string;
      const operation = args["operation"] as string | undefined;
      log.tool.debug("macos_comms.execute: entry", { action, operation });

      const impl = deps[action as keyof MacosCommsDeps];

      if (!impl) {
        log.tool.debug("macos_comms.execute: action not configured", { action, available: Object.keys(deps) });
        return JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "ACTION_NOT_SUPPORTED",
            message: `macOS comms action '${action}' is not configured.`,
            suggestion: `Available actions: mail, contacts, imessage`,
          },
        });
      }

      log.tool.debug("macos_comms.execute: dispatching to channel impl", { action, operation });
      const result = await impl(args, context);
      log.tool.debug("macos_comms.execute: exit", { success: true, action, resultLen: result.length });
      return result;
    },
  };
}
