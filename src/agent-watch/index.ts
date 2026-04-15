/**
 * StackOwl — Agent Watch: Manager
 *
 * Public API. Wires together all components and exposes:
 *   - start() / stop()
 *   - handleTelegramReply() — called when user replies YES/NO in Telegram
 *   - registerUser() — called when user says "watch my claude code"
 *   - getStatus() — for status commands
 */

import express from "express";
import { randomBytes } from "node:crypto";
import type { Server } from "node:http";
import { SessionRegistry } from "./session-registry.js";
import { QuestionQueue } from "./question-queue.js";
import { RiskClassifier } from "./risk-classifier.js";
import { Relay } from "./relay.js";
import { mountAgentWatchRoutes, buildSettingsSnippet, AGENT_WATCH_PORT } from "./server.js";
import { parseAnswer } from "./answer-parser.js";
import { formatWatchStarted, formatSessionSummary, type AgentType } from "./formatters/telegram.js";
import { OpenCodeAdapter, type OpenCodeDiagnosis } from "./adapters/opencode.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface AgentWatchConfig {
  port?: number;
  /** Called to send Telegram messages to users */
  sendToUser: (userId: string, channelId: string, html: string) => Promise<void>;
}

export interface WatchRegistration {
  token: string;
  agentType: AgentType;
  /** Pre-formatted Telegram message to send the user */
  telegramMessage: string;
  /** The JSON snippet to paste into ~/.claude/settings.json (Claude Code only) */
  settingsSnippet?: string;
}

// ─── AgentWatchManager ────────────────────────────────────────────

export class AgentWatchManager {
  private registry = new SessionRegistry();
  private queue = new QuestionQueue();
  private classifier = new RiskClassifier();
  private relay: Relay;
  private server: Server | null = null;
  private port: number;
  /** Active OpenCode adapters keyed by userId */
  private openCodeAdapters = new Map<string, OpenCodeAdapter>();

  constructor(private config: AgentWatchConfig) {
    this.port = config.port ?? AGENT_WATCH_PORT;

    this.relay = new Relay(
      this.registry,
      this.queue,
      this.classifier,
      config.sendToUser,
    );
  }

  // ─── Lifecycle ───────────────────────────────────────────────

  start(): void {
    if (this.server) return;

    const app = express();
    app.use(express.json());

    const router = express.Router();
    mountAgentWatchRoutes(router, this.registry, this.queue, this.relay);
    app.use("/agent-watch", router);

    this.server = app.listen(this.port, "127.0.0.1", () => {
      log.engine.info(`[AgentWatch] Listening on http://localhost:${this.port}/agent-watch`);
    });

    this.server.on("error", (err) => {
      log.engine.warn(`[AgentWatch] Server error: ${err.message}`);
    });
  }

  stop(): void {
    this.server?.close();
    this.server = null;
    log.engine.info("[AgentWatch] Stopped");
  }

  // ─── User Registration ───────────────────────────────────────

  /**
   * Register a user for agent supervision.
   * Detects agent type from the command text and returns appropriate instructions.
   *
   * Called when user says "watch my claude code" or "watch my opencode" in Telegram.
   */
  async registerUser(
    userId: string,
    channelId: string,
    agentType: AgentType = "claude-code",
    port?: number,
  ): Promise<WatchRegistration> {
    const token = randomBytes(12).toString("hex");

    if (agentType === "opencode") {
      return this.registerOpenCode(userId, channelId, token, port);
    }
    return this.registerClaudeCode(userId, channelId, token);
  }

  private registerClaudeCode(
    userId: string,
    channelId: string,
    token: string,
  ): WatchRegistration {
    this.registry.registerToken(token, userId, channelId);
    const snippet = buildSettingsSnippet(token, this.port);
    const telegramMessage = formatWatchStarted(token, this.port, "claude-code");
    log.engine.info(`[AgentWatch] Claude Code registered for user ${userId}`);
    return { token, agentType: "claude-code", telegramMessage, settingsSnippet: snippet };
  }

  private async registerOpenCode(
    userId: string,
    channelId: string,
    token: string,
    port?: number,
  ): Promise<WatchRegistration> {
    // Stop any existing OpenCode adapter for this user first
    this.openCodeAdapters.get(userId)?.stop().catch(() => {});

    const adapter = new OpenCodeAdapter({
      sessionId: `opencode-${userId}-${Date.now()}`,
      port,
    });

    // ── Validate before claiming to watch ────────────────────────
    const diagnosis = await adapter.diagnose();
    if (!diagnosis.reachable) {
      return {
        token,
        agentType: "opencode",
        telegramMessage: buildOpenCodeErrorMessage(diagnosis),
      };
    }

    // ── Server is up — register and start ────────────────────────
    const sessionId = adapter.sessionId;
    this.registry.registerToken(token, userId, channelId);
    this.registry.getOrCreate(sessionId, token);

    adapter.start(async (question) => this.relay.process(question))
      .catch((err) => {
        log.engine.warn(
          `[AgentWatch] OpenCode adapter error: ${err instanceof Error ? err.message : err}`,
        );
        // Notify user that the connection dropped
        this.config.sendToUser(
          userId,
          channelId,
          `⚠️ Lost connection to OpenCode server. Say <b>watch my opencode</b> to reconnect.`,
        ).catch(() => {});
      });

    this.openCodeAdapters.set(userId, adapter);

    log.engine.info(`[AgentWatch] OpenCode watching for user ${userId}`);
    return {
      token,
      agentType: "opencode",
      telegramMessage: formatWatchStarted(token, this.port, "opencode"),
    };
  }

  // ─── Telegram Reply Handler ──────────────────────────────────

