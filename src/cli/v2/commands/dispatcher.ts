import { resolveCommand } from "./registry.js";
import type { CommandContext, CommandResult } from "./registry.js";
import { globalBridge } from "../events/bridge.js";
import { uiStore } from "../state/store.js";

export interface CommandDispatcher {
  dispatch(input: string): Promise<CommandResult>;
}

export function createCommandDispatcher(
  ctxFactory: () => Omit<CommandContext, "bridge" | "getStore">,
): CommandDispatcher {
  const ctx: CommandContext = {
    ...ctxFactory(),
    bridge: globalBridge,
    getStore: () => uiStore.getState(),
  };

  return {
    async dispatch(input: string): Promise<CommandResult> {
      const resolved = resolveCommand(input);
      if (!resolved) {
        return { kind: "error", text: `unknown command: ${input.split(" ")[0]} · type /help` };
      }

      const handler = resolved.subcommand?.handler ?? resolved.spec.handler;
      if (!handler) {
        return { kind: "error", text: `${resolved.spec.name}: missing handler` };
      }

      const result = await handler(ctx, resolved.args);

      if (result.kind === "panel") {
        globalBridge.openPanel(resolved.subcommand?.name ?? resolved.spec.name, result.payload);
      }

      return result;
    },
  };
}
