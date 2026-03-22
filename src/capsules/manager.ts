/**
 * StackOwl — Capsule Manager
 *
 * Creates, stores, and delivers time capsules — messages from past-self
 * that trigger on date, condition, or event.
 */

import type { ModelProvider } from '../providers/base.js';
import type { TimeCapsule, CapsuleTrigger } from './types.js';
import { join } from 'node:path';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { existsSync, mkdirSync } from 'node:fs';
import { log } from '../logger.js';

export class CapsuleManager {
  private provider: ModelProvider;
  private capsuleDir: string;

  constructor(provider: ModelProvider, workspacePath: string) {
    this.provider = provider;
    this.capsuleDir = join(workspacePath, 'capsules');
    if (!existsSync(this.capsuleDir)) mkdirSync(this.capsuleDir, { recursive: true });
  }

  /**
   * Create a new time capsule.
   */
  async create(
    message: string,
    trigger: CapsuleTrigger,
    contextSnapshot?: string,
    owlName?: string,
  ): Promise<TimeCapsule> {
    const capsule: TimeCapsule = {
      id: `capsule_${Date.now()}`,
      message,
      contextSnapshot,
      trigger,
      status: 'sealed',
      createdAt: new Date().toISOString(),
      owlName,
    };

    await this.save(capsule);
    return capsule;
  }

  /**
   * List all capsules, optionally filtered by status.
   */
  async list(status?: TimeCapsule['status']): Promise<TimeCapsule[]> {
    if (!existsSync(this.capsuleDir)) return [];
    const files = await readdir(this.capsuleDir);
    const capsules: TimeCapsule[] = [];

    for (const file of files) {
      if (!file.endsWith('.json')) continue;
      try {
        const data = await readFile(join(this.capsuleDir, file), 'utf-8');
        const capsule: TimeCapsule = JSON.parse(data);
        if (!status || capsule.status === status) capsules.push(capsule);
      } catch { /* skip */ }
    }

    return capsules.sort((a, b) =>
      new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
    );
  }

  /**
   * Check all sealed capsules and deliver any that are ready.
   * Returns delivered capsules.
   */
  async checkDelivery(): Promise<TimeCapsule[]> {
    const sealed = await this.list('sealed');
    const delivered: TimeCapsule[] = [];
    const now = new Date();

    for (const capsule of sealed) {
      const shouldDeliver = await this.evaluateTrigger(capsule.trigger, now);
      if (shouldDeliver) {
        capsule.status = 'delivered';
        capsule.deliveredAt = now.toISOString();
        await this.save(capsule);
        delivered.push(capsule);
      }
    }

    return delivered;
  }

  /**
   * Format a delivered capsule as a user-facing message.
   */
  formatDelivery(capsule: TimeCapsule): string {
    const createdDate = new Date(capsule.createdAt).toLocaleDateString();
    const parts: string[] = [
      `**Time Capsule from ${createdDate}**`,
      '',
      capsule.message,
    ];

    if (capsule.contextSnapshot) {
      parts.push('', `*Context when sealed:* ${capsule.contextSnapshot}`);
    }

    return parts.join('\n');
  }

  /**
   * Open (deliver) a specific capsule by ID, regardless of trigger.
   */
  async open(id: string): Promise<TimeCapsule | null> {
    const capsule = await this.get(id);
    if (!capsule) return null;
    if (capsule.status === 'delivered') return capsule;

    capsule.status = 'delivered';
    capsule.deliveredAt = new Date().toISOString();
    await this.save(capsule);
    return capsule;
  }

  // ─── Private ─────────────────────────────────────────────

  private async get(id: string): Promise<TimeCapsule | null> {
    const path = join(this.capsuleDir, `${id}.json`);
    if (!existsSync(path)) return null;
    try {
      const data = await readFile(path, 'utf-8');
      return JSON.parse(data);
    } catch {
      return null;
    }
  }

  private async evaluateTrigger(trigger: CapsuleTrigger, now: Date): Promise<boolean> {
    switch (trigger.type) {
      case 'date':
        if (!trigger.date) return false;
        return now >= new Date(trigger.date);

      case 'condition':
        if (!trigger.condition) return false;
        return this.evaluateCondition(trigger.condition);

      case 'event':
        // Event triggers are checked externally via deliverOnEvent()
        return false;

      default:
        return false;
    }
  }

  private async evaluateCondition(condition: string): Promise<boolean> {
    try {
      const resp = await this.provider.chat(
        [{
          role: 'user',
          content:
            `Evaluate whether this condition is likely met right now (${new Date().toLocaleDateString()}):\n\n` +
            `Condition: "${condition}"\n\n` +
            `Respond with ONLY "yes" or "no".`,
        }],
        undefined,
        { temperature: 0, maxTokens: 10 },
      );
      return resp.content.trim().toLowerCase().startsWith('yes');
    } catch {
      return false;
    }
  }

  /**
   * Deliver capsules that match a specific event.
   */
  async deliverOnEvent(eventName: string): Promise<TimeCapsule[]> {
    const sealed = await this.list('sealed');
    const delivered: TimeCapsule[] = [];

    for (const capsule of sealed) {
      if (capsule.trigger.type === 'event' && capsule.trigger.event === eventName) {
        capsule.status = 'delivered';
        capsule.deliveredAt = new Date().toISOString();
        await this.save(capsule);
        delivered.push(capsule);
      }
    }

    return delivered;
  }

  private async save(capsule: TimeCapsule): Promise<void> {
    await writeFile(
      join(this.capsuleDir, `${capsule.id}.json`),
      JSON.stringify(capsule, null, 2),
    );
    log.engine.info(`[CapsuleManager] Saved: ${capsule.id}`);
  }
}
