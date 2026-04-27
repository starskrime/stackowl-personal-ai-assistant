import type { ModelProvider } from '../providers/base.js';
import type { VerificationStatus, VerificationResult, IntentMatchResult } from './types.js';

export interface OutcomeVerifierConfig {
  confidenceThreshold: number;
  syncStringMatch?: boolean;
}

const DEFAULT_CONFIG: OutcomeVerifierConfig = {
  confidenceThreshold: 0.7,
  syncStringMatch: true,
};

export class OutcomeVerifier {
  private config: OutcomeVerifierConfig;
  private statusMap: Map<string, VerificationStatus> = new Map();
  private records: Map<string, VerificationResult> = new Map();

  constructor(config: Partial<OutcomeVerifierConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  async verify(
    taskId: string,
    result: string,
    intent: string,
    provider?: ModelProvider,
  ): Promise<VerificationResult> {
    this.logBehavioral('started', taskId);

    if (this.config.syncStringMatch && this.verifySync(result, intent)) {
      const verification: VerificationResult = {
        status: 'passed',
        confidence: 1.0,
        matchDetails: 'String match verified synchronously',
        checkedAt: new Date().toISOString(),
      };
      this.updateStatus(taskId, 'passed');
      this.records.set(taskId, verification);
      this.logBehavioral('completed', taskId);
      return verification;
    }

    if (!provider) {
      const verification: VerificationResult = {
        status: 'pending',
        confidence: 0,
        matchDetails: 'No LLM provider available for semantic verification',
        checkedAt: new Date().toISOString(),
      };
      this.updateStatus(taskId, 'pending');
      this.records.set(taskId, verification);
      return verification;
    }

    const matchResult = await this.checkIntentMatch(result, intent, provider);
    const status: VerificationStatus = matchResult.confidence >= this.config.confidenceThreshold
      ? 'passed'
      : 'failed';

    const verification: VerificationResult = {
      status,
      confidence: matchResult.confidence,
      matchDetails: matchResult.reasoning,
      checkedAt: new Date().toISOString(),
    };

    this.updateStatus(taskId, status);
    this.records.set(taskId, verification);
    this.logBehavioral(status === 'passed' ? 'completed' : 'failed', taskId);
    return verification;
  }

  verifySync(result: string, intent: string): boolean {
    const normalizedResult = result.toLowerCase().trim();
    const normalizedIntent = intent.toLowerCase().trim();

    if (normalizedResult === normalizedIntent) return true;
    if (normalizedResult.includes(normalizedIntent)) return true;
    if (normalizedIntent.includes(normalizedResult)) return true;

    const intentWords = normalizedIntent.split(/\s+/);
    const resultWords = normalizedResult.split(/\s+/);
    if (intentWords.length <= 3) {
      return intentWords.every(word => resultWords.some(rw => rw.includes(word) || word.includes(rw)));
    }

    return false;
  }

  private async checkIntentMatch(
    result: string,
    intent: string,
    provider: ModelProvider,
  ): Promise<IntentMatchResult> {
    const prompt =
      `You are a task completion verifier. Your job is to assess whether the result achieves the original intent.\n\n` +
      `Original intent: ${intent}\n\n` +
      `Result: ${result}\n\n` +
      `Assess whether the result fulfills the intent. Consider:\n` +
      `1. Does the result address the core request?\n` +
      `2. Are there missing pieces or incorrect elements?\n` +
      `3. Is the result substantive (not just text produced)?\n\n` +
      `Return a JSON object with:\n` +
      `- isMatch: boolean (true if intent is fulfilled)\n` +
      `- confidence: number (0.0 to 1.0, how confident you are)\n` +
      `- reasoning: string (brief explanation)\n\n` +
      `Return ONLY the JSON object, no other text.`;

    try {
      const response = await Promise.race([
        provider.chat(
          [{ role: 'user', content: prompt }],
          undefined,
          { temperature: 0, maxTokens: 200 },
        ),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('Intent match check timeout')), 10000),
        ),
      ]);

      const content = response.content.trim();
      let parsed: { isMatch: boolean; confidence: number; reasoning: string };

      try {
        parsed = JSON.parse(content);
      } catch {
        const isMatch = content.toLowerCase().includes('"isMatch": true') ||
          content.toLowerCase().includes('"ismatch" : true') ||
          content.toLowerCase().includes('ismatch: true');
        const confMatch = content.match(/"confidence"\s*:\s*([0-9.]+)/);
        parsed = {
          isMatch: isMatch || false,
          confidence: confMatch ? parseFloat(confMatch[1]) : 0.5,
          reasoning: content.slice(0, 200),
        };
      }

      return {
        isMatch: parsed.isMatch ?? false,
        confidence: Math.max(0, Math.min(1, parsed.confidence ?? 0.5)),
        reasoning: parsed.reasoning ?? 'No reasoning provided',
      };
    } catch {
      return {
        isMatch: false,
        confidence: 0,
        reasoning: 'LLM verification failed',
      };
    }
  }

  private logBehavioral(event: 'started' | 'completed' | 'failed', taskId: string): void {
    const timestamp = new Date().toISOString();
    switch (event) {
      case 'started':
        console.log(
          `${timestamp} INFO [OutcomeVerifier] behavioral.verification.started taskId=${taskId}`,
        );
        break;
      case 'completed':
        console.log(
          `${timestamp} INFO [OutcomeVerifier] behavioral.verification.completed taskId=${taskId}`,
        );
        break;
      case 'failed':
        console.log(
          `${timestamp} INFO [OutcomeVerifier] behavioral.verification.failed taskId=${taskId}`,
        );
        break;
    }
  }

  getStatus(taskId: string): VerificationStatus | undefined {
    return this.statusMap.get(taskId);
  }

  updateStatus(taskId: string, status: VerificationStatus): void {
    this.statusMap.set(taskId, status);
  }

  getVerification(taskId: string): VerificationResult | undefined {
    return this.records.get(taskId);
  }

  getAllStatuses(): Map<string, VerificationStatus> {
    return new Map(this.statusMap);
  }
}

export const outcomeVerifier = new OutcomeVerifier();