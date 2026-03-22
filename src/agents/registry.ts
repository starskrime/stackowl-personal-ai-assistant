/**
 * StackOwl — Agent Registry
 *
 * Simple registry for coding agents. Agents register themselves
 * and can be looked up by ID or capability.
 */

import type { AgentRegistry, CodingAgent } from "./types.js";
import { log } from "../logger.js";

export class DefaultAgentRegistry implements AgentRegistry {
  private agents = new Map<string, CodingAgent>();

  register(agent: CodingAgent): void {
    this.agents.set(agent.id, agent);
    log.engine.info(
      `[AgentRegistry] Registered "${agent.name}" [${agent.id}] — capabilities: ${agent.capabilities.join(", ")}`,
    );
  }

  unregister(id: string): void {
    this.agents.delete(id);
  }

  get(id: string): CodingAgent | undefined {
    return this.agents.get(id);
  }

  list(): CodingAgent[] {
    return [...this.agents.values()];
  }

  findByCapability(...capabilities: string[]): CodingAgent[] {
    const lower = capabilities.map((c) => c.toLowerCase());
    return [...this.agents.values()].filter((agent) =>
      lower.some((cap) =>
        agent.capabilities.some((ac) => ac.toLowerCase().includes(cap)),
      ),
    );
  }

  /**
   * Find agents that listen on a specific ACP channel.
   */
  findByChannel(channel: string): CodingAgent[] {
    return [...this.agents.values()].filter((agent) =>
      agent.acpCapabilities?.some((cap) => cap.channels.includes(channel)),
    );
  }
}
