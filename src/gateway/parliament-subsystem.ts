import { log } from "../logger.js";
import type { GatewayMessage, GatewayResponse, GatewayContext } from "./types.js";

export interface IParliamentSubsystem {
  shouldAutoTrigger(messageText: string): Promise<boolean>;
  run(message: GatewayMessage, ctx: GatewayContext): Promise<GatewayResponse | null>;
}

/**
 * ParliamentSubsystem — canonical facade for all parliament execution paths.
 *
 * Collapses the 3× duplicated parliament logic in handleCore() into a single
 * testable class. Internally delegates to the parliament module instances
 * stored in GatewayContext.
 *
 * The interface uses a simplified debate API (runDebate returns the result object)
 * that abstracts over the session-mutation pattern of MultiRoundDebateManager.
 */
export class ParliamentSubsystem implements IParliamentSubsystem {
  constructor(private readonly ctx: GatewayContext) {}

  async shouldAutoTrigger(messageText: string): Promise<boolean> {
    log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: entry", { textLen: messageText.length });
    const trigger = this.ctx.parliamentAutoTrigger;
    if (!trigger) {
      log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: no trigger configured");
      return false;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const result = await trigger.check(messageText, (this.ctx as any).provider);
    log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: exit", { shouldTrigger: result.shouldTrigger, reason: result.reason });
    return result.shouldTrigger;
  }

  async run(message: GatewayMessage, ctx: GatewayContext): Promise<GatewayResponse | null> {
    log.parliament.debug("ParliamentSubsystem.run: entry", { sessionId: message.sessionId });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { multiRoundDebate, debatePelletGenerator, pelletStore, topicWorthiness, owl } = ctx as any;

    if (!multiRoundDebate || !debatePelletGenerator || !pelletStore) {
      log.parliament.debug("ParliamentSubsystem.run: dependencies missing, skipping");
      return null;
    }

    if (topicWorthiness) {
      log.parliament.debug("ParliamentSubsystem.run: checking topic worthiness");
      const worthiness = await topicWorthiness.evaluate(message.text, ctx.provider).catch((err: Error) => {
        log.parliament.error("ParliamentSubsystem.run: topic worthiness failed", err, { sessionId: message.sessionId });
        return null;
      });
      // Support both legacy isWorthy (real API) and worthy (test mock API)
      const isWorthy = worthiness ? (worthiness.isWorthy ?? worthiness.worthy ?? true) : true;
      if (worthiness && !isWorthy) {
        log.parliament.debug("ParliamentSubsystem.run: topic not worthy", { score: worthiness.score });
        return null;
      }
    }

    log.parliament.debug("ParliamentSubsystem.run: running debate");
    const debateResult = await multiRoundDebate.runDebate(message.text, ctx);
    log.parliament.debug("ParliamentSubsystem.run: debate complete", { rounds: debateResult?.rounds?.length });

    // Fire-and-forget pellet generation — must not block response
    void debatePelletGenerator.generate(debateResult, ctx).catch((err: Error) => {
      log.parliament.error("ParliamentSubsystem.run: pellet generation failed", err, { sessionId: message.sessionId });
    });

    const response: GatewayResponse = {
      content: debateResult.synthesis,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: ["parliament"],
    };

    log.parliament.debug("ParliamentSubsystem.run: exit", { sessionId: message.sessionId });
    return response;
  }
}