  /**
   * Called when a Telegram message arrives that might be an agent-watch reply.
   * Returns true if the message was consumed (it was a YES/NO for a pending question).
   * Returns false if it was unrelated (should be handled by normal gateway).
   */
  handleTelegramReply(userId: string, text: string): boolean {
    const parsed = parseAnswer(text);

    // Get all sessions for this user
    const sessions = this.registry.getForUser(userId);
    if (sessions.length === 0) return false;

    if (parsed.type === "unknown") return false;

    if (parsed.type === "single") {
      // Direct ID answer: "yes abc1" — try all sessions
      const resolved = this.queue.answer(parsed.questionId, parsed.decision);
      return resolved;
    }

    if (parsed.type === "session_all") {
      // Session rule: "yes all Bash"
      // Apply to all sessions for this user (usually just one)
      let applied = false;
      for (const s of sessions) {
        this.relay.applySessionRule(s.agentSessionId, parsed.toolName, parsed.decision);
        applied = true;
      }
      return applied;
    }

    if (parsed.type === "ambiguous") {
      // Bare "yes" or "no" — only handle if exactly one pending question across all user sessions
      const allPending = sessions.flatMap((s) =>
        this.queue.getForSession(s.agentSessionId),
      );
      if (allPending.length === 1) {
        return this.queue.answer(allPending[0].id, parsed.decision);
      }
      // Ambiguous — more than one pending question, need an ID
      return false;
    }

    return false;
  }

  // ─── Status ──────────────────────────────────────────────────

  getStatus(): {
    running: boolean;
    activeSessions: number;
    pendingQuestions: number;
  } {
    return {
      running: this.server !== null,
      activeSessions: this.registry.getAllSessions().length,
      pendingQuestions: this.queue.size,
    };
  }

  /**
   * Called when a session ends (e.g., Claude Code exits).
   * Sends the session summary to the user via Telegram.
   */
  async endSession(agentSessionId: string): Promise<void> {
    this.queue.cancelSession(agentSessionId, "deny");
    const session = this.registry.remove(agentSessionId);
    if (!session) return;

    const durationMs = Date.now() - session.startedAt;
    const summary = formatSessionSummary(agentSessionId, session.stats, durationMs);

    await this.config.sendToUser(session.userId, session.channelId, summary).catch(
      () => {},
    );
  }

  /**
   * Stop watching all sessions for a user.
   * Called when user says "unwatch" in Telegram.
   */
  async unwatchUser(userId: string): Promise<number> {
    // Stop OpenCode adapter if running
    const ocAdapter = this.openCodeAdapters.get(userId);
    if (ocAdapter) {
      await ocAdapter.stop().catch(() => {});
      this.openCodeAdapters.delete(userId);
    }

    const sessions = this.registry.getForUser(userId);
    for (const s of sessions) {
      await this.endSession(s.agentSessionId);
    }
    return sessions.length + (ocAdapter ? 1 : 0);
  }

  /** Add a user-defined risk override. Called from Telegram commands. */
  addUserRule(userId: string, toolPattern: string, decision: "low" | "medium" | "high"): void {
    this.classifier.addUserRule(toolPattern, decision);
    log.engine.info(
      `[AgentWatch] User ${userId} added rule: ${toolPattern} → ${decision}`,
    );
  }
}

// ─── Smart Error Builder ──────────────────────────────────────────

function buildOpenCodeErrorMessage(d: OpenCodeDiagnosis): string {
  const lines: string[] = [];

  // ── Headline based on what we know ───────────────────────────
  if (!d.installed) {
    lines.push(`❌ <b>OpenCode is not installed</b>`);
    lines.push(``);
    lines.push(`I couldn't find the <code>opencode</code> command on your system.`);
    lines.push(``);
    lines.push(`<b>To install OpenCode:</b>`);
    lines.push(`1. Run: <code>npm install -g opencode-ai</code>`);
    lines.push(`   or: <code>curl -fsSL https://opencode.ai/install | bash</code>`);
    lines.push(`2. Then run <code>opencode</code> in your project directory`);
    lines.push(`3. Say <b>watch my opencode</b> again`);
    return lines.join("\n");
  }

  if (d.reason === "timeout") {
    lines.push(`⏱ <b>OpenCode server timed out</b>`);
    lines.push(``);
    lines.push(`OpenCode is installed but the server on port <code>${d.port}</code> didn't respond in time.`);
    lines.push(``);
    lines.push(`<b>Possible causes:</b>`);
    lines.push(`• OpenCode is starting up — wait a few seconds and try again`);
    lines.push(`• A firewall is blocking port ${d.port}`);
    lines.push(`• OpenCode crashed — check your terminal for errors`);
  } else {
    // not_running or unknown
    lines.push(`❌ <b>OpenCode server is not running</b>`);
    lines.push(``);
    lines.push(`Nothing is listening on <code>http://localhost:${d.port}</code>.`);
  }

  // ── Did we find it on another port? ──────────────────────────
  if (d.altPort) {
    lines.push(``);
    lines.push(`💡 <b>I found something running on port ${d.altPort}.</b>`);
    lines.push(`If that's your OpenCode instance, say:`);
    lines.push(`<code>watch my opencode port ${d.altPort}</code>`);
  }

  // ── How to fix ───────────────────────────────────────────────
  lines.push(``);
  lines.push(`<b>To start OpenCode:</b>`);
  lines.push(`1. Open a terminal in your project directory`);
  lines.push(`2. Run: <code>opencode</code>`);
  lines.push(`3. Wait for it to start (you'll see a TUI interface)`);
  lines.push(`4. Come back here and say <b>watch my opencode</b>`);

  if (d.reason === "not_running") {
    lines.push(``);
    lines.push(`<i>OpenCode starts its HTTP server automatically on port 4096 when you run it.</i>`);
  }

  return lines.join("\n");
}
