/**
 * StackOwl — CLI Channel Adapter
 *
 * Transport layer for the interactive terminal. All business logic lives in OwlGateway.
 * This adapter's responsibilities:
 *   - Readline loop
 *   - Command handling (/quit, /owls, /status, /capabilities, /learning)
 *   - Normalize input → GatewayMessage
 *   - Format GatewayResponse with chalk
 */

import { createInterface } from "node:readline";
import chalk from "chalk";
import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import { log } from "../../logger.js";
import type { StreamEvent } from "../../providers/base.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

// ─── Config ──────────────────────────────────────────────────────

export interface CLIAdapterConfig {
  /** Fixed user ID for the CLI — there's always exactly one user */
  userId?: string;
}

// ─── Adapter ─────────────────────────────────────────────────────

export class CLIAdapter implements ChannelAdapter {
  readonly id = "cli";
  readonly name = "CLI";

  private userId: string;
  private sessionId: string;
  private rl?: ReturnType<typeof createInterface>;
  /** Serialized processing — ensures one message completes before the next starts */
  private messageQueue: string[] = [];
  private processing = false;

  constructor(
    private gateway: OwlGateway,
    config: CLIAdapterConfig = {},
  ) {
    this.userId = config.userId ?? "local";
    this.sessionId = makeSessionId(this.id, this.userId);
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this.printResponse(response);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this.printResponse(response);
  }

