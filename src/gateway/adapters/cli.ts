// src/gateway/adapters/cli.ts
/**
 * StackOwl — CLI Channel Adapter
 *
 * Pure transport layer. All rendering lives in TerminalRenderer.
 * Responsibilities:
 *   - Normalize user input → GatewayMessage
 *   - Pass GatewayResponse → renderer
 *   - Implement ChannelAdapter interface
 */

import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import { log } from "../../logger.js";
import { TerminalRenderer } from "../../cli/renderer.js";
import { CommandRegistry } from "../../cli/commands.js";
import { OnboardingFlow } from "../../cli/onboarding-flow.js";
import { resolve } from "node:path";
import { homedir } from "node:os";
import type { ChannelAdapter, GatewayResponse } from "../types.js";
import { SessionPersistence } from "../../cli/session-persistence.js";
import { StructuredOutputManager, isJsonModeEnabled } from "../../cli/structured-output.js";
import { ThinkingSuppressor } from "../../cli/thinking-suppressor.js";
import { ToolStream } from "../../cli/tool-stream.js";
import type { StreamEvent } from "../../providers/base.js";

export interface CLIAdapterConfig { userId?: string; workspacePath?: string; }

export class CLIAdapter implements ChannelAdapter {
  readonly id   = "cli";
  readonly name = "CLI";

  private userId:    string;
  private sessionId: string;
  private renderer:  TerminalRenderer;
  private commands:  CommandRegistry;

  private queue:      string[] = [];
  private processing = false;
  private _shuttingDown = false;
  private _onboarding: OnboardingFlow | null = null;

  // ─── Epic 8: CLI Modules ───────────────────────────────────────
  private sessionPersistence?: SessionPersistence;
  private structuredOutput?: StructuredOutputManager;
  private thinkingSuppressor?: ThinkingSuppressor;
  private toolStream?: ToolStream;
  private jsonMode = false;

  constructor(private gateway: OwlGateway, config: CLIAdapterConfig = {}) {
    this.userId    = config.userId ?? "local";
    this.sessionId = makeSessionId(this.id, this.userId);
    this.renderer  = new TerminalRenderer();
    this.commands  = new CommandRegistry();
    this.renderer.setCommandList(this.commands.listNames());

    // ─── Epic 8: Wire CLI modules ────────────────────────────────
    // SessionPersistence — auto-load session on startup, auto-save on messages
    if (config.workspacePath) {
      this.sessionPersistence = new SessionPersistence({ workspacePath: config.workspacePath });
      this.sessionPersistence.startSession(this.sessionId, this.gateway.getOwl().persona.name)
        .catch((err) => log.engine.warn(`[SessionPersistence] Failed to load session: ${err instanceof Error ? err.message : String(err)}`));
    }

    // StructuredOutput — detect --json flag and suppress TUI
    this.structuredOutput = new StructuredOutputManager();
    this.jsonMode = isJsonModeEnabled();

    // ThinkingSuppressor — wire to output filtering
    this.thinkingSuppressor = new ThinkingSuppressor();

    // ToolStream — wire to real-time tool streaming
    this.toolStream = new ToolStream({
      onToolStart: (toolName, _toolCallId) => {
        log.tool.toolCall(toolName);
      },
      onToolEnd: (toolName, _toolCallId, success, elapsedMs) => {
        log.tool.toolResult(toolName, `completed in ${elapsedMs}ms`, success);
      },
      onToolError: (toolName, _toolCallId, error) => {
        log.tool.warn(`Tool ${toolName} error: ${error}`);
      },
    });
  }

