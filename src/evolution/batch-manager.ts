import type { BatchState, EvolutionTrigger, OutcomeRecord } from './types.js';

export interface BatchManagerConfig {
  batchSize: number;
  errorThreshold: number;
  maxRecordsForErrorRate?: number;
}

const DEFAULT_CONFIG: BatchManagerConfig = {
  batchSize: 10,
  errorThreshold: 0.2,
  maxRecordsForErrorRate: 10,
};

export class EvolutionBatchManager {
  private config: BatchManagerConfig;
  private state: BatchState;
  private onEvolutionTrigger?: (trigger: EvolutionTrigger) => void;

  constructor(config: Partial<BatchManagerConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.state = this.createInitialState();
  }

  private createInitialState(): BatchState {
    return {
      counter: 0,
      records: [],
      errorCount: 0,
      successCount: 0,
    };
  }

  incrementCounter(): number {
    this.state.counter++;
    return this.state.counter;
  }

  getCounter(): number {
    return this.state.counter;
  }

  recordOutcome(outcome: Omit<OutcomeRecord, 'timestamp'>): void {
    const record: OutcomeRecord = {
      ...outcome,
      timestamp: new Date().toISOString(),
    };
    this.state.records.push(record);
    this.state.counter++;

    if (outcome.status === 'failure') {
      this.state.errorCount++;
    } else if (outcome.status === 'success') {
      this.state.successCount++;
    }
  }

  shouldTriggerEvolution(): boolean {
    if (this.state.counter >= this.config.batchSize) {
      return true;
    }

    if (this.checkErrorThreshold()) {
      return true;
    }

    return false;
  }

  private checkErrorThreshold(): boolean {
    const recentRecords = this.state.records.slice(-(this.config.maxRecordsForErrorRate ?? 10));
    if (recentRecords.length === 0) return false;

    const recentErrors = recentRecords.filter((r) => r.status === 'failure').length;
    const errorRate = recentErrors / recentRecords.length;

    return errorRate > this.config.errorThreshold;
  }

  getEvolutionTrigger(): EvolutionTrigger | null {
    if (!this.shouldTriggerEvolution()) {
      return null;
    }

    const trigger: EvolutionTrigger = {
      type: this.state.counter >= this.config.batchSize ? 'batch_size' : 'error_threshold',
      batchState: this.getState(),
      timestamp: new Date().toISOString(),
    };

    return trigger;
  }

  getState(): BatchState {
    return {
      ...this.state,
      records: [...this.state.records],
    };
  }

  reset(): void {
    this.state = this.createInitialState();
  }

  setOnEvolutionTrigger(callback: (trigger: EvolutionTrigger) => void): void {
    this.onEvolutionTrigger = callback;
  }

  triggerEvolution(): EvolutionTrigger | null {
    const trigger = this.getEvolutionTrigger();
    if (trigger) {
      this.state.lastEvolutionTimestamp = trigger.timestamp;
      if (this.onEvolutionTrigger) {
        this.onEvolutionTrigger(trigger);
      }
    }
    return trigger;
  }

  logBehavioralEvent(event: 'batch_complete' | 'triggered' | 'threshold_checked'): void {
    const timestamp = new Date().toISOString();
    const details = {
      batchSize: this.state.counter,
      errorCount: this.state.errorCount,
      successCount: this.state.successCount,
      errorRate: this.checkErrorThreshold()
        ? this.config.errorThreshold + 0.01
        : this.state.records.length > 0
          ? this.state.errorCount / this.state.records.length
          : 0,
    };

    switch (event) {
      case 'batch_complete':
        console.log(
          `${timestamp} INFO [EvolutionBatchManager] behavioral.evolution.batch_complete batchSize=${details.batchSize}`
        );
        break;
      case 'triggered':
        const triggerType = this.state.counter >= this.config.batchSize ? 'batch_size' : 'error_threshold';
        console.log(
          `${timestamp} INFO [EvolutionBatchManager] behavioral.evolution.triggered type=${triggerType} batchSize=${details.batchSize}`
        );
        break;
      case 'threshold_checked':
        console.log(
          `${timestamp} DEBUG [EvolutionBatchManager] behavioral.evolution.threshold_checked errorRate=${details.errorRate}`
        );
        break;
    }
  }
}

export const evolutionBatchManager = new EvolutionBatchManager();