  async start(): Promise<void> {
    const owl = this.gateway.getOwl();

    this.rl = createInterface({
      input: process.stdin,
      output: process.stdout,
      prompt: chalk.cyan("You: "),
    });

    console.log(
      chalk.dim(
        `\nType your message. Commands: ` +
          `${chalk.bold("/quit")} · ${chalk.bold("/owls")} · ` +
          `${chalk.bold("/status")} · ${chalk.bold("/capabilities")} · ` +
          `${chalk.bold("/learning")} · ${chalk.bold("/skills")} · ` +
          `${chalk.bold("/skill <name>")} · ${chalk.bold("/clear")}\n`,
      ),
    );
    console.log(
      chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`) +
        chalk.dim(` (challenge: ${owl.dna.evolvedTraits.challengeLevel})`),
    );
    console.log("");

    this.rl.prompt();

    this.rl.on("line", (line) => {
      const input = line.trim();
      if (!input) {
        this.rl!.prompt();
        return;
      }
      // Enqueue and drain serially — prevents /quit from racing a slow response
      this.messageQueue.push(input);
      this.drainQueue();
    });

    // Keep alive; graceful shutdown on close (EOF or Ctrl+D)
    await new Promise<void>((resolve) => {
      this.rl!.on("close", async () => {
        await this.gracefulShutdown();
        resolve();
      });
    });
  }

  stop(): void {
    this.rl?.close();
  }

  private drainQueue(): void {
    if (this.processing || this.messageQueue.length === 0) return;
    const input = this.messageQueue.shift()!;
    this.processing = true;
    this.processLine(input).finally(() => {
      this.processing = false;
      this.drainQueue(); // process next if any
    });
  }

  private async processLine(input: string): Promise<void> {
    if (await this.handleCommand(input)) {
      return; // command consumed
    }

    // Regular message → gateway
    this.rl!.pause();
    try {
      log.cli.incoming(this.userId, input);
      this.gateway.getCognitiveLoop()?.notifyUserActivity();

      // Track whether streaming already delivered the response to stdout.
      // If it did, skip printResponse to avoid printing the answer twice.
      let streamedContent = false;
      const streamHandler = this.createStreamHandler();
      const trackingHandler = async (event: import("../../providers/base.js").StreamEvent) => {
        await streamHandler(event);
        if (event.type === "text_delta" && event.content.replace(/\[DONE\]/g, "").length > 0) {
          streamedContent = true;
        }
      };

      const response = await this.gateway.handle(
        {
          id: makeMessageId(),
          channelId: this.id,
          userId: this.userId,
          sessionId: this.sessionId,
          text: input,
        },
        {
          onProgress: async (msg: string) => {
            console.log(chalk.dim(`  ⋯ ${msg}`));
          },
          // CLI: no file-sending capability in terminal
          onFile: async (filePath: string) => {
            console.log(chalk.dim(`  [File ready: ${filePath}]`));
          },
          askInstall: async (deps: string[]) => {
            return new Promise<boolean>((resolve) => {
              const tmpRl = createInterface({
                input: process.stdin,
                output: process.stdout,
              });
              tmpRl.question(
                chalk.yellow(
                  `\n📦 Install npm deps: ${deps.join(" ")}? [y/n] `,
                ),
                (answer) => {
                  tmpRl.close();
                  resolve(
                    answer.trim().toLowerCase() === "y" ||
                      answer.trim().toLowerCase() === "yes",
                  );
                },
              );
            });
          },
          onStreamEvent: trackingHandler,
        },
      );

      log.cli.outgoing(this.userId, response.content);
      // Only print the full response if streaming did NOT already deliver it.
      // Streaming + printResponse = duplicate output.
      if (!streamedContent) {
        this.printResponse(response);
      } else {
        console.log(""); // ensure prompt appears on a new line
        if (this.rl) this.rl.prompt();
      }

      if (response.usage) {
        console.log(
          chalk.dim(
            `  [tokens: ${response.usage.promptTokens}→${response.usage.completionTokens}]`,
          ),
        );
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.cli.error(`Error: ${msg}`);
      console.error(chalk.red(`\n❌ Error: ${msg}\n`));
    } finally {
      this.rl!.resume();
      this.rl!.prompt();
    }
  }

  // ─── Command handling ─────────────────────────────────────────

  /** Returns true if the input was a command (consumed). */
  private async handleCommand(input: string): Promise<boolean> {
    const rl = this.rl!;

    if (input === "/quit" || input === "/exit") {
      console.log(chalk.dim("\n🦉 Saving session and evolving..."));
      await this.gateway.endSession(this.sessionId).catch(() => {});
      console.log(chalk.dim("🦉 Goodbye. The owls are always watching.\n"));
      rl.close();
      process.exit(0);
    }

    if (input === "/clear" || input === "/reset") {
      await this.gateway.handle({
        id: makeMessageId(),
        channelId: this.id,
        userId: this.userId,
        sessionId: this.sessionId,
        text: "/reset",
      });
      console.log(
        chalk.dim(
          "\n🧹 Context cleared! Starting fresh. What would you like to work on?\n",
        ),
      );
      rl.prompt();
      return true;
    }

    if (input === "/owls") {
      const registry = this.gateway.getOwlRegistry();
      console.log(chalk.bold("\nAvailable Owls:"));
      for (const o of registry.listOwls()) {
        console.log(
          `  ${o.persona.emoji} ${chalk.bold(o.persona.name)} — ${o.persona.type} (challenge: ${o.dna.evolvedTraits.challengeLevel})`,
        );
      }
      console.log("");
      rl.prompt();
      return true;
    }

    if (input === "/status") {
      const owl = this.gateway.getOwl();
      const config = this.gateway.getConfig();
      console.log(chalk.bold("\nStatus:"));
      console.log(`  Provider: ${config.defaultProvider}`);
      console.log(`  Model:    ${config.defaultModel}`);
      console.log(`  Owl:      ${owl.persona.emoji} ${owl.persona.name}`);
      console.log(`  DNA Gen:  ${owl.dna.generation}`);
      console.log("");
      rl.prompt();
      return true;
    }

    if (input === "/capabilities") {
      const evolution = this.gateway.getEvolution();
      if (!evolution) {
        console.log(chalk.dim("\n  Evolution system not available.\n"));
        rl.prompt();
        return true;
      }
      const records = await evolution.listAll();
      if (records.length === 0) {
        console.log(
          chalk.dim(
            "\n  No synthesized tools yet. The owl will build them when needed.\n",
          ),
        );
      } else {
        console.log(chalk.bold("\n🔧 Synthesized Tools:\n"));
        for (const r of records) {
          const icon =
            r.status === "active"
              ? chalk.green("✓")
              : r.status === "failed"
                ? chalk.red("✗")
                : chalk.dim("⊘");
          console.log(`  ${icon} ${chalk.bold(r.toolName)}`);
          console.log(`     ${chalk.dim(r.description)}`);
          console.log(
            `     ${chalk.dim(`Used: ${r.timesUsed}x | Status: ${r.status}`)}`,
          );
          console.log("");
        }
      }
      rl.prompt();
      return true;
    }

    if (input === "/skills") {
      const loader = this.gateway.getSkillsLoader?.();
      if (!loader) {
        console.log(chalk.dim("\n  Skills not loaded.\n"));
      } else {
        const skills = loader.getRegistry().listEnabled();
        if (skills.length === 0) {
          console.log(
            chalk.dim(
              "\n  No skills loaded. Add SKILL.md files to workspace/skills/\n",
            ),
          );
        } else {
          console.log(chalk.bold("\n🎯 Available Skills:\n"));
          for (const s of skills) {
            const emoji = s.metadata.openclaw?.emoji || "🎯";
            const always = s.metadata.openclaw?.always
              ? chalk.cyan(" [always]")
              : "";
            console.log(`  ${emoji} ${chalk.bold(s.name)}${always}`);
            console.log(`     ${chalk.dim(s.description)}`);
            console.log(`     ${chalk.dim(`Use: /skill ${s.name}`)}`);
            console.log("");
          }
        }
      }
      rl.prompt();
      return true;
    }

    if (input === "/learning") {
      const learning = this.gateway.getLearningEngine();
      if (!learning) {
        console.log(chalk.dim("\n  Learning engine not available.\n"));
        rl.prompt();
        return true;
      }
      console.log(chalk.bold("\n🧠 Learning Report:\n"));
      const report = await learning.getLearningReport();
      console.log(chalk.dim(report));
      console.log("");
      rl.prompt();
      return true;
    }

    return false;
  }

  // ─── Streaming ────────────────────────────────────────────────

  /**
   * Create a StreamEvent handler for progressive stdout streaming.
   * Text deltas are written directly to stdout as they arrive.
   */
  private createStreamHandler(): (event: StreamEvent) => Promise<void> {
    let headerPrinted = false;
    const owl = this.gateway.getOwl();

    return async (event: StreamEvent) => {
      switch (event.type) {
        case "text_delta": {
          // Strip internal [DONE] signal
          const chunk = event.content.replace(/\[DONE\]/g, "");
          if (!chunk) break;
          if (!headerPrinted) {
            process.stdout.write(
              "\n" + chalk.yellow(`${owl.persona.emoji} ${owl.persona.name}: `),
            );
            headerPrinted = true;
          }
          process.stdout.write(chunk);
          break;
        }
        case "tool_start": {
          process.stdout.write(chalk.dim(`\n  ⚙️ ${event.toolName}...`));
          break;
        }
        case "tool_end": {
          process.stdout.write(chalk.dim(" ✓"));
          break;
        }
        case "done": {
          if (headerPrinted) {
            process.stdout.write("\n");
          }
          break;
        }
      }
    };
  }

  // ─── Display ─────────────────────────────────────────────────

  private printResponse(response: GatewayResponse): void {
    console.log("");
    process.stdout.write(
      chalk.yellow(`${response.owlEmoji} ${response.owlName}: `),
    );
    console.log(response.content);
    console.log("");
    if (this.rl) this.rl.prompt();
  }

  private async gracefulShutdown(): Promise<void> {
    await this.gateway.endSession(this.sessionId).catch(() => {});
  }
}
