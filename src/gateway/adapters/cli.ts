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

export interface CLIAdapterConfig { userId?: string; }

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

  constructor(private gateway: OwlGateway, config: CLIAdapterConfig = {}) {
    this.userId    = config.userId ?? "local";
    this.sessionId = makeSessionId(this.id, this.userId);
    this.renderer  = new TerminalRenderer();
    this.commands  = new CommandRegistry();
    this.renderer.setCommandList(this.commands.listNames());
  }

  // ─── ChannelAdapter ───────────────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this.renderer.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this.renderer.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async deliverFile(_userId: string, filePath: string, caption?: string): Promise<void> {
    this.renderer.printInfo(`File ready: ${filePath}${caption ? " — " + caption : ""}`);
  }

  async start(): Promise<void> {
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

  stop(): void { this.renderer.close(); }

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

    try {
      log.cli.incoming(this.userId, input);
      (this.gateway as any).getCognitiveLoop?.()?.notifyUserActivity?.();
      this.renderer.showThinking();

      const { handler, didStream } = this.renderer.createStreamHandler();

      const response = await this.gateway.handle(
        { id: makeMessageId(), channelId: this.id, userId: this.userId, sessionId: this.sessionId, text: input },
        {
          onProgress: async (msg: string) => { log.engine.debug(`[progress] ${msg}`); },
          askInstall: async (deps: string[]) => {
            this.renderer.stopThinking();
            this.renderer.printInfo(`📦 Install ${deps.join(" ")}? [y/n]`);
            return this.renderer.input.promptYesNo();
          },
          onStreamEvent: handler,
        },
      );

      log.cli.outgoing(this.userId, response.content);
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
