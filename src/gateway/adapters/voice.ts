/**
 * StackOwl — Voice Channel Adapter
 *
 * Fully offline voice pipeline:
 *   🎤 naudiodon (mic) → energy VAD → whisper.cpp (STT)
 *        → OwlGateway → macOS `say` (TTS) 🔊
 *
 * Interaction model: push-to-talk + auto-stop
 *   1. User presses Enter (or just Enter from previous loop)
 *   2. Mic opens, red indicator shown
 *   3. Energy VAD detects 1.5s of silence → stops recording
 *   4. Whisper transcribes offline
 *   5. Gateway processes, owl responds
 *   6. macOS `say` speaks the response
 *   7. Loop
 */

import { createInterface } from "node:readline";
import { execSync } from "node:child_process";
import chalk from "chalk";
import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import { Logger } from "../../logger.js";
import { MicrophoneRecorder } from "../../voice/recorder.js";
import { WhisperSTT, type WhisperModel } from "../../voice/stt.js";
import type { StreamEvent } from "../../providers/base.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

const log = new Logger("VOICE");

// ─── Config ──────────────────────────────────────────────────────

export interface VoiceAdapterConfig {
  /** Fixed user ID (single-user channel). */
  userId?: string;
  /** Whisper model to use for STT. Default: "base.en". */
  model?: WhisperModel;
  /** macOS voice for TTS (passed to `say -v`). Default: "Samantha". */
  systemVoice?: string;
  /** Words-per-minute for TTS. Default: 200. */
  speakRate?: number;
  /**
   * RMS level below which audio is considered silence.
   * Raise in noisy environments. Default: 500.
   */
  silenceThreshold?: number;
  /**
   * Milliseconds of continuous silence that triggers end-of-speech.
   * Default: 1500.
   */
  silenceDurationMs?: number;
  /**
   * Pre-built WhisperSTT instance (already ensureReady()'d).
   * Pass this from voiceCommand() to avoid rebuilding the same instance.
   */
  sttInstance?: WhisperSTT;
}

// ─── Adapter ─────────────────────────────────────────────────────

export class VoiceChannelAdapter implements ChannelAdapter {
  readonly id = "voice";
  readonly name = "Voice";

  private userId: string;
  private sessionId: string;
  private recorder: MicrophoneRecorder;
  private stt: WhisperSTT;
  private systemVoice: string;
  private speakRate: number;
  private running = false;
  private rl?: ReturnType<typeof createInterface>;

  constructor(
    private gateway: OwlGateway,
    config: VoiceAdapterConfig = {},
  ) {
    this.userId = config.userId ?? "local";
    this.sessionId = makeSessionId(this.id, this.userId);

    this.recorder = new MicrophoneRecorder({
      sampleRate: 16000,
      channels: 1,
      silenceThreshold: config.silenceThreshold ?? 500,
      silenceDurationMs: config.silenceDurationMs ?? 1500,
    });

    this.stt = config.sttInstance ?? new WhisperSTT({
      model: config.model ?? "base.en",
      language: "en",
      removeWavAfter: true,
    });

    this.systemVoice = config.systemVoice ?? "Samantha";
    this.speakRate = config.speakRate ?? 200;
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this.printResponse(response);
    await this.speak(response.content);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this.printResponse(response);
    await this.speak(response.content);
  }

