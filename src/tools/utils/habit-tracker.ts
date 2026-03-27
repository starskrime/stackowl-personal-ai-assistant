import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

interface HabitEntry {
  habit: string;
  date: string;
  timestamp: string;
}

interface HabitData {
  entries: HabitEntry[];
}

function getDataPath(context: ToolContext): string {
  return join(context.cwd, "workspace", "habits.json");
}

function loadData(path: string): HabitData {
  if (!existsSync(path)) {
    return { entries: [] };
  }
  try {
    const raw = readFileSync(path, "utf-8");
    return JSON.parse(raw) as HabitData;
  } catch {
    return { entries: [] };
  }
}

function saveData(path: string, data: HabitData): void {
  const dir = dirname(path);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
}

function todayStr(): string {
  return new Date().toISOString().split("T")[0]!;
}

export const HabitTrackerTool: ToolImplementation = {
  definition: {
    name: "habit_tracker",
    description:
      "Track daily habits — log completions, view today's status, and see weekly history.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            'Action: "log" (log a habit completion), "status" (today\'s habits), "history" (last 7 days)',
        },
        habit: {
          type: "string",
          description: "Habit name (required for log action)",
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    try {
      const action = String(args.action);
      const dataPath = getDataPath(context);

      switch (action) {
        case "log": {
          const habit = args.habit ? String(args.habit) : "";
          if (!habit) {
            return 'Error: "habit" parameter is required for log action.';
          }
          const data = loadData(dataPath);
          const today = todayStr();

          // Check if already logged today
          const alreadyLogged = data.entries.some(
            (e) => e.habit === habit && e.date === today,
          );
          if (alreadyLogged) {
            return `"${habit}" is already logged for today (${today}).`;
          }

          data.entries.push({
            habit,
            date: today,
            timestamp: new Date().toISOString(),
          });
          saveData(dataPath, data);
          return `Logged "${habit}" for ${today}.`;
        }

        case "status": {
          const data = loadData(dataPath);
          const today = todayStr();
          const todayEntries = data.entries.filter((e) => e.date === today);

          if (todayEntries.length === 0) {
            return `No habits logged today (${today}).`;
          }

          const lines = todayEntries.map(
            (e) =>
              `  [x] ${e.habit} (${e.timestamp.split("T")[1]?.slice(0, 5)})`,
          );
          return `Today's habits (${today}):\n${lines.join("\n")}`;
        }

        case "history": {
          const data = loadData(dataPath);
          const dates: string[] = [];
          for (let i = 6; i >= 0; i--) {
            const d = new Date();
            d.setDate(d.getDate() - i);
            dates.push(d.toISOString().split("T")[0]!);
          }

          // Collect unique habits
          const allHabits = [
            ...new Set(
              data.entries
                .filter((e) => dates.includes(e.date))
                .map((e) => e.habit),
            ),
          ];

          if (allHabits.length === 0) {
            return "No habit data in the last 7 days.";
          }

          let result = "Habit History (last 7 days):\n";
          result += `${"".padEnd(20)} ${dates.map((d) => d.slice(5)).join(" ")}\n`;
          for (const habit of allHabits) {
            const row = dates
              .map((d) =>
                data.entries.some((e) => e.habit === habit && e.date === d)
                  ? " x   "
                  : " .   ",
              )
              .join("");
            result += `${habit.padEnd(20)} ${row}\n`;
          }
          return result;
        }

        default:
          return `Error: Unknown action "${action}". Use: log, status, or history.`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error with habit tracker: ${msg}`;
    }
  },
};
