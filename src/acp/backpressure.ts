/**
 * StackOwl — ACP Backpressure
 *
 * Pull-based bounded inbox per agent.
 * When an agent's inbox is full, senders get 'backpressure' status
 * instead of dropping messages.
 */

import type { ACPMessage, DeliveryStatus } from "./types.js";

interface InboxEntry {
  message: ACPMessage;
  addedAt: number;
}

export class ACPBackpressure {
  private inboxes = new Map<string, InboxEntry[]>();
  private listeners = new Map<string, Array<() => void>>();
  private maxInboxSize: number;

  constructor(maxInboxSize: number = 100) {
    this.maxInboxSize = maxInboxSize;
  }

  /**
   * Enqueue a message to an agent's inbox.
   * Returns 'backpressure' when full, 'expired' when TTL exceeded.
   */
  enqueue(agentId: string, message: ACPMessage): DeliveryStatus {
    // Check TTL
    if (message.ttlMs) {
      const age = Date.now() - message.timestamp;
      if (age > message.ttlMs) return "expired";
    }

    let inbox = this.inboxes.get(agentId);
    if (!inbox) {
      inbox = [];
      this.inboxes.set(agentId, inbox);
    }

    // Backpressure check
    if (inbox.length >= this.maxInboxSize) {
      return "backpressure";
    }

    inbox.push({ message, addedAt: Date.now() });

    // Notify waiting consumers
    const callbacks = this.listeners.get(agentId);
    if (callbacks && callbacks.length > 0) {
      const cb = callbacks.shift()!;
      cb();
    }

    return "delivered";
  }

  /**
   * Pull the next message from an agent's inbox.
   * Returns null if empty.
   */
  dequeue(agentId: string): ACPMessage | null {
    const inbox = this.inboxes.get(agentId);
    if (!inbox || inbox.length === 0) return null;

    const entry = inbox.shift()!;

    // Check TTL on dequeue too
    if (entry.message.ttlMs) {
      const age = Date.now() - entry.message.timestamp;
      if (age > entry.message.ttlMs) {
        // Message expired, try next one
        return this.dequeue(agentId);
      }
    }

    return entry.message;
  }

  /**
   * Register a callback for when a message arrives for an agent.
   * Used to implement async pull-based consumption.
   */
  onAvailable(agentId: string, callback: () => void): void {
    let callbacks = this.listeners.get(agentId);
    if (!callbacks) {
      callbacks = [];
      this.listeners.set(agentId, callbacks);
    }
    callbacks.push(callback);
  }

  /**
   * Get inbox size for an agent.
   */
  getInboxSize(agentId: string): number {
    return this.inboxes.get(agentId)?.length ?? 0;
  }

  /**
   * Clear an agent's inbox (e.g. when agent disconnects).
   */
  clearInbox(agentId: string): void {
    this.inboxes.delete(agentId);
    this.listeners.delete(agentId);
  }

  /**
   * Prune expired messages across all inboxes.
   */
  pruneExpired(): number {
    let pruned = 0;
    const now = Date.now();

    for (const [agentId, inbox] of this.inboxes) {
      const before = inbox.length;
      const filtered = inbox.filter((entry) => {
        if (!entry.message.ttlMs) return true;
        return now - entry.message.timestamp < entry.message.ttlMs;
      });
      this.inboxes.set(agentId, filtered);
      pruned += before - filtered.length;
    }

    return pruned;
  }
}