  async start(): Promise<void> {
    this.running = true;

    const owl = this.gateway.getOwl();

    console.log(
      chalk.cyan(`\n🎤 Voice mode active`) +
        chalk.dim(` — ${owl.persona.emoji} ${owl.persona.name}`),
    );
    console.log(
      chalk.dim(
        `   Model: whisper ${this.stt.modelName} | Voice: ${this.systemVoice}`,
      ),
    );
    console.log(
      chalk.dim(
        `   Commands: ${chalk.bold("Enter")} = speak · ${chalk.bold("quit")} + Enter = exit\n`,
      ),
    );

    this.rl = createInterface({
      input: process.stdin,
      output: process.stdout,
    });

    while (this.running) {
      const input = await this.prompt(
        chalk.cyan("🎤 Press Enter to speak... "),
      );

      if (!this.running) break;
      if (input.trim().toLowerCase() === "quit") break;

      // --- Record ---
      process.stdout.write(
        chalk.red("🔴 Recording") + chalk.dim(" (silence auto-stops)\r"),
      );

      let wavPath: string | null = null;
      try {
        wavPath = await this.recorder.record();
      } catch (err) {
        const msg = (err as Error).message;
        if (msg === "No speech detected") {
          console.log(chalk.dim("  (no speech detected)\n"));
        } else {
          console.error(chalk.red(`  ❌ Mic error: ${msg}\n`));
        }
        continue;
      }

      // --- Transcribe ---
      process.stdout.write(chalk.yellow("⏳ Transcribing...         \r"));

      let text: string;
      try {
        text = await this.stt.transcribe(wavPath);
      } catch (err) {
        console.error(
          chalk.red(`  ❌ STT error: ${(err as Error).message}\n`),
        );
        MicrophoneRecorder.cleanup(wavPath);
        continue;
      }

      if (!text.trim()) {
        console.log(chalk.dim("  (nothing transcribed)\n"));
        continue;
      }

      console.log(chalk.white(`\nYou: ${chalk.bold(text)}`));

      // --- Gateway ---
      process.stdout.write(chalk.dim("⏳ Processing...\n"));

      let response: GatewayResponse;
      try {
        log.debug(`Incoming voice message from ${this.userId}: ${text}`);
        this.gateway.getCognitiveLoop?.()?.notifyUserActivity?.();

        let streamedContent = false;
        const streamHandler = this.createStreamHandler();
        const trackingHandler = async (event: StreamEvent) => {
          await streamHandler(event);
          if (
            event.type === "text_delta" &&
            event.content.replace(/\[DONE\]/g, "").length > 0
          ) {
            streamedContent = true;
          }
        };

        response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: this.id,
            userId: this.userId,
            sessionId: this.sessionId,
            text,
          },
          {
            onProgress: async (msg: string) => {
              process.stdout.write(chalk.dim(`  ⋯ ${msg}\r`));
            },
            onStreamEvent: trackingHandler,
            suppressThinking: true,
          },
        );

        if (!streamedContent) {
          this.printResponse(response);
        } else {
          process.stdout.write("\n");
        }
      } catch (err) {
        console.error(chalk.red(`  ❌ Error: ${(err as Error).message}\n`));
        continue;
      }

      // --- Speak ---
      await this.speak(response.content);
      console.log("");
    }

    this.running = false;
    this.rl?.close();
    console.log(chalk.dim("\n🦉 Voice session ended.\n"));
  }

  stop(): void {
    this.running = false;
    this.rl?.close();
  }

  // ─── Readline helper ─────────────────────────────────────────

  private prompt(question: string): Promise<string> {
    return new Promise((resolve) => {
      if (!this.rl || !this.running) {
        resolve("");
        return;
      }
      this.rl.question(question, (answer) => resolve(answer));
    });
  }

  // ─── TTS ─────────────────────────────────────────────────────

  /**
   * Speak text via macOS `say` — fully offline, uses Apple neural voices.
   * Strips markdown syntax before speaking.
   */
  private async speak(text: string): Promise<void> {
    if (process.platform !== "darwin") {
      log.warn("System TTS (say) is only available on macOS");
      return;
    }

    const clean = this.stripMarkdown(text);
    if (!clean.trim()) return;

    // Truncate very long responses to avoid blocking the loop for too long
    const MAX_CHARS = 800;
    const truncated =
      clean.length > MAX_CHARS
        ? clean.slice(0, MAX_CHARS) + "... (truncated)"
        : clean;

    try {
      const escaped = truncated.replace(/"/g, '\\"').replace(/\$/g, "\\$");
      execSync(
        `say -v "${this.systemVoice}" -r ${this.speakRate} "${escaped}"`,
        { timeout: 60_000, stdio: "ignore" },
      );
    } catch (err) {
      log.warn(`TTS failed: ${(err as Error).message}`);
    }
  }

  private stripMarkdown(text: string): string {
    return text
      .replace(/#{1,6}\s+/g, "")           // headings
      .replace(/\*\*([^*]+)\*\*/g, "$1")   // bold
      .replace(/\*([^*]+)\*/g, "$1")       // italic
      .replace(/`{1,3}[^`]*`{1,3}/g, "")  // code
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // links
      .replace(/^\s*[-*+]\s+/gm, "")      // list bullets
      .replace(/^\s*\d+\.\s+/gm, "")      // numbered lists
      .replace(/\n{2,}/g, ". ")           // paragraph breaks → pauses
      .replace(/\n/g, " ")
      .trim();
  }

  // ─── Streaming ────────────────────────────────────────────────

  private createStreamHandler(): (event: StreamEvent) => Promise<void> {
    let headerPrinted = false;
    const owl = this.gateway.getOwl();

    return async (event: StreamEvent) => {
      switch (event.type) {
        case "text_delta": {
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
          if (headerPrinted) process.stdout.write("\n");
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
  }
}
