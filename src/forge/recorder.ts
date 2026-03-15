import { randomUUID } from 'node:crypto';
import { Logger } from '../logger.js';
import type { DemoRecording, DemoStep, ForgeConfig } from './types.js';

const DEFAULT_CONFIG: ForgeConfig = {
  maxSteps: 50,
  maxStepOutputChars: 500,
  autoGenerateSkill: true,
};

const logger = new Logger('FORGE');

export class DemoRecorder {
  private config: ForgeConfig;
  private active: Map<string, DemoRecording> = new Map();
  private completed: DemoRecording[] = [];

  constructor(config?: Partial<ForgeConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  startRecording(name: string, description: string, cwd: string): string {
    const id = randomUUID();
    const recording: DemoRecording = {
      id,
      name,
      description,
      steps: [],
      startedAt: Date.now(),
      completedAt: 0,
      context: { cwd },
    };
    this.active.set(id, recording);
    logger.info(`Started recording "${name}" (${id})`);
    return id;
  }

  recordStep(recordingId: string, step: Omit<DemoStep, 'order' | 'timestamp'>): void {
    const recording = this.active.get(recordingId);
    if (!recording) {
      logger.warn(`No active recording found for ID ${recordingId}`);
      return;
    }

    if (recording.steps.length >= this.config.maxSteps) {
      logger.warn(`Recording "${recording.name}" reached max steps (${this.config.maxSteps}), ignoring new step`);
      return;
    }

    const fullStep: DemoStep = {
      ...step,
      order: recording.steps.length + 1,
      timestamp: Date.now(),
      output: step.output
        ? step.output.slice(0, this.config.maxStepOutputChars)
        : undefined,
    };

    recording.steps.push(fullStep);
    logger.debug(`Recorded step ${fullStep.order} (${step.type}: ${step.action})`);
  }

  endRecording(recordingId: string): DemoRecording {
    const recording = this.active.get(recordingId);
    if (!recording) {
      throw new Error(`No active recording found for ID ${recordingId}`);
    }

    recording.completedAt = Date.now();
    this.active.delete(recordingId);
    this.completed.push(recording);
    logger.info(`Completed recording "${recording.name}" with ${recording.steps.length} step(s)`);
    return recording;
  }

  isRecording(recordingId: string): boolean {
    return this.active.has(recordingId);
  }

  getRecording(recordingId: string): DemoRecording | null {
    return this.active.get(recordingId) ?? this.completed.find(r => r.id === recordingId) ?? null;
  }

  listRecordings(): DemoRecording[] {
    return [...this.completed];
  }
}
