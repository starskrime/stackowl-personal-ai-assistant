/**
 * cli-v2.ts — TUI v2 Channel Adapter.
 *
 * Phase 1: Full REPL adapter that calls gateway.handle() and feeds
 * engine signals into the UiBridge as typed UiEvents.
 *
 * Key contracts:
 * - emit() routes all engine signals through UiBridge (the ONE translator)
 * - capabilities() declares tuiV2: true so heartbeat + parliament choose the right delivery path
 * - wireToolNarration from v1 is intentionally NOT used here — tool events come via emit()
 * - Ink owns stdin; the adapter exposes submitMessage() for the Composer component to call
 */

import { v4 as uuidv4 } from "uuid";
import type { ChannelAdapter, ChannelCapabilities, GatewayResponse } from "../types.js";
import type { UiEvent } from "../../cli/v2/events/UiEvent.js";
import { globalBridge, type OwlMeta } from "../../cli/v2/events/bridge.js";
import { OwlGateway, makeMessage, makeSessionId } from "../core.js";

export interface CliV2AdapterConfig {
  userId?: string;
  workspacePath?: string;
}

export class CliV2Adapter implements ChannelAdapter {
  readonly id = "cli-v2";
  readonly name = "CLI v2";

  private readonly _gateway: OwlGateway;
  private readonly _userId: string;
  private readonly _channelId = "cli-v2";
  private _sessionId: string;

  /** Resolves when stop() is called — start() awaits this. */
  private _quitResolve: (() => void) | null = null;
  private _quitPromise: Promise<void>;

  constructor(gateway: OwlGateway, config: CliV2AdapterConfig = {}) {
    this._gateway = gateway;
    this._userId = config.userId ?? "local";
    this._sessionId = makeSessionId(this._channelId, this._userId);

    // Pre-construct the quit promise so stop() is safe to call before start().
    this._quitPromise = new Promise<void>((resolve) => {
      this._quitResolve = resolve;
    });
  }

  // ─── ChannelAdapter ───────────────────────────────────────────────────────

  async start(): Promise<void> {
    // Resolve initial owl meta from gateway.
    const owl = this._gateway.getOwl();
    const owlEmoji = owl.persona.emoji;
    const owlName = owl.persona.name;

    // Seed the store with the current session.
    globalBridge.emit({ kind: "session.changed", sessionId: this._sessionId });

    // Announce the initial owl (no turn yet — use a placeholder turnId).
    // The Composer will call submitMessage(), which generates a real turnId per turn.
    // This pre-seeds the header so the UI shows the owl before the first message.
    globalBridge.translateOwlChange("__init__", owlEmoji, owlName);

    // Stay alive until stop() is called (Ink owns the event loop via stdin).
    await this._quitPromise;
  }

  stop(): void {
    this._quitResolve?.();
    this._quitResolve = null;
  }

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this._emitCommitted(response);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this._emitCommitted(response);
  }

  // ─── TUI v2 extensions ────────────────────────────────────────────────────

  emit(event: UiEvent): void {
    globalBridge.emit(event);
  }

  capabilities(): ChannelCapabilities {
    return {
      tuiV2: true,
      richText: false,
      fileAttachments: false,
    };
  }

  // ─── REPL surface (called by Ink Composer component) ─────────────────────

  /**
   * Process a user message through the gateway.
   * Called by the Composer Ink component on Enter.
   */
  async submitMessage(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;

    const msg = makeMessage(this._channelId, this._userId, trimmed, this._sessionId);
    if (!msg) return;

    const turnId = uuidv4();

    // Resolve current owl meta for the bridge.
    const owl = this._gateway.getOwl();
    const owlMeta: OwlMeta = {
      owlEmoji: owl.persona.emoji,
      owlName: owl.persona.name,
    };

    // Announce turn start (current owl).
    globalBridge.translateOwlChange(turnId, owlMeta.owlEmoji, owlMeta.owlName);

    // Accumulate streamed text so we can supply fullText on "done".
    let accumulated = "";
    // Track whether the streaming path already emitted turn.committed via the
    // "done" StreamEvent so the fallback below doesn't fire a second time.
    let committedViaStream = false;

    try {
      const response = await this._gateway.handle(msg, {
        suppressThinking: true,

        onStreamEvent: async (event) => {
          // Track accumulated text for the "done" event.
          if (event.type === "text_delta") {
            accumulated += event.content;
          }
          // translateStreamEvent emits turn.committed when event.type === "done".
          if (event.type === "done") {
            committedViaStream = true;
          }
          globalBridge.translateStreamEvent(turnId, event, owlMeta, accumulated);
        },

        onOwlChange: (owlEmoji: string, owlName: string) => {
          // Re-emit turn.started with the new specialist owl.
          owlMeta.owlEmoji = owlEmoji;
          owlMeta.owlName = owlName;
          globalBridge.translateOwlChange(turnId, owlEmoji, owlName);
        },

        onProgress: async (_text: string) => {
          // Ink handles progress via stream events — no-op here.
        },

        askInstall: async (_deps: string[]) => {
          // Phase 1: always approve. Phase 3 will wire a modal.
          return true;
        },
      });

      // Fallback: if the engine did NOT stream a "done" event (non-streaming
      // providers), emit turn.committed from the final GatewayResponse.
      if (!committedViaStream) {
        const finalText = response.content || accumulated;
        globalBridge.emit({
          kind: "turn.committed",
          turnId,
          text: finalText,
          usage: response.usage
            ? {
                promptTokens: response.usage.promptTokens,
                completionTokens: response.usage.completionTokens,
                costUsd: response.estimatedCostUsd ?? 0,
              }
            : undefined,
        });
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      globalBridge.emit({
        kind: "notice",
        source: "error",
        text: errMsg,
        severity: "error",
      });
    }
  }

  // ─── Internals ────────────────────────────────────────────────────────────

  private _emitCommitted(response: GatewayResponse): void {
    const turnId = uuidv4();
    globalBridge.translateOwlChange(turnId, response.owlEmoji, response.owlName);
    globalBridge.emit({
      kind: "turn.committed",
      turnId,
      text: response.content,
      usage: response.usage
        ? {
            promptTokens: response.usage.promptTokens,
            completionTokens: response.usage.completionTokens,
            costUsd: response.estimatedCostUsd ?? 0,
          }
        : undefined,
    });
  }
}
