/**
 * bridge.ts — the ONE translator.
 *
 * Translates engine StreamEvents and gateway bus events into UiEvents.
 * Nothing else may produce UiEvents.
 *
 * Forbidden: importing from src/engine/* directly.
 * Allowed: importing from src/gateway/events.ts stable re-exports only.
 */

import type { StreamEvent } from "../../../providers/base.js";
import type { OwlPosition, OwlChallenge, ParliamentPhase } from "../../../parliament/protocol.js";
import type { UiEvent } from "./UiEvent.js";

export type UiEventHandler = (event: UiEvent) => void;

export interface OwlMeta {
  owlEmoji: string;
  owlName: string;
  owlRole?: string;
  estimatedCostUsd?: number;
}

export class UiBridge {
  private _handlers: UiEventHandler[] = [];
  private _toolStartTimes = new Map<string, number>();

  subscribe(handler: UiEventHandler): () => void {
    this._handlers.push(handler);
    return () => {
      this._handlers = this._handlers.filter((h) => h !== handler);
    };
  }

  emit(event: UiEvent): void {
    for (const h of this._handlers) h(event);
  }

  /**
   * Translate a single StreamEvent into a UiEvent and emit it.
   *
   * Mapping:
   *   text_delta       → token.delta
   *   tool_start       → tool.requested
   *   tool_args_delta  → (ignored — args streaming not surfaced in TUI)
   *   tool_end         → tool.completed
   *   done             → turn.committed (accumulated text must be supplied by caller)
   */
  translateStreamEvent(
    turnId: string,
    event: StreamEvent,
    owlMeta: OwlMeta,
    /** Accumulated full text so far — required when event.type === "done" */
    fullText: string,
  ): void {
    switch (event.type) {
      case "text_delta":
        this.emit({ kind: "token.delta", turnId, text: event.content });
        break;

      case "tool_start":
        this._toolStartTimes.set(event.toolCallId, Date.now());
        this.emit({
          kind: "tool.requested",
          toolCallId: event.toolCallId,
          turnId,
          toolName: event.toolName,
        });
        break;

      case "tool_args_delta":
        // Intentionally ignored — arg streaming is not surfaced in the TUI.
        break;

      case "tool_end": {
        const startTime = this._toolStartTimes.get(event.toolCallId) ?? Date.now();
        this._toolStartTimes.delete(event.toolCallId);
        const elapsedMs = Date.now() - startTime;
        this.emit({
          kind: "tool.completed",
          toolCallId: event.toolCallId,
          elapsedMs,
        });
        break;
      }

      case "done":
        this.emit({
          kind: "turn.committed",
          turnId,
          text: fullText,
          usage: event.usage
            ? {
                promptTokens: event.usage.promptTokens,
                completionTokens: event.usage.completionTokens,
                costUsd: owlMeta.estimatedCostUsd ?? 0,
              }
            : undefined,
        });
        break;
    }
  }

  /**
   * Emit a turn.started event when the active owl is known (or changes).
   * Called before gateway.handle() begins and again when onOwlChange fires.
   */
  translateOwlChange(
    turnId: string,
    owlEmoji: string,
    owlName: string,
    owlRole?: string,
  ): void {
    this.emit({
      kind: "turn.started",
      turnId,
      owlId: owlName,
      owlName,
      owlEmoji,
      owlRole,
    });
  }

  // ─── Parliament debate event translators ─────────────────────────────────

  /**
   * Called when a debate round begins.
   * Emits parliament.round.started — the reducer switches ui.mode to "parliament".
   */
  translateDebateRoundStart(
    debateId: string,
    round: number,
    _phase: ParliamentPhase,
    owls: Array<{ owlName: string; owlEmoji: string }>,
  ): void {
    this.emit({
      kind: "parliament.round.started",
      debateId,
      round,
      totalRounds: 3,
      owls: owls.map((o) => ({
        owlId: o.owlName,
        owlName: o.owlName,
        owlEmoji: o.owlEmoji,
      })),
    });
  }

  /**
   * Called when an owl delivers their Round 1 position.
   * Emits parliament.position.ready.
   */
  translateDebatePosition(debateId: string, position: OwlPosition): void {
    this.emit({
      kind: "parliament.position.ready",
      debateId,
      owlId: position.owlName,
      owlName: position.owlName,
      owlEmoji: position.owlEmoji,
      position: `[${position.position}] ${position.argument}`,
    });
  }

  /**
   * Called when the challenger delivers their cross-examination (Round 2).
   * Emits parliament.challenge.ready.
   */
  translateDebateChallenge(debateId: string, challenge: OwlChallenge): void {
    this.emit({
      kind: "parliament.challenge.ready",
      debateId,
      owlId: challenge.owlName,
      owlName: challenge.owlName,
      owlEmoji: "",
      challenge: challenge.challengeContent,
    });
  }

  /**
   * Called when the synthesis owl delivers the final verdict (Round 3).
   * Emits parliament.synthesis.ready — the reducer switches ui.mode back to "chat".
   */
  translateDebateSynthesis(
    debateId: string,
    synthesis: string,
    _verdict: string,
    synthOwlName: string,
  ): void {
    this.emit({
      kind: "parliament.synthesis.ready",
      debateId,
      synthesis,
      owlId: synthOwlName,
      owlName: synthOwlName,
    });
  }

  /**
   * Emit parliament.view.requested — switches ui.mode to "parliament".
   * Called by Ctrl+P keyboard handler.
   */
  requestParliamentView(): void {
    this.emit({ kind: "parliament.view.requested" });
  }

  /**
   * Emit parliament.view.dismissed — switches ui.mode back to "chat".
   * Called by Ctrl+P when already in parliament mode (toggle behavior).
   */
  dismissParliamentView(): void {
    this.emit({ kind: "parliament.view.dismissed" });
  }

  /**
   * Build a DebateCallbacks object that routes all parliament events through this bridge.
   * Pass these callbacks into ParliamentSession.config.callbacks before runDebate().
   */
  makeDebateCallbacks(
    debateId: string,
    participants: Array<{ owlName: string; owlEmoji: string }>,
  ): import("../../../parliament/protocol.js").ParliamentCallbacks {
    return {
      onRoundStart: async (round, phase) => {
        this.translateDebateRoundStart(debateId, round, phase, participants);
      },
      onPositionReady: async (position) => {
        this.translateDebatePosition(debateId, position);
      },
      onChallengeReady: async (challenge) => {
        this.translateDebateChallenge(debateId, challenge);
      },
      onSynthesisReady: async (synthesis, verdict) => {
        // Pick the first participant as fallback synthesizer name — the real name
        // is baked into synthesis text. We don't have the owl identity here.
        const synthName = participants[0]?.owlName ?? "Parliament";
        this.translateDebateSynthesis(debateId, synthesis, verdict, synthName);
      },
    };
  }
}

export const globalBridge = new UiBridge();
