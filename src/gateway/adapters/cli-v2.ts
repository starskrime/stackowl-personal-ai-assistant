/**
 * cli-v2.ts — TUI v2 Channel Adapter shell.
 *
 * Phase 0: no-op stub that satisfies ChannelAdapter + emit() + capabilities().
 * Phase 1: wired to the UiBridge to deliver engine signals as UiEvents.
 *
 * Key contracts:
 * - emit() routes all engine signals through UiBridge (the ONE translator)
 * - capabilities() declares tuiV2: true so heartbeat + parliament choose the right delivery path
 * - wireToolNarration from v1 is intentionally NOT used here — tool events come via emit()
 */

import type { ChannelAdapter, ChannelCapabilities, GatewayResponse } from "../types.js";
import type { UiEvent } from "../../cli/v2/events/UiEvent.js";
import { globalBridge } from "../../cli/v2/events/bridge.js";

export interface CliV2AdapterConfig {
  userId?: string;
  workspacePath?: string;
}

export class CliV2Adapter implements ChannelAdapter {
  readonly id = "cli-v2";
  readonly name = "CLI v2";

  constructor(_config: CliV2AdapterConfig = {}) {
    void _config; // Phase 1 will use userId + workspacePath
  }

  // ─── ChannelAdapter ───────────────────────────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    // Phase 1: routes to turn.committed event via emit().
    // Phase 0: no-op (Ink renders from store, not direct calls).
    void response;
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    void response;
  }

  async start(): Promise<void> {
    // Phase 1: subscribe to gateway event bus, translate StreamEvents → UiEvents via bridge.
  }

  stop(): void {
    // Phase 1: unsubscribe.
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
}