  // ─── ChannelAdapter ───────────────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    if (this.jsonMode) {
      const usage = response.usage ? {
        promptTokens: response.usage.promptTokens,
        completionTokens: response.usage.completionTokens,
        totalTokens: (response.usage.promptTokens ?? 0) + (response.usage.completionTokens ?? 0),
      } : undefined;
      this.structuredOutput?.print(this.structuredOutput.success(response.content, {
        owlName: response.owlName,
        usage,
      }));
      return;
    }
    this.renderer.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    if (this.jsonMode) {
      const usage = response.usage ? {
        promptTokens: response.usage.promptTokens,
        completionTokens: response.usage.completionTokens,
        totalTokens: (response.usage.promptTokens ?? 0) + (response.usage.completionTokens ?? 0),
      } : undefined;
      this.structuredOutput?.print(this.structuredOutput.success(response.content, {
        owlName: response.owlName,
        usage,
      }));
      return;
    }
    this.renderer.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async deliverFile(_userId: string, filePath: string, caption?: string): Promise<void> {
    if (this.jsonMode) {
      this.structuredOutput?.print(this.structuredOutput.success(`File ready: ${filePath}${caption ? " — " + caption : ""}`));
      return;
    }
    this.renderer.printInfo(`File ready: ${filePath}${caption ? " — " + caption : ""}`);
  }

  async start(): Promise<void> {
    // In JSON mode, skip TUI entirely
    if (this.jsonMode) {
      console.log(JSON.stringify({ status: "ok", mode: "json", timestamp: new Date().toISOString() }));
      return;
    }

    const owl    = this.gateway.getOwl();
    const config = this.gateway.getConfig();
    const traits = owl.dna.evolvedTraits;
    const challengeNum = typeof traits.challengeLevel === "number"
      ? traits.challengeLevel
      : parseInt(String(traits.challengeLevel), 10) || 5;

    this.renderer.setOwl(owl.persona.emoji, owl.persona.name, config.defaultProvider, config.defaultModel);
    this.renderer.updateDNA({ challenge: challengeNum, verbosity: (traits as any).verbosity ?? 5, mood: 7 });
    this.renderer.setRecentSessions([]);

    this._wireRenderer();
    await this._showHome(owl, config, challengeNum);

    await new Promise<void>(res => {
      this.renderer.once("quit", res);
      process.once("_stackowlStop", res as () => void);
    });
  }

  stop(): void {
    if (this.jsonMode) return;
    this.renderer.close();
    // ─── Epic 8: Save session on shutdown ───────────────────────
    this.sessionPersistence?.endSession().catch((err) =>
      log.engine.warn(`[SessionPersistence] Failed to save session on shutdown: ${err instanceof Error ? err.message : String(err)}`)
    );
  }

  // ─── Home → Session transition ────────────────────────────────

  private _showHome(
    owl:          ReturnType<OwlGateway["getOwl"]>,
    _config:      ReturnType<OwlGateway["getConfig"]>,
    challengeNum: number,
  ): Promise<void> {
    return new Promise(resolve => {
      // Populate left-panel home state
      (this.renderer as any)._state.generation = owl.dna.generation;
      (this.renderer as any)._state.challenge  = challengeNum;
      (this.renderer as any)._state.skills     =
        this.gateway.getSkillsLoader?.()?.getRegistry().listEnabled().length ?? 0;

      this.renderer.enter();

      // First "line" event transitions to session mode
      const onActivate = (input: string) => {
        this.renderer.setMode("session");
        if (input) {
          this.renderer.showUserMessage(input);
          this.queue.push(input);
          this._drain();
        }
        resolve();
      };
      this.renderer.input.once("line", onActivate);
    });
  }

  // ─── Wire renderer events ─────────────────────────────────────

  private _wireRenderer(): void {
    this.renderer.input.on("line", (input: string) => {
      if ((this.renderer as any)._state.mode !== "session") return; // handled by _showHome
      if (!this._onboarding) this.renderer.showUserMessage(input);
      this.queue.push(input);
      this._drain();
    });
    this.renderer.on("quit", async () => { await this._gracefulShutdown(); });
    this.renderer.input.on("quit", async () => { await this._gracefulShutdown(); });
    this.renderer.on("onboarding", () => {
      this._onboarding = new OnboardingFlow(resolve(homedir(), ".stackowl", "stackowl.config.json"));
      this._onboarding.start(this.renderer);
    });
  }

  // ─── Queue ────────────────────────────────────────────────────

  private _drain(): void {
    if (this.processing || this.queue.length === 0) return;
    const input = this.queue.shift()!;
    this.processing = true;
    this._processLine(input).finally(() => { this.processing = false; this._drain(); });
  }

  private async _processLine(input: string): Promise<void> {
    if (this._onboarding) {
      const done = await this._onboarding.handleInput(input, this.renderer);
      if (done) this._onboarding = null;
      return;
    }

    const consumed = await this.commands.handle(input, this.renderer, this.gateway);
    if (consumed) return;

    // ─── Epic 8: Add user message to session persistence ──────────
    this.sessionPersistence?.addMessage("user", input);
    this.sessionPersistence?.incrementTurn();

    try {
      log.cli.incoming(this.userId, input);
      (this.gateway as any).getCognitiveLoop?.()?.notifyUserActivity?.();
      this.renderer.showThinking();

      const { handler, didStream } = this.renderer.createStreamHandler();

      // ─── Epic 8: Wire ToolStream to stream events ───────────────
      const toolStreamHandler = this.toolStream?.createStreamHandler();
      const suppressedHandler = this.thinkingSuppressor?.createSuppressedCallback(
        async (event: StreamEvent) => {
          // Forward to tool stream if available
          if (toolStreamHandler) {
            await toolStreamHandler(event);
          }
          // Forward to original renderer handler
          await handler(event);
        }
      ) ?? handler;

      const response = await this.gateway.handle(
        { id: makeMessageId(), channelId: this.id, userId: this.userId, sessionId: this.sessionId, text: input },
        {
          onProgress: this.thinkingSuppressor?.shouldSuppressProgress()
            ? async () => { /* suppress progress in full mode */ }
            : async (msg: string) => { log.engine.debug(`[progress] ${msg}`); },
          askInstall: async (deps: string[]) => {
            this.renderer.stopThinking();
            this.renderer.printInfo(`📦 Install ${deps.join(" ")}? [y/n]`);
            return this.renderer.input.promptYesNo();
          },
          onStreamEvent: suppressedHandler,
        },
      );

      log.cli.outgoing(this.userId, response.content);

      // ─── Epic 8: Add assistant response to session persistence ───
      this.sessionPersistence?.addMessage("assistant", response.content, response.owlName);

      if (!didStream()) {
        this.renderer.stopThinking();
        this.renderer.showResponse(response);
      }
      if (response.usage) {
        this.renderer.updateStats(
          (response.usage.promptTokens ?? 0) + (response.usage.completionTokens ?? 0), 0,
        );
      }
    } catch (err) {
      this.renderer.stopThinking();
      const msg = err instanceof Error ? err.message : String(err);
      log.cli.error(`Error: ${msg}`);
      this.renderer.printError(msg);
    }
  }

  private async _gracefulShutdown(): Promise<void> {
    if (this._shuttingDown) return;
    this._shuttingDown = true;
    this.renderer.close();
    process.exit(0);
  }
}
