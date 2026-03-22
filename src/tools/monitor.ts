/**
 * StackOwl — Monitor Tool
 *
 * Exposes health check management to the LLM engine.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import type { HealthChecker } from "../monitoring/checker.js";
import type { HealthCheck } from "../monitoring/types.js";

export function createMonitorTool(checker: HealthChecker): ToolImplementation {
  return {
    definition: {
      name: "monitor",
      description:
        "Manage infrastructure health checks. Actions: list, add, remove, status, alerts. " +
        "Use 'add' to create a new health check, 'status' to see current health, " +
        "'alerts' to see active alerts.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            enum: ["list", "add", "remove", "status", "alerts", "acknowledge"],
            description: "The action to perform",
          },
          name: {
            type: "string",
            description: "Name for the health check (for add)",
          },
          type: {
            type: "string",
            enum: ["http", "tcp", "dns", "command"],
            description: "Check type (for add). Default: http",
          },
          target: {
            type: "string",
            description: "Target URL, host:port, or command (for add)",
          },
          checkId: {
            type: "string",
            description: "Check ID (for remove, status)",
          },
          alertId: {
            type: "string",
            description: "Alert ID (for acknowledge)",
          },
          intervalSeconds: {
            type: "number",
            description: "Check interval in seconds (for add). Default: 300",
          },
        },
        required: ["action"],
      },
    },
    category: "network",
    source: "builtin",
    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
      const action = args.action as string;

      switch (action) {
        case "list": {
          const checks = checker.getChecks();
          if (checks.length === 0) return "No health checks configured.";
          return checks
            .map(c => {
              const result = checker.getLastResult(c.id);
              const status = result?.status ?? "unknown";
              return `- ${c.name} (${c.type}:${c.target}) — ${status} [${c.enabled ? "enabled" : "disabled"}]`;
            })
            .join("\n");
        }

        case "add": {
          const name = args.name as string;
          const target = args.target as string;
          if (!name || !target) return "Error: name and target are required for add";

          const check: HealthCheck = {
            id: `check-${name.toLowerCase().replace(/\s+/g, "-")}-${Date.now()}`,
            name,
            type: (args.type as HealthCheck["type"]) ?? "http",
            target,
            intervalSeconds: (args.intervalSeconds as number) ?? 300,
            timeoutMs: 10_000,
            failThreshold: 2,
            enabled: true,
            tags: [],
          };
          checker.addCheck(check);
          await checker.save();
          return `Health check "${name}" added. Monitoring ${target} every ${check.intervalSeconds}s.`;
        }

        case "remove": {
          const id = args.checkId as string;
          if (!id) return "Error: checkId is required for remove";
          checker.removeCheck(id);
          await checker.save();
          return `Health check "${id}" removed.`;
        }

        case "status": {
          const id = args.checkId as string;
          if (id) {
            const result = checker.getLastResult(id);
            if (!result) return `No results for check "${id}".`;
            return `${id}: ${result.status} (${result.responseTimeMs}ms)${result.error ? ` — ${result.error}` : ""}`;
          }
          // All statuses
          const checks = checker.getChecks();
          if (checks.length === 0) return "No health checks configured.";
          return checks
            .map(c => {
              const r = checker.getLastResult(c.id);
              return `${c.name}: ${r?.status ?? "unknown"} (${r?.responseTimeMs ?? "?"}ms)`;
            })
            .join("\n");
        }

        case "alerts": {
          const alerts = checker.getActiveAlerts();
          if (alerts.length === 0) return "No active alerts.";
          return alerts
            .map(a => `[${a.severity.toUpperCase()}] ${a.message} (${new Date(a.timestamp).toLocaleString()})`)
            .join("\n");
        }

        case "acknowledge": {
          const alertId = args.alertId as string;
          if (!alertId) return "Error: alertId is required for acknowledge";
          checker.acknowledgeAlert(alertId);
          await checker.save();
          return `Alert "${alertId}" acknowledged.`;
        }

        default:
          return `Unknown action: ${action}. Use list, add, remove, status, alerts, or acknowledge.`;
      }
    },
  };
}
