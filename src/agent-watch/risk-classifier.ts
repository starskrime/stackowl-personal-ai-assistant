/**
 * StackOwl — Agent Watch: Risk Classifier
 *
 * Classifies each tool call as low / medium / high risk.
 * Low  → auto-approve silently
 * Medium → notify user but auto-approve after timeout if no reply
 * High → always wait for human, no auto-approve
 *
 * Rules are checked top-down. First match wins.
 * Custom user rules (from Telegram) override defaults.
 */

import type { RiskLevel } from "./adapters/base.js";

// ─── Rule Definition ──────────────────────────────────────────────

interface RiskRule {
  /** Tool name pattern (exact string or regex string) */
  tool: string | RegExp;
  /** Optional: match against tool input values */
  inputPattern?: RegExp;
  risk: RiskLevel;
  reason: string;
}

// ─── Default Rules ────────────────────────────────────────────────

const DEFAULT_RULES: RiskRule[] = [
  // ── Always high risk ─────────────────────────────────────────
  {
    tool: "Bash",
    inputPattern: /rm\s+-rf|sudo\s+rm|rimraf|del\s+\/[sqf]/i,
    risk: "high",
    reason: "Destructive delete command",
  },
  {
    tool: "Bash",
    inputPattern: /git\s+push|kubectl\s+apply|terraform\s+apply|docker\s+push/i,
    risk: "high",
    reason: "Deploy or publish operation",
  },
  {
    tool: "Bash",
    inputPattern: /curl|wget|fetch.*https?:/i,
    risk: "high",
    reason: "External network request",
  },
  {
    tool: /Write|Edit/,
    inputPattern: /\.env|secrets?|credentials?|\.pem|\.key|id_rsa/i,
    risk: "high",
    reason: "Writing to sensitive/secret file",
  },
  {
    tool: "Bash",
    inputPattern: /git\s+reset\s+--hard|git\s+clean\s+-f/i,
    risk: "high",
    reason: "Destructive git operation",
  },

  // ── Medium risk ───────────────────────────────────────────────
  {
    tool: "Bash",
    inputPattern: /npm\s+install|pip\s+install|yarn\s+add|pnpm\s+add/i,
    risk: "medium",
    reason: "Package installation",
  },
  {
    tool: "Bash",
    inputPattern: /git\s+commit|git\s+add|git\s+merge/i,
    risk: "medium",
    reason: "Git write operation",
  },
  {
    tool: /Write|Create/,
    risk: "medium",
    reason: "Writing or creating files",
  },
  {
    tool: "Edit",
    risk: "medium",
    reason: "Editing existing files",
  },
  {
    tool: "Bash",
    inputPattern: /npm\s+run|npx\s+/i,
    risk: "medium",
    reason: "Running scripts",
  },

  // ── Low risk (auto-approve) ───────────────────────────────────
  {
    tool: /Read|Cat|Head|Tail/,
    risk: "low",
    reason: "Reading files",
  },
  {
    tool: "Bash",
    inputPattern: /^(ls|find|grep|rg|cat|head|tail|wc|echo|pwd|which|type|file)\b/,
    risk: "low",
    reason: "Read-only shell command",
  },
  {
    tool: "Bash",
    inputPattern: /npm\s+(test|run\s+test|run\s+lint|run\s+build)|vitest|jest|pytest/i,
    risk: "low",
    reason: "Running tests or linting",
  },
  {
    tool: "Bash",
    inputPattern: /git\s+(status|log|diff|show|branch|stash\s+list)/i,
    risk: "low",
    reason: "Read-only git command",
  },
  {
    tool: /Glob|Grep|Search/,
    risk: "low",
    reason: "Search operation",
  },

  // ── Default fallback ─────────────────────────────────────────
  {
    tool: "Bash",
    risk: "medium",
    reason: "General shell command",
  },
];

// ─── Classifier ───────────────────────────────────────────────────

export class RiskClassifier {
  /** User-defined overrides: "always approve [tool]" or "always ask for [tool]" */
  private userRules: RiskRule[] = [];

  classify(toolName: string, toolInput: Record<string, unknown>): {
    risk: RiskLevel;
    reason: string;
  } {
    const inputText = JSON.stringify(toolInput).toLowerCase();

    // Check user rules first
    for (const rule of [...this.userRules, ...DEFAULT_RULES]) {
      if (!this.matchesTool(rule.tool, toolName)) continue;
      if (rule.inputPattern && !rule.inputPattern.test(inputText)) continue;
      return { risk: rule.risk, reason: rule.reason };
    }

    // Unknown tool — treat as medium
    return { risk: "medium", reason: "Unknown tool" };
  }

  /** Add a user override rule. */
  addUserRule(toolPattern: string, risk: RiskLevel): void {
    this.userRules.unshift({
      tool: toolPattern,
      risk,
      reason: `User rule: ${toolPattern} → ${risk}`,
    });
  }

  private matchesTool(pattern: string | RegExp, toolName: string): boolean {
    if (typeof pattern === "string") return pattern === toolName;
    return pattern.test(toolName);
  }
}
