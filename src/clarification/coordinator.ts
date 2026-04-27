import { log } from '../logger.js';
import type { ClarificationQuestion } from './types.js';

interface CoordinatedQuestion {
  id: string;
  sourceModule: 'AmbiguityDetector' | 'PreExecutionConfirmer' | 'UnclaritySurfacer';
  question: ClarificationQuestion | null;
  sessionKey: string;
  createdAt: number;
}

export class ClarificationCoordinator {
  private recentQuestions: CoordinatedQuestion[] = [];
  private readonly SESSION_WINDOW_MS = 5 * 60 * 1000; // 5 minutes

  shouldAsk(
    moduleName: CoordinatedQuestion['sourceModule'],
    question: ClarificationQuestion | null,
    sessionKey: string
  ): boolean {
    const now = Date.now();

    // Clean old questions
    this.recentQuestions = this.recentQuestions.filter(
      q => now - q.createdAt < this.SESSION_WINDOW_MS
    );

    // If no question to ask, don't interfere
    if (!question) return false;

    // Check for semantically similar question asked recently by ANY module
    // Also suppress same question ID from different module (deduplicated elsewhere)
    const similarQuestion = this.recentQuestions.find(rq => {
      if (rq.sessionKey !== sessionKey) return false;
      if (rq.id === question.id && rq.sourceModule !== moduleName) return true;
      return this.isSemanticallySimilar(rq.question?.question || '', question.question);
    });

    if (similarQuestion) {
      log.engine.info(`[ClarificationCoordinator] Suppressing duplicate question from ${moduleName} (matches ${similarQuestion.sourceModule})`);
      return false;
    }

    // Record this question
    this.recentQuestions.push({
      id: question.id,
      sourceModule: moduleName,
      question,
      sessionKey,
      createdAt: now,
    });

    return true;
  }

  private isSemanticallySimilar(text1: string, text2: string): boolean {
    const words1 = text1.toLowerCase().split(/\s+/).filter(w => w.length > 3);
    const words2 = text2.toLowerCase().split(/\s+/).filter(w => w.length > 3);

    const set1 = new Set(words1);
    const set2 = new Set(words2);
    const intersection = words1.filter(w => set2.has(w));
    let unionLen = 0;
    const seen: string[] = [];
    set1.forEach(w => { if (!seen.includes(w)) { seen.push(w); unionLen++; } });
    set2.forEach(w => { if (!seen.includes(w)) { seen.push(w); unionLen++; } });

    return intersection.length / unionLen >= 0.7;
  }

  clear(): void {
    this.recentQuestions = [];
  }
}

export const clarificationCoordinator = new ClarificationCoordinator();