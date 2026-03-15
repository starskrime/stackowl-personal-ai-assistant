import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

export const SystemInfoTool: ToolImplementation = {
    definition: {
        name: "system_info",
        description:
            "Get system information — macOS version, CPU usage, memory, disk space, battery level, and uptime.",
        parameters: {
            type: "object",
            properties: {},
            required: [],
        },
    },

    async execute(_args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const commands = [
            { label: "macOS Version", cmd: "sw_vers" },
            { label: "Memory (bytes)", cmd: "sysctl -n hw.memsize" },
            { label: "Disk Usage", cmd: "df -h /" },
            { label: "Uptime", cmd: "uptime" },
            { label: "Battery", cmd: "pmset -g batt" },
            { label: "CPU", cmd: "top -l 1 -n 0 | head -10" },
        ];

        const results: string[] = [];

        for (const { label, cmd } of commands) {
            try {
                const { stdout } = await execAsync(cmd, { timeout: 15000 });
                results.push(`=== ${label} ===\n${stdout.trim()}`);
            } catch {
                results.push(`=== ${label} ===\nUnable to retrieve.`);
            }
        }

        // Format memory as GB
        try {
            const memLine = results.find(r => r.includes("Memory (bytes)"));
            if (memLine) {
                const match = memLine.match(/\n(\d+)/);
                if (match) {
                    const gb = (parseInt(match[1], 10) / (1024 ** 3)).toFixed(1);
                    const idx = results.findIndex(r => r.includes("Memory (bytes)"));
                    results[idx] = `=== Memory ===\n${gb} GB`;
                }
            }
        } catch {
            // Keep raw output
        }

        return results.join("\n\n");
    },
};
