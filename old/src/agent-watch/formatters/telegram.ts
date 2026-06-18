/**
 * StackOwl — Agent Watch: Telegram Formatter
 *
 * Formats agent questions into readable Telegram messages.
 * Formats user replies into decisions.
 */

import type { AgentQuestion, Decision, RiskLevel } from "../adapters/base.js";

// ─── Risk Visuals ─────────────────────────────────────────────────

const RISK_EMOJI: Record<RiskLevel, string> = {
  low: "🟢",
  medium: "🟡",
  high: "🔴",
};

const RISK_LABEL: Record<RiskLevel, string> = {
  low: "Low",
  medium: "Medium",
  high: "HIGH",
};

// ─── Question Formatter ───────────────────────────────────────────

export function formatQuestion(q: AgentQuestion, riskReason: string): string {
  const riskEmoji = RISK_EMOJI[q.risk];
  const riskLabel = RISK_LABEL[q.risk];

  // Build a human-readable summary of what the tool will do
  const inputSummary = summarizeInput(q.toolName, q.toolInput);

  const timeoutNote =
    q.risk === "medium"
      ? "\n⏱ Auto-approved in 5 min if no reply."
      : q.risk === "high"
        ? "\n⏱ Auto-denied in 10 min if no reply."
        : "";

  return [
    `🤖 <b>Agent needs your decision</b>`,
    ``,
    `🔧 Tool: <code>${q.toolName}</code>`,
    `📋 Action: ${inputSummary}`,
    `${riskEmoji} Risk: ${riskLabel} — ${riskReason}`,
    ``,
    `Reply with:`,
    `  <b>yes ${q.id}</b> — allow`,
    `  <b>no ${q.id}</b> — deny`,
    `  <b>yes all ${q.toolName}</b> — allow all ${q.toolName} this session`,
    `  <b>no all ${q.toolName}</b> — deny all ${q.toolName} this session`,
    timeoutNote,
  ]
    .filter((l) => l !== undefined)
    .join("\n");
}

/** Format the notification sent after an auto-decision */
export function formatAutoDecision(
  q: AgentQuestion,
  decision: Decision,
  reason: string,
): string {
  const emoji = decision === "allow" ? "✅" : "❌";
  return `${emoji} Auto-${decision === "allow" ? "approved" : "denied"}: <code>${q.toolName}</code>\n${reason}`;
}

export type AgentType = "claude-code" | "opencode" | "unknown";

/** Format session started message — adapts instructions to the agent type */
export function formatWatchStarted(
  token: string,
  port: number,
  agentType: AgentType = "claude-code",
): string {
  if (agentType === "opencode") {
    return formatOpenCodeWatchStarted(port);
  }
  return formatClaudeCodeWatchStarted(token, port);
}

function formatClaudeCodeWatchStarted(token: string, port: number): string {
  return [
    `👁 <b>Agent Watch activated — Claude Code</b>`,
    ``,
    `Add this to <code>~/.claude/settings.json</code>:`,
    ``,
    `<pre>`,
    `{`,
    `  "hooks": {`,
    `    "PreToolUse": [{`,
    `      "hooks": [{`,
    `        "type": "command",`,
    `        "command": "curl -sX POST http://localhost:${port}/agent-watch/hook -H 'X-Watch-Token: ${token}' -d @- --max-time 580"`,
    `      }]`,
    `    }]`,
    `  }`,
    `}`,
    `</pre>`,
    ``,
    `Then start Claude Code normally. I'll message you whenever it needs a decision.`,
    ``,
    `Say <b>unwatch</b> to stop.`,
  ].join("\n");
}

function formatOpenCodeWatchStarted(_port: number): string {
  return [
    `👁 <b>Agent Watch activated — OpenCode</b>`,
    ``,
    `I'm now subscribing to your running OpenCode session.`,
    ``,
    `Make sure OpenCode is running — it starts its HTTP server automatically on port <code>4096</code>.`,
    ``,
    `I'll message you here whenever OpenCode needs a decision. Reply:`,
    `  <b>yes</b> — allow`,
    `  <b>no</b> — deny`,
    ``,
    `If OpenCode uses a password, tell me:`,
    `<code>opencode password &lt;your-password&gt;</code>`,
    ``,
    `Say <b>unwatch</b> to stop.`,
    ``,
    `<i>StackOwl is monitoring http://localhost:4096</i>`,
  ].join("\n");
}

/** Format session summary when it ends */
export function formatSessionSummary(
  agentSessionId: string,
  stats: {
    approved: number;
    denied: number;
    autoApproved: number;
    autoDenied: number;
  },
  durationMs: number,
): string {
  const total =
    stats.approved + stats.denied + stats.autoApproved + stats.autoDenied;
  const durationMin = Math.round(durationMs / 60000);
  return [
    `📊 <b>Agent session ended</b>`,
    `Session: <code>${agentSessionId.slice(0, 8)}</code>`,
    `Duration: ${durationMin} min`,
    ``,
    `Decisions:`,
    `  ✅ You approved: ${stats.approved}`,
    `  ❌ You denied: ${stats.denied}`,
    `  🟢 Auto-approved: ${stats.autoApproved}`,
    `  🔴 Auto-denied: ${stats.autoDenied}`,
    `  📦 Total: ${total}`,
  ].join("\n");
}

// ─── Input Summarizer ─────────────────────────────────────────────

function summarizeInput(
  toolName: string,
  input: Record<string, unknown>,
): string {
  if (toolName === "Bash" || toolName === "Shell") {
    const cmd = input.command ?? input.cmd ?? input.script ?? "";
    return `<code>${String(cmd).slice(0, 200)}</code>`;
  }
  if (toolName === "Write" || toolName === "Edit" || toolName === "Create") {
    const path = input.file_path ?? input.path ?? input.filename ?? "";
    return `<code>${String(path).slice(0, 200)}</code>`;
  }
  if (toolName === "Read") {
    const path = input.file_path ?? input.path ?? "";
    return `<code>${String(path).slice(0, 200)}</code>`;
  }

  // Generic fallback: first non-empty string value
  const firstVal = Object.values(input).find((v) => typeof v === "string");
  if (firstVal) return `<code>${String(firstVal).slice(0, 200)}</code>`;

  return `<code>${JSON.stringify(input).slice(0, 200)}</code>`;
}
