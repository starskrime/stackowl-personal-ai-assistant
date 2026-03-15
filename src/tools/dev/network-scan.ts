import { exec } from "node:child_process";
import { promisify } from "node:util";
import * as net from "node:net";
import type { ToolImplementation } from "../registry.js";

const execAsync = promisify(exec);
const TIMEOUT_MS = 15000;
const PORT_TIMEOUT_MS = 2000;

const COMMON_PORTS = [22, 80, 443, 3000, 5000, 8080, 8443];

function checkPort(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port, timeout: PORT_TIMEOUT_MS });
    socket.on("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.on("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.on("error", () => {
      socket.destroy();
      resolve(false);
    });
  });
}

export const NetworkScanTool: ToolImplementation = {
  definition: {
    name: "network_scan",
    description:
      "Network utilities — discover devices on LAN, scan ports, ping hosts, get public IP.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["devices", "ports", "ping", "my_ip"],
          description:
            'Action: "devices" lists LAN devices, "ports" scans common ports on a host, "ping" pings a host, "my_ip" gets public IP.',
        },
        host: {
          type: "string",
          description: 'Target host for "ports" and "ping" actions.',
        },
      },
      required: ["action"],
    },
  },

  async execute(args, _context) {
    const action = args.action as string;
    const host = args.host as string | undefined;

    try {
      switch (action) {
        case "devices": {
          const { stdout, stderr } = await execAsync("arp -a", { timeout: TIMEOUT_MS });
          const output = (stdout || "").trim();
          if (!output) return stderr?.trim() || "No devices found.";
          return `LAN devices:\n${output}`;
        }

        case "ports": {
          if (!host) return "Error: host is required for the ports action.";
          const results: string[] = [];
          const checks = COMMON_PORTS.map(async (port) => {
            const open = await checkPort(host, port);
            results.push(`  Port ${port}: ${open ? "OPEN" : "closed"}`);
          });
          await Promise.all(checks);
          // Sort by port number for consistent output
          results.sort((a, b) => {
            const pa = parseInt(a.match(/\d+/)![0]);
            const pb = parseInt(b.match(/\d+/)![0]);
            return pa - pb;
          });
          return `Port scan for ${host}:\n${results.join("\n")}`;
        }

        case "ping": {
          if (!host) return "Error: host is required for the ping action.";
          const { stdout, stderr } = await execAsync(`ping -c 4 ${host}`, {
            timeout: TIMEOUT_MS,
          });
          const output = (stdout || "").trim();
          if (!output) return stderr?.trim() || "No ping response.";
          return output;
        }

        case "my_ip": {
          const resp = await fetch("https://api.ipify.org", {
            signal: AbortSignal.timeout(15000),
          });
          const ip = await resp.text();
          return `Public IP: ${ip.trim()}`;
        }

        default:
          return `Unknown action: ${action}. Use devices, ports, ping, or my_ip.`;
      }
    } catch (e) {
      return `network_scan error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
