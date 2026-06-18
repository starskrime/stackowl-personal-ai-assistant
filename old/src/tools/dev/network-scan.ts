import { exec } from "node:child_process";
import { promisify } from "node:util";
import * as net from "node:net";
import type { ToolImplementation } from "../registry.js";
import { log } from "../../logger.js";

const execAsync = promisify(exec);
const TIMEOUT_MS = 15000;
const PORT_TIMEOUT_MS = 2000;

const COMMON_PORTS = [22, 80, 443, 3000, 5000, 8080, 8443];

function checkPort(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.createConnection({
      host,
      port,
      timeout: PORT_TIMEOUT_MS,
    });
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

    // 1. ENTRY
    log.tool.debug("network_scan.execute: entry", { action, host });

    try {
      switch (action) {
        case "devices": {
          // 2. DECISION — passive ARP scan (no target host needed)
          log.tool.debug("network_scan.execute: scanning LAN devices via arp");
          const { stdout, stderr } = await execAsync("arp -a", {
            timeout: TIMEOUT_MS,
          });
          const output = (stdout || "").trim();
          if (!output) return stderr?.trim() || "No devices found.";
          const result = `LAN devices:\n${output}`;
          // 4. EXIT
          log.tool.debug("network_scan.execute: exit", { success: true, resultsCount: output.split("\n").length });
          return result;
        }

        case "ports": {
          if (!host) return "Error: host is required for the ports action.";
          // 2. DECISION — TCP port probe
          log.tool.debug("network_scan.execute: probing ports", { host, ports: COMMON_PORTS });
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
          const openCount = results.filter((r) => r.includes("OPEN")).length;
          // 3. STEP — scan complete
          log.tool.debug("network_scan.execute: port scan complete", { host, portsChecked: COMMON_PORTS.length, openCount });
          const result = `Port scan for ${host}:\n${results.join("\n")}`;
          // 4. EXIT
          log.tool.debug("network_scan.execute: exit", { success: true, resultsCount: results.length });
          return result;
        }

        case "ping": {
          if (!host) return "Error: host is required for the ping action.";
          // 3. STEP — ping subprocess
          log.tool.debug("network_scan.execute: pinging host", { host });
          const { stdout, stderr } = await execAsync(`ping -c 4 ${host}`, {
            timeout: TIMEOUT_MS,
          });
          const output = (stdout || "").trim();
          if (!output) return stderr?.trim() || "No ping response.";
          // 4. EXIT
          log.tool.debug("network_scan.execute: exit", { success: true, resultLen: output.length });
          return output;
        }

        case "my_ip": {
          // 3. STEP — HTTP request to ipify
          log.tool.debug("network_scan.execute: fetching public IP");
          const resp = await fetch("https://api.ipify.org", {
            signal: AbortSignal.timeout(15000),
          });
          const ip = await resp.text();
          const result = `Public IP: ${ip.trim()}`;
          // 4. EXIT
          log.tool.debug("network_scan.execute: exit", { success: true, resultLen: result.length });
          return result;
        }

        default:
          return `Unknown action: ${action}. Use devices, ports, ping, or my_ip.`;
      }
    } catch (e) {
      log.tool.error("network_scan.execute: operation failed", e instanceof Error ? e : new Error(String(e)), { action, host });
      return `network_scan error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
