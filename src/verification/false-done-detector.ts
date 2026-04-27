import type { ModelProvider } from '../providers/base.js';

export interface FalseDoneResult {
  isFalseDone: boolean;
  reason?: string;
  suggestedCorrection?: string;
  confidence: number;
}

export interface FalseDoneDetectorConfig {
  confidenceThreshold: number;
  minResultLength?: number;
}

const DEFAULT_CONFIG: FalseDoneDetectorConfig = {
  confidenceThreshold: 0.7,
  minResultLength: 10,
};

export class FalseDoneDetector {
  private config: FalseDoneDetectorConfig;
  private provider?: ModelProvider;
  private detectionHistory: Map<string, FalseDoneResult> = new Map();
  private pendingSelfCorrection: Map<string, FalseDoneResult> = new Map();

  constructor(provider?: ModelProvider, config: Partial<FalseDoneDetectorConfig> = {}) {
    this.provider = provider;
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  async detect(
    taskId: string,
    result: string,
    intent: string,
    provider?: ModelProvider,
  ): Promise<FalseDoneResult> {
    const effectiveProvider = provider ?? this.provider;

    if (result.length < (this.config.minResultLength ?? 10)) {
      const falseResult: FalseDoneResult = {
        isFalseDone: true,
        reason: `Result too short (${result.length} chars, minimum ${this.config.minResultLength})`,
        suggestedCorrection: 'Provide more substantial result',
        confidence: 1.0,
      };
      this.recordDetection(taskId, falseResult);
      this.pendingSelfCorrection.set(taskId, falseResult);
      this.logBehavioral('false_done_detected', taskId);
      this.logBehavioral('self_correct_triggered', taskId);
      return falseResult;
    }

    const doneCheck = await this.checkDoneStatus(result, intent, effectiveProvider);

    if (doneCheck.isDone && doneCheck.hasEvidence) {
      const falseResult: FalseDoneResult = {
        isFalseDone: false,
        confidence: doneCheck.confidence,
      };
      this.recordDetection(taskId, falseResult);
      return falseResult;
    }

    const falseResult: FalseDoneResult = {
      isFalseDone: true,
      reason: doneCheck.concerns ?? 'Task completion not verified',
      suggestedCorrection: 'Provide actual work evidence (file paths, outputs, code)',
      confidence: doneCheck.confidence,
    };
    this.recordDetection(taskId, falseResult);
    this.pendingSelfCorrection.set(taskId, falseResult);
    this.logBehavioral('false_done_detected', taskId);
    this.logBehavioral('self_correct_triggered', taskId);
    return falseResult;
  }

  private async checkDoneStatus(
    result: string,
    intent: string,
    provider?: ModelProvider,
  ): Promise<{ isDone: boolean; hasEvidence: boolean; evidenceTypes: string[]; confidence: number; concerns: string | null }> {
    if (!provider) {
      const hasDoneMarker = /\[DONE\]|task\s+completed|finished|all\s+done/i.test(result);
      return {
        isDone: hasDoneMarker,
        hasEvidence: hasDoneMarker,
        evidenceTypes: hasDoneMarker ? ['marker_found'] : [],
        confidence: hasDoneMarker ? 0.5 : 0,
        concerns: hasDoneMarker ? 'No evidence provided with done marker' : 'No LLM provider available',
      };
    }

    const prompt =
      `Analyze this assistant response and determine if the task was actually completed vs just claimed to be done.\n\n` +
      `Response: "${result.replace(/"/g, '\\"')}"\n\n` +
      `Intent: "${intent.replace(/"/g, '\\"')}"\n\n` +
      `Look for:\n` +
      `1. Actual work shown (file paths, command outputs, code, results)\n` +
      `2. Just "[DONE]" or "finished" without evidence\n` +
      `3. Partial completion vs full completion\n` +
      `4. Evidence of deliverables\n\n` +
      `Respond with JSON:\n` +
      `{\n` +
      `  "isDone": boolean,\n` +
      `  "hasEvidence": boolean,\n` +
      `  "evidenceTypes": ["file_created", "command_output", "code_shown", "result_described"] or [],\n` +
      `  "confidence": 0.0-1.0,\n` +
      `  "concerns": "What seems incomplete or concerning" or null\n` +
      `}`;

    try {
      const response = await Promise.race([
        provider.chat(
          [{ role: 'user', content: prompt }],
          undefined,
          { temperature: 0, maxTokens: 300 },
        ),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('Done status check timeout')), 10000),
        ),
      ]);

      const content = response.content.trim();
      const parsed = JSON.parse(content);

      return {
        isDone: parsed.isDone ?? false,
        hasEvidence: parsed.hasEvidence ?? false,
        evidenceTypes: parsed.evidenceTypes ?? [],
        confidence: Math.max(0, Math.min(1, parsed.confidence ?? 0.5)),
        concerns: parsed.concerns ?? null,
      };
    } catch {
      return {
        isDone: false,
        hasEvidence: false,
        evidenceTypes: [],
        confidence: 0,
        concerns: 'LLM done status check failed',
      };
    }
  }

  shouldSelfCorrect(taskId: string): boolean {
    const correction = this.pendingSelfCorrection.get(taskId);
    return correction !== undefined && correction.isFalseDone;
  }

  getPendingCorrection(taskId: string): FalseDoneResult | undefined {
    return this.pendingSelfCorrection.get(taskId);
  }

  clearPendingCorrection(taskId: string): void {
    this.pendingSelfCorrection.delete(taskId);
  }

  private recordDetection(taskId: string, result: FalseDoneResult): void {
    this.detectionHistory.set(taskId, result);
  }

  getDetectionHistory(taskId: string): FalseDoneResult | undefined {
    return this.detectionHistory.get(taskId);
  }

  private logBehavioral(event: 'false_done_detected' | 'self_correct_triggered', taskId: string): void {
    const timestamp = new Date().toISOString();
    switch (event) {
      case 'false_done_detected':
        console.log(
          `${timestamp} INFO [FalseDoneDetector] behavioral.detection.false_done_detected taskId=${taskId}`,
        );
        break;
      case 'self_correct_triggered':
        console.log(
          `${timestamp} INFO [FalseDoneDetector] behavioral.detection.self_correct_triggered taskId=${taskId}`,
        );
        break;
    }
  }
}

export const falseDoneDetector = new FalseDoneDetector();