import { exec } from "node:child_process";
import { promisify } from "node:util";
import type { ToolImplementation } from "../registry.js";
import { log } from "../../logger.js";

const execAsync = promisify(exec);
const TIMEOUT_MS = 15000;

export const DockerTool: ToolImplementation = {
  definition: {
    name: "docker_tool",
    description:
      "Manage Docker containers — list, start, stop, restart, view logs, and check resource usage.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["ps", "images", "logs", "start", "stop", "restart", "stats"],
          description: "Docker action to perform.",
        },
        container_name: {
          type: "string",
          description:
            "Container name or ID. Required for logs, start, stop, restart.",
        },
        tail: {
          type: "number",
          description:
            "Number of log lines to show (default 50). Used with logs.",
        },
      },
      required: ["action"],
    },
  },

  async execute(args, _context) {
    const action = args.action as string;
    const container = args.container_name as string | undefined;
    const tail = (args.tail as number) ?? 50;

    // 1. ENTRY
    log.tool.debug("docker_tool.execute: entry", { action, container });

    try {
      let cmd: string;

      switch (action) {
        case "ps":
          cmd =
            "docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'";
          break;
        case "images":
          cmd =
            "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}'";
          break;
        case "logs":
          if (!container)
            return "Error: container_name is required for the logs action.";
          cmd = `docker logs --tail ${tail} ${container}`;
          break;
        case "start":
          if (!container)
            return "Error: container_name is required for the start action.";
          cmd = `docker start ${container}`;
          break;
        case "stop":
          if (!container)
            return "Error: container_name is required for the stop action.";
          cmd = `docker stop ${container}`;
          break;
        case "restart":
          if (!container)
            return "Error: container_name is required for the restart action.";
          cmd = `docker restart ${container}`;
          break;
        case "stats":
          cmd =
            "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}'";
          break;
        default:
          return `Unknown action: ${action}. Use ps, images, logs, start, stop, restart, or stats.`;
      }

      // 2. DECISION — read-only vs state-changing operation
      const isMutating = ["start", "stop", "restart"].includes(action);
      log.tool.debug("docker_tool.execute: command built", { cmd, isMutating, container });

      // 3. STEP — subprocess spawned
      const { stdout, stderr } = await execAsync(cmd, { timeout: TIMEOUT_MS });
      const output = (stdout || "").trim();
      const errors = (stderr || "").trim();

      log.tool.debug("docker_tool.execute: command complete", { outputLen: output.length, hasStderr: !!errors });

      if (errors && !output) return `Docker error:\n${errors}`;
      if (errors) return `${output}\n\n(stderr: ${errors})`;

      const result = output || `docker ${action} completed successfully.`;

      // 4. EXIT
      log.tool.debug("docker_tool.execute: exit", { success: true, resultLen: result.length });
      return result;
    } catch (e) {
      log.tool.error("docker_tool.execute: command failed", e instanceof Error ? e : new Error(String(e)), { action, container });
      return `docker_tool error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
