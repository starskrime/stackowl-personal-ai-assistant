/**
 * Channel-agnostic command dispatcher and context factory.
 *
 * Any channel adapter (Telegram, Slack, Discord, …) can use this to invoke
 * any registered command without needing a TUI CommandContext.  Handlers that
 * only use CoreCommandContext will work automatically; handlers that require
 * bridge/store will receive data from the gateway where possible, with 0/empty
 * fallbacks for session-level metrics that only exist in TUI state.
 */

import { resolveCommand } from "../../cli/v2/commands/registry.js";
import type { CoreCommandContext, CoreCommandResult, CommandContext } from "../../cli/v2/commands/registry.js";
import type { OwlGateway } from "../core.js";
import { log } from "../../logger.js";

/**
 * Build a CoreCommandContext from an OwlGateway.
 * Use this in every channel adapter — it's the single source of truth
 * for creating a channel-agnostic context.
 */
export function buildCoreCtx(gateway: OwlGateway): CoreCommandContext {
  return {
    getOwlGateway: () => gateway,
    getMemoryRepo:  () => gateway.getMemoryRepo()!,
    getMcpManager:  () => gateway.getMcpManager()!,
  };
}

/**
 * Build a CommandContext from a CoreCommandContext by attaching no-op TUI stubs.
 * getStore() is populated with gateway-readable data (owl, config) so commands
 * like /status show real values. Session-level metrics (tokens, cost, context)
 * default to 0 since those only exist in TUI state.
 */
function asTuiContext(core: CoreCommandContext): CommandContext {
  const gateway = core.getOwlGateway();
  const config = gateway.getConfig();
  const owl = gateway.getOwl();

  return {
    ...core,
    bridge: {
      emit: () => {},
      openPanel: () => {},
      closePanel: () => {},
      requestOnboardingView: () => {},
      on: () => () => {},
    } as unknown as import("../../cli/v2/events/bridge.js").UiBridge,
    getStore: () => ({
      activeOwlEmoji:    owl?.persona.emoji    ?? "",
      activeOwlName:     owl?.persona.name     ?? "Unknown",
      activeModel:       config.defaultModel   ?? "",
      activeProvider:    config.defaultProvider ?? "",
      totalTokens:       0,
      totalCostUsd:      0,
      contextWindowPct:  0,
    }) as unknown as import("../../cli/v2/state/store.js").UiState,
  };
}

export interface DispatchResult {
  result: CoreCommandResult;
  /**
   * True only when the command returned a panel with non-renderable content
   * (e.g. interactive panels with action handlers). Standard list panels are
   * rendered as text and panelFallback is false.
   */
  panelFallback: boolean;
}

/**
 * Dispatch a slash-command string using only a CoreCommandContext.
 *
 * Panel results from handlers are rendered as plain text lists so they work
 * on non-TUI channels (Telegram, Slack, Discord).
 *
 * @param input  Full command string, e.g. "/config provider list"
 * @param core   Channel's CoreCommandContext implementation
 */
export async function dispatchCoreCommand(
  input: string,
  core: CoreCommandContext,
): Promise<DispatchResult> {
  log.gateway.debug("core-dispatcher.dispatch: entry", { input });

  const resolved = resolveCommand(input);
  if (!resolved) {
    return {
      result: { kind: "error", text: `Unknown command: ${input.split(" ")[0]}` },
      panelFallback: false,
    };
  }

  const handler = resolved.subcommand?.handler ?? resolved.spec.handler;
  if (!handler) {
    // No top-level handler — show usage for commands that have subcommands
    if (resolved.spec.subcommands?.length) {
      const subs = resolved.spec.subcommands.map((s) => `  ${resolved.spec.name} ${s.name} — ${s.description}`).join("\n");
      return {
        result: { kind: "system-message", text: `${resolved.spec.name}\n\n${subs}` },
        panelFallback: false,
      };
    }
    return {
      result: { kind: "error", text: `${resolved.spec.name}: no handler registered` },
      panelFallback: false,
    };
  }

  const ctx = asTuiContext(core);
  const raw = await handler(ctx, resolved.args);
  log.gateway.debug("core-dispatcher.dispatch: exit", { kind: raw.kind });

  if (raw.kind === "panel") {
    // Render the panel payload as a plain-text list for non-TUI channels.
    // PanelPayload: { title: string; items: PanelItem[]; emptyText?: string }
    // PanelItem:    { id: string; label: string; meta?: string }
    const payload = raw.payload as {
      title?: string;
      items?: Array<{ label: string; meta?: string }>;
      emptyText?: string;
    };
    const items = payload.items ?? [];
    if (items.length === 0) {
      return {
        result: { kind: "system-message", text: payload.emptyText ?? "No results." },
        panelFallback: false,
      };
    }
    const header = payload.title ? `${payload.title}\n\n` : "";
    const body = items.map((i) => (i.meta ? `${i.label} — ${i.meta}` : i.label)).join("\n");
    return {
      result: { kind: "system-message", text: header + body },
      panelFallback: false,
    };
  }

  return { result: raw as CoreCommandResult, panelFallback: false };
}
