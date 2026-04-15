/**
 * StackOwl — Agent Watch: Claude Code Hooks Adapter
 *
 * Receives PreToolUse / PermissionRequest hook payloads from Claude Code
 * via HTTP POST to /agent-watch/hook.
 *
 * The hook is configured in ~/.claude/settings.json as a command hook using
 * curl, which holds the connection open for up to 580 seconds (command hook
 * default timeout is 600s). StackOwl holds the Express request pending until
 * the user replies via Telegram — this is standard HTTP long-polling.
 *
 * Hook response formats (exact Claude Code schemas):
 *
 *   PreToolUse allow:
 *     { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow" } }
 *
 *   PreToolUse deny:
 *     { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny",
 *                             permissionDecisionReason: "Denied by StackOwl supervisor" } }
 *
 *   PermissionRequest allow:
 *     { hookSpecificOutput: { hookEventName: "PermissionRequest",
 *                             decision: { behavior: "allow" } } }
 *
 *   PermissionRequest deny:
 *     { hookSpecificOutput: { hookEventName: "PermissionRequest",
 *                             decision: { behavior: "deny", message: "Denied via Telegram" } } }
 */

import type { Request, Response } from "express";
import type { AgentQuestion, Decision } from "./base.js";
import { RiskClassifier } from "../risk-classifier.js";
import { randomBytes } from "node:crypto";

// ─── Hook Payload Types ───────────────────────────────────────────

export interface ClaudeHookPayload {
  session_id: string;
  transcript_path?: string;
  cwd?: string;
  hook_event_name: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_use_id?: string;
  permission_suggestions?: unknown[];
  permission_mode?: string;
}

// ─── Response Builders ────────────────────────────────────────────

export function buildPreToolUseResponse(decision: Decision, reason?: string): object {
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: decision === "allow" ? "allow" : "deny",
      ...(decision === "deny"
        ? { permissionDecisionReason: reason ?? "Denied by StackOwl supervisor via Telegram" }
        : {}),
    },
  };
}

export function buildPermissionRequestResponse(decision: Decision, reason?: string): object {
  return {
    hookSpecificOutput: {
      hookEventName: "PermissionRequest",
      decision: {
        behavior: decision,
        ...(decision === "deny"
          ? { message: reason ?? "Denied by StackOwl supervisor via Telegram" }
          : {}),
      },
    },
  };
}

// ─── Request Handler ──────────────────────────────────────────────

/**
 * Parses an incoming Claude Code hook POST request and turns it into
 * an AgentQuestion. Returns null if the payload is malformed.
 */
export function parseHookRequest(
  req: Request,
): { question: AgentQuestion; eventName: string } | null {
  const body = req.body as Partial<ClaudeHookPayload>;

  if (!body?.session_id || !body?.tool_name || !body?.hook_event_name) {
    return null;
  }

  const classifier = new RiskClassifier();
  const { risk } = classifier.classify(
    body.tool_name,
    body.tool_input ?? {},
  );

  const question: AgentQuestion = {
    id: generateQuestionId(),
    sessionId: body.session_id,
    toolName: body.tool_name,
    toolInput: body.tool_input ?? {},
    risk,
    receivedAt: Date.now(),
    raw: body as Record<string, unknown>,
  };

  return { question, eventName: body.hook_event_name };
}

/**
 * Send the final decision as a JSON response that Claude Code understands.
 */
export function sendHookResponse(
  res: Response,
  eventName: string,
  decision: Decision,
  reason?: string,
): void {
  let body: object;

  if (eventName === "PermissionRequest") {
    body = buildPermissionRequestResponse(decision, reason);
  } else {
    // PreToolUse and everything else
    body = buildPreToolUseResponse(decision, reason);
  }

  res.status(200).json(body);
}

// ─── ID Generator ─────────────────────────────────────────────────

/** Generate a short 4-char alphanumeric question ID */
function generateQuestionId(): string {
  return randomBytes(3).toString("hex").slice(0, 4);
}
