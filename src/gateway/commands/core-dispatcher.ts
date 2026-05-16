/**
 * Channel-agnostic command dispatcher and context factory.
 *
 * Any channel adapter (Telegram, Slack, Discord, …) can use this to invoke
 * any registered command without needing a TUI CommandContext.  Handlers that
 * only use CoreCommandContext will work automatically; handlers that require
 * bridge/store will receive stub no-ops and must not call them.
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
 * This lets CoreCommandHandler-based handlers run on non-TUI channels safely.
 * Handlers that call bridge or getStore will get harmless no-ops.
 */
function asTuiContext(core: CoreCommandContext): CommandContext {
  return {
    ...core,
    bridge: {
      emit: () => {},
      openPanel: () => {},
      closePanel: () => {},
      requestOnboardingView: () => {},
      on: () => () => {},
    } as unknown as import("../../cli/v2/events/bridge.js").UiBridge,
    getStore: () => ({}) as unknown as import("../../cli/v2/state/store.js").UiState,
  };
}

export interface DispatchResult {
  result: CoreCommandResult;
  /** True when the command is TUI-only (returned a panel) — channel should fall back to its own UI. */
  panelFallback: boolean;
}

/**
 * Dispatch a slash-command string using only a CoreCommandContext.
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
      result: { kind: "error", text: `Unknown command: ${input.split(" ")[0]} · try /config help` },
      panelFallback: false,
    };
  }

  const handler = resolved.subcommand?.handler ?? resolved.spec.handler;
  if (!handler) {
    return {
      result: { kind: "error", text: `${resolved.spec.name}: no handler registered` },
      panelFallback: false,
    };
  }

  const ctx = asTuiContext(core);
  const raw = await handler(ctx, resolved.args);
  log.gateway.debug("core-dispatcher.dispatch: exit", { kind: raw.kind });

  if (raw.kind === "panel") {
    return {
      result: { kind: "system-message", text: "(This command requires the interactive TUI. Run StackOwl in CLI mode.)" },
      panelFallback: true,
    };
  }

  return { result: raw as CoreCommandResult, panelFallback: false };
}
