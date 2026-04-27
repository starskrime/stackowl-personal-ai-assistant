import type { PreExecutionConfirmation } from './types.js';

export class PreExecutionConfirmer {
  private pendingConfirmations: Map<string, PreExecutionConfirmation> = new Map();
  private confirmationHistory: PreExecutionConfirmation[] = [];

  assessRequest(message: string, context: string[] = []): PreExecutionConfirmation | null {
    const confidence = this.calculateConfidence(message, context);

    if (confidence > 0.65) {
      return null;
    }

    const recentVagueness = context.slice(-3).every(msg => {
      return this.calculateConfidence(msg, []) < 0.7;
    });
    if (!recentVagueness && confidence > 0.5) {
      return null;
    }

    const summary = this.summarizeUnderstanding(message, context);
    const uncertaintyAreas = this.findUncertaintyAreas(message, context);

    const confirmation: PreExecutionConfirmation = {
      id: `confirm_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      summary,
      uncertaintyAreas,
      confidence,
      isHighStakes: false,
      confirmed: null,
      timestamp: new Date().toISOString(),
    };

    this.pendingConfirmations.set(confirmation.id, confirmation);
    return confirmation;
  }

  private findUncertaintyAreas(message: string, _context: string[]): string[] {
    const areas: string[] = [];

    if (/\bwhich\b.*\b(or|versus|vs\.)\b/i.test(message)) {
      areas.push("Multiple options present");
    }

    if (/if.*then/i.test(message) && !/then/i.test(message)) {
      areas.push("Conditional outcome not specified");
    }

    return areas;
  }

  private calculateConfidence(message: string, context: string[]): number {
    let score = 1.0;

    const ambiguousPatterns = [
      /\b(?:which|what|who|where|when|how)\b.*\?\s*$/i,
      /\[UNCERTAIN\]/i,
    ];

    if (ambiguousPatterns.some(p => p.test(message))) {
      score -= 0.4;
    }

    const contextConfidences = context.map(c => this.calculateConfidence(c, []));
    if (contextConfidences.length > 0 && contextConfidences.every(c => c < 0.6)) {
      score -= 0.2;
    }

    const words = message.split(/\s+/);
    if (words.length < 5 && score > 0.5) {
      score -= 0.1;
    }

    return Math.max(0, Math.min(1, score));
  }

  private summarizeUnderstanding(message: string, context: string[]): string {
    const words = message.split(/\s+/).slice(0, 50);
    let summary = words.join(' ');

    if (message.split(/\s+/).length > 50) {
      summary += '...';
    }

    if (context.length > 0) {
      summary += `\n\nBased on our conversation, I understand you want me to help with the above task.`;
    }

    return summary;
  }

  getConfirmationQuestion(confirmation: PreExecutionConfirmation): string {
    const lines = [
      `Here's my understanding:`,
      ``,
      `${confirmation.summary}`,
      ``,
    ];

    if (confirmation.uncertaintyAreas.length > 0) {
      lines.push(`**Areas I'm uncertain about:**`);
      confirmation.uncertaintyAreas.forEach(area => {
        lines.push(`- ${area}`);
      });
      lines.push(``);
    }

    lines.push(`Confidence: ${Math.round(confirmation.confidence * 100)}%`);

    if (confirmation.isHighStakes) {
      lines.push(``);
      lines.push(`⚠️ This appears to be a high-stakes request. Please confirm my understanding is correct.`);
    } else {
      lines.push(``);
      lines.push(`Did I understand correctly?`);
    }

    return lines.join('\n');
  }

  confirm(confirmationId: string): boolean {
    const confirmation = this.pendingConfirmations.get(confirmationId);
    if (!confirmation) return false;

    confirmation.confirmed = true;
    this.confirmationHistory.push(confirmation);
    this.pendingConfirmations.delete(confirmationId);
    return true;
  }

  correct(confirmationId: string, _correction: string): boolean {
    const confirmation = this.pendingConfirmations.get(confirmationId);
    if (!confirmation) return false;

    confirmation.confirmed = false;
    this.confirmationHistory.push(confirmation);
    this.pendingConfirmations.delete(confirmationId);
    return true;
  }

  getPendingConfirmation(id: string): PreExecutionConfirmation | undefined {
    return this.pendingConfirmations.get(id);
  }

  getPendingConfirmations(): PreExecutionConfirmation[] {
    return Array.from(this.pendingConfirmations.values());
  }

  hasPendingConfirmation(): boolean {
    return this.pendingConfirmations.size > 0;
  }

  cancelAll(): void {
    this.pendingConfirmations.clear();
  }
}

export const preExecutionConfirmer = new PreExecutionConfirmer();