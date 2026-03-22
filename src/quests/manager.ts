/**
 * StackOwl — Quest Manager
 *
 * Creates and manages gamified learning quests with adaptive milestones.
 * Uses LLM to generate quest structure and check progress against pellets/sessions.
 */

import type { ModelProvider } from '../providers/base.js';
import type { PelletStore } from '../pellets/store.js';
import type { Quest, QuestMilestone, QuestProgress, QuestDifficulty } from './types.js';
import { join } from 'node:path';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { existsSync, mkdirSync } from 'node:fs';
import { log } from '../logger.js';

export class QuestManager {
  private provider: ModelProvider;
  private pelletStore: PelletStore;
  private questDir: string;

  constructor(
    provider: ModelProvider,
    pelletStore: PelletStore,
    workspacePath: string,
  ) {
    this.provider = provider;
    this.pelletStore = pelletStore;
    this.questDir = join(workspacePath, 'quests');
    if (!existsSync(this.questDir)) mkdirSync(this.questDir, { recursive: true });
  }

  /**
   * Create a new quest on a given topic.
   */
  async create(topic: string, difficulty: QuestDifficulty = 'intermediate'): Promise<Quest> {
    const milestones = await this.generateMilestones(topic, difficulty);

    const quest: Quest = {
      id: `quest_${Date.now()}`,
      title: `${topic} Learning Quest`,
      description: '',
      topic,
      difficulty,
      status: 'active',
      milestones,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    // Generate description via LLM
    try {
      const resp = await this.provider.chat(
        [{
          role: 'user',
          content:
            `Write a 1-2 sentence description for a learning quest about "${topic}" ` +
            `at ${difficulty} level with these milestones: ${milestones.map(m => m.title).join(', ')}. ` +
            `Be motivating and specific.`,
        }],
        undefined,
        { temperature: 0.4, maxTokens: 100 },
      );
      quest.description = resp.content.trim();
    } catch {
      quest.description = `A structured learning journey through ${topic}.`;
    }

    await this.save(quest);
    return quest;
  }

  /**
   * List all quests, optionally filtered by status.
   */
  async list(status?: Quest['status']): Promise<Quest[]> {
    if (!existsSync(this.questDir)) return [];
    const files = await readdir(this.questDir);
    const quests: Quest[] = [];

    for (const file of files) {
      if (!file.endsWith('.json')) continue;
      try {
        const data = await readFile(join(this.questDir, file), 'utf-8');
        const quest: Quest = JSON.parse(data);
        if (!status || quest.status === status) quests.push(quest);
      } catch { /* skip corrupt */ }
    }

    return quests.sort((a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
  }

  /**
   * Get progress for a specific quest, checking pellets for milestone completion.
   */
  async progress(questId: string): Promise<QuestProgress | null> {
    const quest = await this.get(questId);
    if (!quest) return null;

    // Check milestones against pellets
    await this.checkMilestones(quest);

    const completed = quest.milestones.filter(m => m.completed).length;
    const total = quest.milestones.length;
    const next = quest.milestones.find(m => !m.completed);

    let suggestion: string | undefined;
    if (next) {
      try {
        const resp = await this.provider.chat(
          [{
            role: 'user',
            content:
              `The user is working on a quest about "${quest.topic}". ` +
              `Their next milestone is: "${next.title}" — ${next.description}. ` +
              `They've completed ${completed}/${total} milestones so far. ` +
              `Give a brief (1-2 sentence) actionable suggestion for what to do next.`,
          }],
          undefined,
          { temperature: 0.3, maxTokens: 100 },
        );
        suggestion = resp.content.trim();
      } catch { /* skip */ }
    }

    // Auto-complete quest if all milestones done
    if (completed === total && quest.status === 'active') {
      quest.status = 'completed';
      quest.completedAt = new Date().toISOString();
      await this.save(quest);
    }

    return {
      questId: quest.id,
      questTitle: quest.title,
      completedMilestones: completed,
      totalMilestones: total,
      percentComplete: total > 0 ? Math.round((completed / total) * 100) : 0,
      nextMilestone: next,
      suggestion,
    };
  }

  /**
   * Pause or abandon a quest.
   */
  async updateStatus(questId: string, status: Quest['status']): Promise<Quest | null> {
    const quest = await this.get(questId);
    if (!quest) return null;
    quest.status = status;
    quest.updatedAt = new Date().toISOString();
    if (status === 'completed') quest.completedAt = new Date().toISOString();
    await this.save(quest);
    return quest;
  }

  // ─── Private ─────────────────────────────────────────────

  private async get(id: string): Promise<Quest | null> {
    const path = join(this.questDir, `${id}.json`);
    if (!existsSync(path)) return null;
    try {
      const data = await readFile(path, 'utf-8');
      return JSON.parse(data);
    } catch {
      return null;
    }
  }

  private async generateMilestones(topic: string, difficulty: QuestDifficulty): Promise<QuestMilestone[]> {
    const countByDifficulty: Record<QuestDifficulty, number> = {
      beginner: 3,
      intermediate: 5,
      advanced: 7,
      expert: 10,
    };
    const count = countByDifficulty[difficulty];

    try {
      const resp = await this.provider.chat(
        [{
          role: 'user',
          content:
            `Create ${count} learning milestones for a "${topic}" quest at ${difficulty} level.\n\n` +
            `Respond with JSON array: [{"title":"...","description":"...","completionCriteria":"..."}]\n\n` +
            `Milestones should be progressive (easy to hard) and specific. ` +
            `Each completionCriteria should describe what evidence would prove mastery.`,
        }],
        undefined,
        { temperature: 0.3, maxTokens: 800 },
      );

      const text = resp.content.trim();
      const jsonMatch = text.match(/\[[\s\S]*\]/);
      if (!jsonMatch) throw new Error('No JSON array found');

      const parsed = JSON.parse(jsonMatch[0]);
      return parsed.map((m: any, i: number) => ({
        id: `milestone_${Date.now()}_${i}`,
        title: m.title || `Milestone ${i + 1}`,
        description: m.description || '',
        completionCriteria: m.completionCriteria || '',
        completed: false,
        relatedPellets: [],
        order: i,
      }));
    } catch (err) {
      log.engine.debug(`[QuestManager] Milestone generation failed: ${err}`);
      // Fallback milestones
      return Array.from({ length: 3 }, (_, i) => ({
        id: `milestone_${Date.now()}_${i}`,
        title: `${topic} — Step ${i + 1}`,
        description: `Explore aspect ${i + 1} of ${topic}`,
        completionCriteria: `Create a pellet about this aspect of ${topic}`,
        completed: false,
        relatedPellets: [],
        order: i,
      }));
    }
  }

  private async checkMilestones(quest: Quest): Promise<void> {
    const pellets = await this.pelletStore.search(quest.topic);
    const topicPellets = pellets.slice(0, 20);

    for (const milestone of quest.milestones) {
      if (milestone.completed) continue;

      // Check if any pellet matches this milestone's criteria
      for (const pellet of topicPellets) {
        const pelletText = `${pellet.title} ${pellet.content} ${pellet.tags.join(' ')}`.toLowerCase();
        const criteriaWords = milestone.completionCriteria.toLowerCase().split(/\s+/).filter(w => w.length > 3);
        const matchCount = criteriaWords.filter(w => pelletText.includes(w)).length;

        if (criteriaWords.length > 0 && matchCount / criteriaWords.length >= 0.4) {
          milestone.completed = true;
          milestone.completedAt = new Date().toISOString();
          milestone.relatedPellets.push(pellet.id);
          break;
        }
      }
    }

    quest.updatedAt = new Date().toISOString();
    await this.save(quest);
  }

  private async save(quest: Quest): Promise<void> {
    await writeFile(
      join(this.questDir, `${quest.id}.json`),
      JSON.stringify(quest, null, 2),
    );
    log.engine.info(`[QuestManager] Saved: ${quest.id}`);
  }
}
