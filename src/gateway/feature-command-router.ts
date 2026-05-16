import { log } from "../logger.js";
import type { GatewayResponse, GatewayMessage, GatewayCallbacks } from "./types.js";
import type { Session } from "../memory/store.js";

export interface FeatureCommandContext {
  message: GatewayMessage;
  session: Session;
  owlName: string;
  owlEmoji: string;
  gatewayCtx: import("./types.js").GatewayContext;
  sessionManager: import("./session-manager.js").ISessionManager;
  agentWatch: import("../agent-watch/index.js").AgentWatchManager | null;
  skillInjector: import("../skills/injector.js").SkillContextInjector | null;
  callbacks: GatewayCallbacks;
}

export interface IFeatureCommandHandler {
  readonly commands: readonly string[];
  handle(cmd: string, args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null>;
}

export interface IFeatureCommandRouter {
  register(handler: IFeatureCommandHandler): void;
  dispatch(input: string, ctx: FeatureCommandContext): Promise<GatewayResponse | null>;
  isCommand(input: string): boolean;
}

export class FeatureCommandRouter implements IFeatureCommandRouter {
  private readonly handlers = new Map<string, IFeatureCommandHandler>();

  register(handler: IFeatureCommandHandler): void {
    for (const cmd of handler.commands) {
      log.gateway.debug("FeatureCommandRouter.register: entry", { cmd });
      if (this.handlers.has(cmd)) {
        log.gateway.warn("FeatureCommandRouter.register: duplicate command", { cmd });
      }
      this.handlers.set(cmd.toLowerCase(), handler);
      log.gateway.debug("FeatureCommandRouter.register: exit", { cmd });
    }
  }

  isCommand(input: string): boolean {
    const cmd = this.extractCommand(input);
    return cmd !== null && this.handlers.has(cmd);
  }

  async dispatch(input: string, ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    const cmd = this.extractCommand(input);
    if (!cmd) {
      return null;
    }
    const handler = this.handlers.get(cmd);
    if (!handler) {
      log.gateway.debug("FeatureCommandRouter.dispatch: no handler", { cmd });
      return null;
    }
    const args = input.trim().split(/\s+/).slice(1);
    log.gateway.debug("FeatureCommandRouter.dispatch: entry", { cmd, argCount: args.length });
    try {
      const result = await handler.handle(cmd, args, ctx);
      log.gateway.debug("FeatureCommandRouter.dispatch: exit", { cmd, handled: result !== null });
      return result;
    } catch (err) {
      log.gateway.error("FeatureCommandRouter.dispatch: handler threw", err as Error, { cmd });
      return null;
    }
  }

  private extractCommand(input: string): string | null {
    const first = input.trim().split(/\s+/)[0] ?? "";
    return first.startsWith("/") ? first.toLowerCase() : null;
  }
}
