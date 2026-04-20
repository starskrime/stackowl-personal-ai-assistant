/**
 * StackOwl — CLI Channel Adapter
 *
 * Lifecycle:
 *   1. start() → show HomeScreen (Screen 1)
 *   2. First keypress → HomeScreen.transition() → TerminalUI.enter() (Screen 2)
 *   3. TerminalUI emits "line" events → gateway → responses back to UI
 */

import { resolve }                              from "node:path";
import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import { log }                                  from "../../logger.js";
import { TerminalUI }                           from "../../cli/ui.js";
import { HomeScreen }                           from "../../cli/home.js";
import { CommandRegistry }                      from "../../cli/commands.js";
import { OnboardingFlow }                       from "../../cli/onboarding-flow.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

export interface CLIAdapterConfig {
  userId?: string;
}

export class CLIAdapter implements ChannelAdapter {
  readonly id   = "cli";
  readonly name = "CLI";

  private userId:    string;
  private sessionId: string;
  private ui:        TerminalUI;
  private commands:  CommandRegistry;

  /** Serialized processing — one message at a time. */
  private queue:      string[] = [];
  private processing = false;

  /** Active onboarding wizard (null when not running). */
  private _onboarding: OnboardingFlow | null = null;

  constructor(
    private gateway: OwlGateway,
    config: CLIAdapterConfig = {},
  ) {
    this.userId    = config.userId ?? "local";
    this.sessionId = makeSessionId(this.id, this.userId);
    this.ui        = new TerminalUI();
    this.ui.sessionId = this.sessionId;
    this.commands  = new CommandRegistry();
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this.ui.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this.ui.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async start(): Promise<void> {
    const owl    = this.gateway.getOwl();
    const config = this.gateway.getConfig();

    // Prepare TerminalUI identity (not entered yet)
    this.ui.setOwl(
      owl.persona.emoji,
      owl.persona.name,
      config.defaultProvider,
      config.defaultModel,
    );

    // Update DNA from owl traits
    const traits = owl.dna.evolvedTraits;
    const challengeNum = typeof traits.challengeLevel === "number"
      ? traits.challengeLevel
      : parseInt(String(traits.challengeLevel), 10) || 5;
    this.ui.updateDNA({
      challenge: challengeNum,
      verbosity: (traits as any).verbosity ?? 5,
      mood:      7,
    });

    // ── Phase 1: show Home screen ──────────────────────────────────
    await this._showHome(owl, config, challengeNum);

    // ── Phase 2: TerminalUI is now active ─────────────────────────
    this._wireUI();

    // Keep process alive until the UI emits "quit"
    await new Promise<void>((res) => {
      this.ui.once("quit", res);
      process.once("_stackowlStop", res as () => void);
    });
  }

  stop(): void {
    this.ui.close();
  }

  async deliverFile(_userId: string, filePath: string, caption?: string): Promise<void> {
    this.ui.printInfo(`File ready: ${filePath}${caption ? " — " + caption : ""}`);
  }

  // ─── Home → Active transition ─────────────────────────────────

  private _showHome(
    owl:          ReturnType<OwlGateway["getOwl"]>,
    config:       ReturnType<OwlGateway["getConfig"]>,
    challengeNum: number,
  ): Promise<void> {
    return new Promise((resolve) => {
      // Gather recent sessions if session store is available
      const recentSessions: Array<{ title: string; turns: number; ago: string }> = [];

      const home = new HomeScreen({
        owlEmoji:   owl.persona.emoji,
        owlName:    owl.persona.name,
        generation: owl.dna.generation,
        challenge:  challengeNum,
        provider:   config.defaultProvider,
        model:      config.defaultModel,
        skills:     (this.gateway.getSkillsLoader?.()?.getRegistry().listEnabled().length) ?? 0,
        recentSessions,
      });

      home.on("quit", async () => {
        home.close();
        await this._gracefulShutdown();
      });

      home.on("activate", (firstKey: string) => {
        // Stay in alt screen — hand off to TerminalUI
        home.transition();
        this.ui.enter();
        // Feed the first typed character into the input
        if (firstKey.length >= 1) {
          this.ui.feedChar(firstKey);
        }
        resolve();
      });

      home.enter();
    });
  }

  // ─── Wire TerminalUI events ───────────────────────────────────

  private _wireUI(): void {
    this.ui.on("line", (input: string) => {
      this.queue.push(input);
      this._drain();
    });

    this.ui.on("quit", async () => {
      await this._gracefulShutdown();
    });

    this.ui.on("onboarding", () => {
      const configPath   = resolve(process.cwd(), "stackowl.config.json");
      this._onboarding   = new OnboardingFlow(configPath);
      this._onboarding.start(this.ui);
    });
  }

  // ─── Queue ───────────────────────────────────────────────────

  private _drain(): void {
    if (this.processing || this.queue.length === 0) return;
    const input = this.queue.shift()!;
    this.processing = true;
    this._processLine(input).finally(() => {
      this.processing = false;
      this._drain();
    });
  }

  private async _processLine(input: string): Promise<void> {
    // Wizard intercepts all input while active
    if (this._onboarding) {
      const done = await this._onboarding.handleInput(input, this.ui);
      if (done) this._onboarding = null;
      return;
    }

    const consumed = await this.commands.handle(input, this.ui, this.gateway);
    if (consumed) return;

    try {
      log.cli.incoming(this.userId, input);
      this.gateway.getCognitiveLoop()?.notifyUserActivity();

      this.ui.showThinking();

      const { handler, didStream } = this.ui.createStreamHandler();

      const response = await this.gateway.handle(
        {
          id:        makeMessageId(),
          channelId: this.id,
          userId:    this.userId,
          sessionId: this.sessionId,
          text:      input,
        },
        {
          onProgress: async (msg: string) => {
            log.engine.debug(`[progress] ${msg}`);
          },
          askInstall: async (deps: string[]) => {
            this.ui.stopThinking();
            this.ui.printInfo(`📦 Install ${deps.join(" ")}? [y/n]`);
            return new Promise<boolean>((res) => {
              const onKey = (data: string) => {
                if (data.toLowerCase() === "y") { process.stdin.off("data", onKey); res(true); }
                else if (data.toLowerCase() === "n" || data === "\x03") { process.stdin.off("data", onKey); res(false); }
              };
              process.stdin.on("data", onKey);
            });
          },
          onStreamEvent: handler,
        },
      );

      log.cli.outgoing(this.userId, response.content);

      if (!didStream()) {
        this.ui.stopThinking();
        this.ui.printResponse(response.owlEmoji, response.owlName, response.content);
      }

      if (response.usage) {
        this.ui.updateStats(
          (response.usage.promptTokens ?? 0) + (response.usage.completionTokens ?? 0),
          0,
        );
      }
    } catch (err) {
      this.ui.stopThinking();
      const msg = err instanceof Error ? err.message : String(err);
      log.cli.error(`Error: ${msg}`);
      this.ui.printError(msg);
    }
  }

  private async _gracefulShutdown(): Promise<void> {
    this.ui.printInfo("Saving session…");
    await this.gateway.endSession(this.sessionId).catch(() => {});
    this.ui.close();
    process.exit(0);
  }
}
