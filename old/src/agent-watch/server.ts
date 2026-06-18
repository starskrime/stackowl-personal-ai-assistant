/**
 * StackOwl — Agent Watch: HTTP Server
 *
 * Express routes for the agent supervision webhook.
 *
 * POST /agent-watch/hook
 *   Receives PreToolUse / PermissionRequest hook payloads from Claude Code.
 *   Long-polls until the user decides via Telegram (up to 580s — within
 *   Claude Code's 600s command hook timeout).
 *
 * POST /agent-watch/register
 *   Registers a watch token for a user. Returns the settings.json snippet
 *   the user needs to paste into their Claude Code config.
 *
 * GET /agent-watch/status
 *   Returns active sessions and pending questions.
 *
 * DELETE /agent-watch/session/:sessionId
 *   Stops watching a session.
 */

import type { Router, Request, Response } from "express";
import { randomBytes } from "node:crypto";
import type { SessionRegistry } from "./session-registry.js";
import type { QuestionQueue } from "./question-queue.js";
import type { Relay } from "./relay.js";
import {
  parseHookRequest,
  sendHookResponse,
} from "./adapters/claude-code-hooks.js";
import { log } from "../logger.js";

export const AGENT_WATCH_PORT = 3111;

export function mountAgentWatchRoutes(
  router: Router,
  registry: SessionRegistry,
  queue: QuestionQueue,
  relay: Relay,
): void {
  // ── POST /agent-watch/hook ─────────────────────────────────────
  // Receives hook from Claude Code. Holds connection open (long-poll).
  router.post("/hook", async (req: Request, res: Response) => {
    const token = (req.headers["x-watch-token"] as string) ?? "";
    if (!token) {
      res.status(401).json({ error: "Missing X-Watch-Token header" });
      return;
    }

    const parsed = parseHookRequest(req);
    if (!parsed) {
      res.status(400).json({ error: "Invalid hook payload" });
      return;
    }

    const { question, eventName } = parsed;

    // Ensure session is registered under this token
    const session = registry.getOrCreate(question.sessionId, token);
    if (!session) {
      // Unknown token — pass through (don't block Claude Code)
      res.status(200).json({ hookSpecificOutput: { hookEventName: eventName, permissionDecision: "allow" } });
      return;
    }

    log.engine.info(
      `[AgentWatch] Hook: ${eventName} | tool=${question.toolName} | risk=${question.risk} | session=${question.sessionId.slice(0, 8)}`,
    );

    try {
      // Await user decision (long-poll — may wait minutes)
      const decision = await relay.process(question);
      sendHookResponse(res, eventName, decision);
    } catch (err) {
      log.engine.warn(
        `[AgentWatch] Hook processing error: ${err instanceof Error ? err.message : err}`,
      );
      // On error, default to allow so Claude Code is not permanently blocked
      sendHookResponse(res, eventName, "allow", "StackOwl error — defaulting to allow");
    }
  });

  // ── POST /agent-watch/register ─────────────────────────────────
  // Register a user for a new watch session. Returns their config snippet.
  router.post("/register", (req: Request, res: Response) => {
    const { userId, channelId } = req.body as {
      userId?: string;
      channelId?: string;
    };

    if (!userId || !channelId) {
      res.status(400).json({ error: "userId and channelId required" });
      return;
    }

    const token = randomBytes(12).toString("hex");
    registry.registerToken(token, userId, channelId);

    const snippet = buildSettingsSnippet(token, AGENT_WATCH_PORT);

    res.json({ token, snippet });
  });

  // ── GET /agent-watch/status ────────────────────────────────────
  router.get("/status", (_req: Request, res: Response) => {
    const sessions = registry.getAllSessions().map((s) => ({
      sessionId: s.agentSessionId,
      userId: s.userId,
      pendingQuestions: queue.getForSession(s.agentSessionId).length,
      stats: s.stats,
      durationMs: Date.now() - s.startedAt,
    }));
    res.json({ sessions, totalPending: queue.size });
  });

  // ── DELETE /agent-watch/session/:sessionId ─────────────────────
  router.delete("/session/:sessionId", (req: Request, res: Response) => {
    const sessionId = String(req.params["sessionId"] ?? "");
    queue.cancelSession(sessionId, "deny");
    const removed = registry.remove(String(sessionId));
    if (removed) {
      res.json({ ok: true, stats: removed.stats });
    } else {
      res.status(404).json({ error: "Session not found" });
    }
  });
}

// ─── Settings Snippet Builder ─────────────────────────────────────

export function buildSettingsSnippet(token: string, port: number): string {
  return JSON.stringify(
    {
      hooks: {
        PreToolUse: [
          {
            hooks: [
              {
                type: "command",
                command: `curl -sX POST http://localhost:${port}/agent-watch/hook -H "X-Watch-Token: ${token}" -H "Content-Type: application/json" -d @- --max-time 580`,
                timeout: 590,
              },
            ],
          },
        ],
      },
    },
    null,
    2,
  );
}
