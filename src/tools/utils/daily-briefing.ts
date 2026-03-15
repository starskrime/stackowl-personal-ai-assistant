import { exec } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../registry.js";

function execPromise(cmd: string, timeout = 15000): Promise<string> {
  return new Promise((resolve) => {
    exec(cmd, { timeout }, (error, stdout) => {
      if (error) {
        resolve("");
      } else {
        resolve(stdout.trim());
      }
    });
  });
}

export const DailyBriefingTool: ToolImplementation = {
  definition: {
    name: "daily_briefing",
    description:
      "Generate a morning briefing with date, weather, today's calendar events, pending reminders, and system status.",
    parameters: {
      type: "object",
      properties: {},
      required: [],
    },
  },

  async execute(
    _args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const sections: string[] = [];

      // Date & Time
      const now = new Date();
      const dateStr = now.toLocaleDateString("en-US", {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
      });
      const timeStr = now.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
      });
      sections.push(`Daily Briefing\n${"=".repeat(40)}\n${dateStr} at ${timeStr}`);

      // Weather (auto-detect location via wttr.in)
      try {
        const weatherRes = await fetch("https://wttr.in/?format=j1", {
          headers: { "User-Agent": "stackowl-briefing" },
          signal: AbortSignal.timeout(15000),
        });
        if (weatherRes.ok) {
          const data = (await weatherRes.json()) as Record<string, unknown>;
          const nearest = data.nearest_area as Record<string, unknown>[];
          const area = nearest?.[0];
          const areaName = (area?.areaName as Record<string, unknown>[])?.[0]
            ?.value ?? "Unknown";
          const country = (area?.country as Record<string, unknown>[])?.[0]
            ?.value ?? "";
          const current = (
            data.current_condition as Record<string, unknown>[]
          )?.[0];
          if (current) {
            const desc = (
              current.weatherDesc as Record<string, unknown>[]
            )?.[0]?.value;
            sections.push(
              `\nWeather (${areaName}, ${country}):\n  ${desc}, ${current.temp_C}°C / ${current.temp_F}°F, Humidity: ${current.humidity}%`,
            );
          }
        }
      } catch {
        sections.push("\nWeather: Unable to fetch");
      }

      // Calendar events (macOS Calendar via osascript)
      const calendarScript = `osascript -e '
        set today to current date
        set time of today to 0
        set tomorrow to today + (1 * days)
        tell application "Calendar"
          set output to ""
          repeat with c in calendars
            set evts to (every event of c whose start date >= today and start date < tomorrow)
            repeat with e in evts
              set output to output & "  - " & summary of e & " at " & time string of start date of e & linefeed
            end repeat
          end repeat
          if output is "" then
            return "  No events today"
          else
            return output
          end if
        end tell
      '`;
      const calendarOutput = await execPromise(calendarScript);
      sections.push(
        `\nToday's Calendar:\n${calendarOutput || "  Unable to access Calendar"}`,
      );

      // Pending reminders (macOS Reminders via osascript)
      const remindersScript = `osascript -e '
        tell application "Reminders"
          set output to ""
          repeat with r in (every reminder whose completed is false)
            set output to output & "  - " & name of r & linefeed
          end repeat
          if output is "" then
            return "  No pending reminders"
          else
            return output
          end if
        end tell
      '`;
      const remindersOutput = await execPromise(remindersScript);
      sections.push(
        `\nPending Reminders:\n${remindersOutput || "  Unable to access Reminders"}`,
      );

      // System info
      const [batteryOutput, diskOutput] = await Promise.all([
        execPromise(
          "pmset -g batt | grep -Eo '\\d+%' | head -1",
        ),
        execPromise(
          "df -h / | tail -1 | awk '{print $4 \" available of \" $2}'",
        ),
      ]);
      let sysInfo = "\nSystem Status:";
      if (batteryOutput) {
        sysInfo += `\n  Battery: ${batteryOutput}`;
      }
      if (diskOutput) {
        sysInfo += `\n  Disk: ${diskOutput}`;
      }
      sections.push(sysInfo);

      return sections.join("\n");
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error generating briefing: ${msg}`;
    }
  },
};
