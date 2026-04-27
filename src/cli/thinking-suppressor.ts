/**
 * StackOwl — Thinking Message Suppressor
 *
 * Suppresses intermediate thinking/progress messages in CLI output
 * when running in scripting mode for clean piped output.
 */

import type { StreamEvent } from "../providers/base.js";

export type SuppressionLevel = "none" | "progress" | "full";

export interface ThinkingSuppressorOptions {
  suppressEnvVar?: string;
  quietArg?: boolean;
  defaultLevel?: SuppressionLevel;
}

export class ThinkingSuppressor {
  private suppressed = false;
  private level: SuppressionLevel;
  private bufferedDeltas: string[] = [];
  private messageCount = 0;

  constructor(options: ThinkingSuppressorOptions = {}) {
    this.level = this._computeLevel(options);
    this.suppressed = this.level !== "none";
  }

  private _computeLevel(options: ThinkingSuppressorOptions): SuppressionLevel {
    if (process.env.STACKOWL_SUPPRESS_THINKING === "true") return "full";
    if (process.env.STACKOWL_SUPPRESS_THINKING === "progress") return "progress";
    if (options.suppressEnvVar && process.env[options.suppressEnvVar] === "true") return "full";
    if (options.quietArg || process.argv.includes("--quiet") || process.argv.includes("-q")) return "full";
    if (process.env.STACKOWL_JSON === "true" || process.argv.includes("--json")) return "full";
    return options.defaultLevel ?? "none";
  }

  /**
   * Check if suppression is active.
   */
  isActive(): boolean {
    return this.suppressed;
  }

  /**
   * Get current suppression level.
   */
  getLevel(): SuppressionLevel {
    return this.level;
  }

  /**
   * Enable suppression.
   */
  enable(level: SuppressionLevel = "full"): void {
    this.level = level;
    this.suppressed = level !== "none";
  }

  /**
   * Disable suppression.
   */
  disable(): void {
    this.level = "none";
    this.suppressed = false;
  }

  /**
   * Should we suppress a progress message?
   */
  shouldSuppressProgress(): boolean {
    return this.suppressed && this.level === "full";
  }

  /**
   * Should we suppress individual text deltas?
   */
  shouldSuppressDelta(): boolean {
    return this.suppressed && this.level === "full";
  }

  /**
   * Process a stream event and return whether it should be suppressed.
   */
  processEvent(event: StreamEvent): boolean {
    this.messageCount++;

    switch (event.type) {
      case "text_delta": {
        if (this.shouldSuppressDelta()) {
          this.bufferedDeltas.push(event.content);
          return true;
        }
        return false;
      }
      case "tool_start": {
        if (this.shouldSuppressProgress()) return true;
        return false;
      }
      case "tool_end": {
        if (this.shouldSuppressProgress()) return true;
        return false;
      }
      default:
        return false;
    }
  }

  /**
   * Get buffered deltas (only available after suppression ends).
   */
  getBufferedContent(): string {
    return this.bufferedDeltas.join("");
  }

  /**
   * Clear buffered content.
   */
  clearBuffer(): void {
    this.bufferedDeltas = [];
  }

  /**
   * Get count of processed messages.
   */
  getMessageCount(): number {
    return this.messageCount;
  }

  /**
   * Should tool calls be shown in progress?
   */
  shouldShowToolCalls(): boolean {
    return !this.suppressed;
  }

  /**
   * Should the thinking indicator be shown?
   */
  shouldShowThinking(): boolean {
    return !this.suppressed;
  }

  /**
   * Create a callback suitable for onStreamEvent that applies suppression.
   */
  createSuppressedCallback(
    originalCallback: (event: StreamEvent) => Promise<void>
  ): (event: StreamEvent) => Promise<void> {
    return async (event: StreamEvent) => {
      if (this.processEvent(event)) {
        return;
      }
      await originalCallback(event);
    };
  }

  /**
   * Create a progress callback that respects suppression.
   */
  createProgressCallback(
    originalCallback: (msg: string) => Promise<void>
  ): (msg: string) => Promise<void> {
    return async (msg: string) => {
      if (this.shouldSuppressProgress()) {
        return;
      }
      await originalCallback(msg);
    };
  }
}

export function createThinkingSuppressor(options?: ThinkingSuppressorOptions): ThinkingSuppressor {
  return new ThinkingSuppressor(options);
}