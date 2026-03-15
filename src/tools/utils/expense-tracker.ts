import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

interface Expense {
  amount: number;
  category: string;
  description: string;
  date: string;
  timestamp: string;
}

interface ExpenseData {
  expenses: Expense[];
}

function getDataPath(context: ToolContext): string {
  return join(context.cwd, "workspace", "expenses.json");
}

function loadData(path: string): ExpenseData {
  if (!existsSync(path)) {
    return { expenses: [] };
  }
  try {
    const raw = readFileSync(path, "utf-8");
    return JSON.parse(raw) as ExpenseData;
  } catch {
    return { expenses: [] };
  }
}

function saveData(path: string, data: ExpenseData): void {
  const dir = dirname(path);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
}

export const ExpenseTrackerTool: ToolImplementation = {
  definition: {
    name: "expense_tracker",
    description:
      "Track expenses — add purchases, view monthly summaries by category, and export to CSV.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            'Action: "add" (log expense), "summary" (monthly totals by category), "list" (recent expenses), "export" (CSV)',
        },
        amount: {
          type: "number",
          description: "Expense amount (for add action)",
        },
        category: {
          type: "string",
          description:
            'Expense category, e.g. "food", "transport", "entertainment" (for add action)',
        },
        description: {
          type: "string",
          description: "Description of the expense (for add action)",
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
        case "add": {
          const amount = args.amount ? Number(args.amount) : NaN;
          const category = args.category ? String(args.category) : "";
          const description = args.description
            ? String(args.description)
            : "";

          if (!isFinite(amount) || amount <= 0) {
            return 'Error: Valid positive "amount" is required.';
          }
          if (!category) {
            return 'Error: "category" is required for add action.';
          }

          const data = loadData(dataPath);
          const now = new Date();
          data.expenses.push({
            amount,
            category: category.toLowerCase(),
            description,
            date: now.toISOString().split("T")[0]!,
            timestamp: now.toISOString(),
          });
          saveData(dataPath, data);
          return `Expense added: $${amount.toFixed(2)} in "${category}"${description ? ` — ${description}` : ""}`;
        }

        case "summary": {
          const data = loadData(dataPath);
          const now = new Date();
          const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

          const monthExpenses = data.expenses.filter((e) =>
            e.date.startsWith(currentMonth),
          );

          if (monthExpenses.length === 0) {
            return `No expenses recorded for ${currentMonth}.`;
          }

          const byCategory: Record<string, number> = {};
          let total = 0;
          for (const e of monthExpenses) {
            byCategory[e.category] =
              (byCategory[e.category] ?? 0) + e.amount;
            total += e.amount;
          }

          let result = `Expense Summary for ${currentMonth}:\n${"=".repeat(35)}\n`;
          const sorted = Object.entries(byCategory).sort(
            (a, b) => b[1] - a[1],
          );
          for (const [cat, amt] of sorted) {
            const pct = ((amt / total) * 100).toFixed(1);
            result += `  ${cat.padEnd(18)} $${amt.toFixed(2).padStart(8)} (${pct}%)\n`;
          }
          result += `${"─".repeat(35)}\n`;
          result += `  ${"Total".padEnd(18)} $${total.toFixed(2).padStart(8)}`;

          return result;
        }

        case "list": {
          const data = loadData(dataPath);
          const recent = data.expenses.slice(-15).reverse();

          if (recent.length === 0) {
            return "No expenses recorded yet.";
          }

          let result = "Recent Expenses:\n";
          for (const e of recent) {
            result += `  ${e.date} | $${e.amount.toFixed(2).padStart(8)} | ${e.category}${e.description ? ` — ${e.description}` : ""}\n`;
          }
          return result;
        }

        case "export": {
          const data = loadData(dataPath);
          if (data.expenses.length === 0) {
            return "No expenses to export.";
          }

          const rows = ["date,amount,category,description"];
          for (const e of data.expenses) {
            const desc = e.description.includes(",")
              ? `"${e.description.replace(/"/g, '""')}"`
              : e.description;
            rows.push(`${e.date},${e.amount},${e.category},${desc}`);
          }
          return rows.join("\n");
        }

        default:
          return `Error: Unknown action "${action}". Use: add, summary, list, or export.`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error with expense tracker: ${msg}`;
    }
  },
};
