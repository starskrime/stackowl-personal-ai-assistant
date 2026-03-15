import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import type { ToolImplementation } from "../registry.js";

interface MonitorEntry {
  url: string;
  intervalMinutes: number;
  contentHash: string;
  lastChecked: string;
}

interface MonitorsFile {
  monitors: MonitorEntry[];
}

async function loadMonitors(filePath: string): Promise<MonitorsFile> {
  try {
    const raw = await readFile(filePath, "utf-8");
    return JSON.parse(raw) as MonitorsFile;
  } catch {
    return { monitors: [] };
  }
}

async function saveMonitors(filePath: string, data: MonitorsFile): Promise<void> {
  const dir = filePath.substring(0, filePath.lastIndexOf("/"));
  await mkdir(dir, { recursive: true });
  await writeFile(filePath, JSON.stringify(data, null, 2), "utf-8");
}

function hashContent(content: string): string {
  return createHash("md5").update(content).digest("hex");
}

export const WebMonitorTool: ToolImplementation = {
  definition: {
    name: "web_monitor",
    description:
      "Monitor web pages for changes. Watch a URL and check later to see what changed.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["watch", "list", "check", "stop"],
          description:
            'Action to perform: "watch" a URL, "list" watched URLs, "check" all for changes, or "stop" watching a URL.',
        },
        url: {
          type: "string",
          description: 'URL to watch or stop watching. Required for "watch" and "stop".',
        },
        interval_minutes: {
          type: "number",
          description: "Check interval in minutes (default 60). Used with watch.",
        },
      },
      required: ["action"],
    },
  },

  async execute(args, context) {
    const action = args.action as string;
    const url = args.url as string | undefined;
    const intervalMinutes = (args.interval_minutes as number) ?? 60;
    const monitorsPath = join(context.cwd, "workspace", "monitors.json");

    try {
      if (action === "watch") {
        if (!url) return "Error: url is required for the watch action.";

        const data = await loadMonitors(monitorsPath);
        const existing = data.monitors.find((m) => m.url === url);
        if (existing) {
          existing.intervalMinutes = intervalMinutes;
          await saveMonitors(monitorsPath, data);
          return `Updated monitor for ${url} — interval set to ${intervalMinutes} minutes.`;
        }

        // Fetch initial content to get baseline hash
        let initialHash = "";
        try {
          const resp = await fetch(url, { signal: AbortSignal.timeout(15000) });
          const body = await resp.text();
          initialHash = hashContent(body);
        } catch (e) {
          return `Error fetching ${url} for initial snapshot: ${e instanceof Error ? e.message : String(e)}`;
        }

        data.monitors.push({
          url,
          intervalMinutes,
          contentHash: initialHash,
          lastChecked: new Date().toISOString(),
        });
        await saveMonitors(monitorsPath, data);
        return `Now watching ${url} (interval: ${intervalMinutes}m). Initial content hash: ${initialHash}`;
      }

      if (action === "list") {
        const data = await loadMonitors(monitorsPath);
        if (data.monitors.length === 0) return "No URLs are currently being monitored.";
        const lines = data.monitors.map(
          (m) => `- ${m.url} (every ${m.intervalMinutes}m, last checked: ${m.lastChecked})`
        );
        return `Monitored URLs:\n${lines.join("\n")}`;
      }

      if (action === "check") {
        const data = await loadMonitors(monitorsPath);
        if (data.monitors.length === 0) return "No URLs are currently being monitored.";

        const results: string[] = [];
        for (const monitor of data.monitors) {
          try {
            const resp = await fetch(monitor.url, { signal: AbortSignal.timeout(15000) });
            const body = await resp.text();
            const newHash = hashContent(body);
            const changed = newHash !== monitor.contentHash;
            results.push(
              `${monitor.url}: ${changed ? "CHANGED" : "no change"} (old: ${monitor.contentHash.slice(0, 8)}, new: ${newHash.slice(0, 8)})`
            );
            monitor.contentHash = newHash;
            monitor.lastChecked = new Date().toISOString();
          } catch (e) {
            results.push(
              `${monitor.url}: ERROR — ${e instanceof Error ? e.message : String(e)}`
            );
          }
        }
        await saveMonitors(monitorsPath, data);
        return `Check results:\n${results.join("\n")}`;
      }

      if (action === "stop") {
        if (!url) return "Error: url is required for the stop action.";
        const data = await loadMonitors(monitorsPath);
        const before = data.monitors.length;
        data.monitors = data.monitors.filter((m) => m.url !== url);
        if (data.monitors.length === before) return `URL ${url} was not being monitored.`;
        await saveMonitors(monitorsPath, data);
        return `Stopped monitoring ${url}.`;
      }

      return `Unknown action: ${action}. Use watch, list, check, or stop.`;
    } catch (e) {
      return `web_monitor error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
