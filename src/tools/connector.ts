/**
 * StackOwl — Connector Tool
 *
 * Exposes connector management to the LLM engine.
 * Allows owls to list presets, configure connections, and check status.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ConnectorResolver } from "../connectors/resolver.js";
import {
  listPresets,
  listCategories,
  getPreset,
} from "../connectors/presets.js";

export function createConnectorTool(
  resolver: ConnectorResolver,
): ToolImplementation {
  return {
    definition: {
      name: "connector",
      description:
        "Manage app connectors. Actions: presets (list available), configured (list active), " +
        "connect (configure a new connector), disconnect, status. " +
        "Connectors let you interact with GitHub, AWS, Kubernetes, databases, Slack, etc.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            enum: ["presets", "configured", "connect", "disconnect", "status"],
            description: "The action to perform",
          },
          presetId: {
            type: "string",
            description: "Connector preset ID (e.g., github, aws, postgres)",
          },
          env: {
            type: "object",
            description:
              "Environment variables for the connector (for connect action)",
          },
          category: {
            type: "string",
            description: "Filter presets by category",
          },
        },
        required: ["action"],
      },
    },
    category: "network",
    source: "builtin",
    async execute(
      args: Record<string, unknown>,
      _context: ToolContext,
    ): Promise<string> {
      const action = args.action as string;

      switch (action) {
        case "presets": {
          const category = args.category as string | undefined;
          const presets = category
            ? listPresets(category as Parameters<typeof listPresets>[0])
            : listPresets();

          if (presets.length === 0)
            return "No presets available for this category.";

          const categories = listCategories();
          const lines = [
            `Available connectors (${presets.length}) — categories: ${categories.join(", ")}\n`,
          ];
          for (const p of presets) {
            lines.push(
              `${p.icon} **${p.name}** (\`${p.id}\`) — ${p.description}` +
                `\n  Requires: ${p.requiredEnv.join(", ") || "nothing"}`,
            );
          }
          return lines.join("\n");
        }

        case "configured": {
          const instances = resolver.getInstances();
          if (instances.length === 0) return "No connectors configured yet.";
          return instances
            .map((i) => {
              const preset = getPreset(i.presetId);
              const health =
                i.healthy === undefined
                  ? "unchecked"
                  : i.healthy
                    ? "healthy"
                    : "unhealthy";
              return `${preset?.icon ?? "🔌"} **${i.name}** (${i.presetId}) — ${i.enabled ? "enabled" : "disabled"} — ${health}`;
            })
            .join("\n");
        }

        case "connect": {
          const presetId = args.presetId as string;
          if (!presetId)
            return "Error: presetId is required. Use 'presets' to see available options.";
          const env = (args.env as Record<string, string>) ?? {};

          const preset = getPreset(presetId);
          if (!preset)
            return `Unknown preset: ${presetId}. Use 'presets' to see available options.`;

          const missing = preset.requiredEnv.filter((k) => !env[k]);
          if (missing.length > 0) {
            return `Missing required credentials: ${missing.join(", ")}.\nPlease provide them in the 'env' parameter.`;
          }

          try {
            await resolver.configure(presetId, env);
            return `${preset.icon} Connected to **${preset.name}**! The connector is ready to use.`;
          } catch (err) {
            return `Failed to connect: ${err instanceof Error ? err.message : String(err)}`;
          }
        }

        case "disconnect": {
          const presetId = args.presetId as string;
          if (!presetId) return "Error: presetId is required.";
          await resolver.remove(presetId);
          return `Disconnected from ${presetId}.`;
        }

        case "status": {
          const instances = resolver.getEnabledInstances();
          if (instances.length === 0) return "No active connectors.";
          return instances
            .map((i) => {
              const preset = getPreset(i.presetId);
              const lastCheck = i.lastHealthCheck
                ? new Date(i.lastHealthCheck).toLocaleString()
                : "never";
              return `${preset?.icon ?? "🔌"} ${i.name}: ${i.healthy === true ? "✅ healthy" : i.healthy === false ? "❌ unhealthy" : "❓ unchecked"} (last: ${lastCheck})`;
            })
            .join("\n");
        }

        default:
          return `Unknown action: ${action}. Use presets, configured, connect, disconnect, or status.`;
      }
    },
  };
}
