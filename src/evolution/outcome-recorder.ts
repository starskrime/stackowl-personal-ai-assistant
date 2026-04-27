import type { OutcomeRecord, OutcomeStatus } from './types.js';

export class OutcomeRecorder {
  private records: OutcomeRecord[] = [];

  record(params: {
    toolName: string;
    taskType: string;
    status: OutcomeStatus;
    errorMessage?: string;
    metadata?: Record<string, unknown>;
  }): OutcomeRecord {
    const record: OutcomeRecord = {
      toolName: params.toolName,
      taskType: params.taskType,
      timestamp: new Date().toISOString(),
      status: params.status,
      errorMessage: params.errorMessage,
      metadata: params.metadata,
    };
    this.records.push(record);
    return record;
  }

  getRecords(): OutcomeRecord[] {
    return [...this.records];
  }

  getRecordsByTool(toolName: string): OutcomeRecord[] {
    return this.records.filter((r) => r.toolName === toolName);
  }

  getRecordsByTaskType(taskType: string): OutcomeRecord[] {
    return this.records.filter((r) => r.taskType === taskType);
  }

  getRecentRecords(count: number): OutcomeRecord[] {
    return this.records.slice(-count);
  }

  getErrorRate(): number {
    if (this.records.length === 0) return 0;
    const failures = this.records.filter((r) => r.status === 'failure').length;
    return failures / this.records.length;
  }

  clear(): void {
    this.records = [];
  }
}

export const outcomeRecorder = new OutcomeRecorder();
