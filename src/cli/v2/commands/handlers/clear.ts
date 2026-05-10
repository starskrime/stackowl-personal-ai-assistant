import { makeMessage } from "../../../../gateway/core.js";
import type { CommandHandler } from "../registry.js";

export const handleClear: CommandHandler = async (ctx, _args) => {
  const gateway = ctx.getOwlGateway();
  const msg = makeMessage("cli-v2", "local", "/reset", "cli-v2:local");
  if (msg) {
    await gateway.handle(msg);
  }
  return { kind: "action" };
};
