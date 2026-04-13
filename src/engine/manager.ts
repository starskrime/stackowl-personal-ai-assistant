import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { EventBus } from "../events/bus.js";
import { log } from "../logger.js";

export interface ManagerContext {
  provider: ModelProvider;
  owl: OwlInstance;
  eventBus: EventBus;
  sendToUser: (message: string) => Promise<void>;
}

/**
 * Lightweight Manager Agent.
 * Listens for `agent:ping_request` events and processes them in the background,
 * separating "Thinking" from "Executing".
 * It checks the global state (tracked internally) and drops ping requests 
 * if the main OwlEngine is currently busy executing a task.
 */
export class ManagerEngine {
  private ctx: ManagerContext;
  private busySessions: Set<string> = new Set();

  constructor(ctx: ManagerContext) {
    this.ctx = ctx;

    // Track state changes emitted by the Gateway
    this.ctx.eventBus.on("agent:state_change", (payload) => {
      if (payload.state === "IDLE") {
        this.busySessions.delete(payload.sessionId);
      } else {
        this.busySessions.add(payload.sessionId);
      }
    });

    // Handle incoming proactive ping requests
    this.ctx.eventBus.on("agent:ping_request", async (payload: { prompt: string, sessionId?: string }) => {
      // If a specific session ping is requested, check only that session
      // Otherwise, assume it's a global background job and check if ANY session is busy
      const isBusy = payload.sessionId 
        ? this.busySessions.has(payload.sessionId) 
        : this.busySessions.size > 0;

      if (isBusy) {
        log.engine.info("[Manager] Dropping proactive ping request. Main engine is currently busy.");
        return;
      }

      await this.processPing(payload.prompt, payload.sessionId);
    });
  }

  private async processPing(prompt: string, sessionId?: string): Promise<void> {
    try {
      // Re-architect the prompt to guarantee it comes from a System persona.
      // This enforces that the LLM knows it is generating a proactive message to the user,
      // avoiding the "No follow-up on my end" inversion bug.
      const messages = [
        {
          role: "system",
          content: `You are ${this.ctx.owl.persona.name}, the user's AI assistant. 
The user is currently idle. Proactively evaluate the following internal thought context and compose 
a friendly, concise outreach message to the user based on it.
Do NOT roleplay as if the user sent this context to you. You are initializing the conversation.`,
        },
        {
          role: "user",
          content: `Internal Context:\n${prompt}`,
        },
      ];

      const response = await this.ctx.provider.chat(messages, undefined, {
        temperature: 0.6,
      });

      // DOUBLE-CHECK LOCK: LLM generation takes seconds. The user might have sent a message 
      // WHILE we were thinking. We must check the lock again right before sending to prevent
      // interleaving a proactive ping over an active streaming response.
      const isStillBusy = sessionId 
        ? this.busySessions.has(sessionId) 
        : this.busySessions.size > 0;

      if (isStillBusy) {
        log.engine.info("[Manager] Post-generation drop: Engine became busy while formatting ping.");
        return;
      }

      await this.ctx.sendToUser(response.content);
    } catch (e) {
      log.engine.warn(`[Manager] Failed to process ping request: ${e}`);
    }
  }
}
