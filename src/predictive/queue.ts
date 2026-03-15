import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { Logger } from '../logger.js';
import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { PatternAnalyzer } from './analyzer.js';
import type { PredictedTask, PredictiveConfig } from './types.js';

const log = new Logger('PREDICTIVE');

const DEFAULT_CONFIG: PredictiveConfig = {
  minPatternFrequency: 3,
  predictionHorizonHours: 24,
  maxQueuedTasks: 10,
  minConfidence: 0.6,
};

export class PredictiveQueue {
  private queue = new Map<string, PredictedTask>();
  private filePath: string;
  private config: PredictiveConfig;

  constructor(
    private analyzer: PatternAnalyzer,
    private provider: ModelProvider,
    private workspacePath: string,
    config?: Partial<PredictiveConfig>
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.filePath = join(workspacePath, 'predicted-tasks.json');
  }

  async generatePredictions(): Promise<PredictedTask[]> {
    const upcoming = this.analyzer.getUpcoming(this.config.predictionHorizonHours);
    const newTasks: PredictedTask[] = [];

    for (const pattern of upcoming) {
      const alreadyQueued = Array.from(this.queue.values()).some(
        t => t.source === pattern.id && (t.status === 'queued' || t.status === 'preparing' || t.status === 'ready')
      );
      if (alreadyQueued) continue;

      if (this.queue.size >= this.config.maxQueuedTasks) break;

      const lastTime = new Date(pattern.lastOccurred).getTime();
      const predictedTime = new Date(lastTime + pattern.avgIntervalHours * 60 * 60 * 1000).toISOString();

      const task: PredictedTask = {
        id: randomUUID(),
        action: pattern.action,
        predictedTime,
        confidence: pattern.confidence,
        source: pattern.id,
        status: 'queued',
        relatedSkills: pattern.relatedSkills,
      };

      this.queue.set(task.id, task);
      newTasks.push(task);
    }

    if (newTasks.length > 0) {
      log.info(`Generated ${newTasks.length} predicted tasks`);
    }

    return newTasks;
  }

  async prepareTask(taskId: string): Promise<void> {
    const task = this.queue.get(taskId);
    if (!task) {
      log.warn(`Task not found: ${taskId}`);
      return;
    }

    task.status = 'preparing';

    try {
      const messages: ChatMessage[] = [
        {
          role: 'system',
          content: 'You are a proactive AI assistant preparing content the user will likely need soon. Be concise and helpful.',
        },
        {
          role: 'user',
          content: `The user typically "${task.action}" around this time. Prepare a brief summary or result they'd find useful. Be proactive and helpful. Keep it under 200 words.`,
        },
      ];

      const response = await this.provider.chat(messages, undefined, { temperature: 0.5 });
      task.preparedContent = response.content;
      task.status = 'ready';
      log.info(`Prepared task: ${task.action}`);
    } catch (err) {
      log.error(`Failed to prepare task ${taskId}: ${err}`);
      task.status = 'queued';
    }
  }

  getReadyTasks(): PredictedTask[] {
    return Array.from(this.queue.values()).filter(t => t.status === 'ready');
  }

  updateTaskStatus(taskId: string, status: PredictedTask['status']): void {
    const task = this.queue.get(taskId);
    if (!task) {
      log.warn(`Task not found for status update: ${taskId}`);
      return;
    }
    task.status = status;
    log.debug(`Task ${taskId} status: ${status}`);
  }

  getQueue(): PredictedTask[] {
    return Array.from(this.queue.values());
  }

  formatForPresentation(): string {
    const ready = this.getReadyTasks();
    if (ready.length === 0) return '';

    const lines = ['I\'ve prepared some things based on your usual routine:', ''];

    for (let i = 0; i < ready.length; i++) {
      const task = ready[i];
      const pct = Math.round(task.confidence * 100);
      lines.push(`${i + 1}. **${task.action}** (${pct}% confidence) -- Ready`);
      if (task.preparedContent) {
        lines.push(`   ${task.preparedContent.split('\n')[0]}`);
      }
      lines.push('');
    }

    return lines.join('\n');
  }

  async save(): Promise<void> {
    try {
      if (!existsSync(this.workspacePath)) {
        mkdirSync(this.workspacePath, { recursive: true });
      }
      const data = Array.from(this.queue.values());
      writeFileSync(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
      log.debug(`Saved ${data.length} predicted tasks`);
    } catch (err) {
      log.error(`Failed to save predicted tasks: ${err}`);
    }
  }

  async load(): Promise<void> {
    try {
      if (!existsSync(this.filePath)) {
        log.debug('No existing predicted tasks found');
        return;
      }
      const raw = readFileSync(this.filePath, 'utf-8');
      const data: PredictedTask[] = JSON.parse(raw);
      this.queue.clear();
      for (const task of data) {
        this.queue.set(task.id, task);
      }
      log.info(`Loaded ${this.queue.size} predicted tasks`);
    } catch (err) {
      log.error(`Failed to load predicted tasks: ${err}`);
    }
  }
}
