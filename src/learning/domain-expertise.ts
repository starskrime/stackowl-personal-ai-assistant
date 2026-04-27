/**
 * StackOwl — Domain Expertise Tracker
 *
 * Tracks the owl's confidence level per domain based on tool execution outcomes.
 * Confidence scores (0.0-1.0) inform which domains the owl is strongest in,
 * enabling prioritization and adaptive caution in weaker domains.
 */

export interface DomainExpertiseRecord {
  confidence: number;
  successCount: number;
  failureCount: number;
  totalAttempts: number;
  lastUpdated: string;
}

const CONFIDENCE_INCREMENT = 0.05;
const CONFIDENCE_DECREMENT = 0.10;
const CAUTIOUS_THRESHOLD = 0.30;
const MAX_CONFIDENCE = 1.0;
const MIN_CONFIDENCE = 0.0;

export class DomainExpertiseTracker {
  private domains: Map<string, DomainExpertiseRecord> = new Map();

  private createDefaultRecord(): DomainExpertiseRecord {
    return {
      confidence: 0.5,
      successCount: 0,
      failureCount: 0,
      totalAttempts: 0,
      lastUpdated: new Date().toISOString(),
    };
  }

  private normalizeConfidence(value: number): number {
    return Math.round(Math.max(MIN_CONFIDENCE, Math.min(MAX_CONFIDENCE, value)) * 1000) / 1000;
  }

  recordToolExecution(domain: string, success: boolean): void {
    const normalized = domain.trim().toLowerCase();
    if (!normalized) return;

    const record = this.domains.get(normalized) ?? this.createDefaultRecord();

    if (success) {
      record.successCount += 1;
      record.confidence = this.normalizeConfidence(
        record.confidence + CONFIDENCE_INCREMENT,
      );
    } else {
      record.failureCount += 1;
      record.confidence = this.normalizeConfidence(
        record.confidence - CONFIDENCE_DECREMENT,
      );
    }

    record.totalAttempts += 1;
    record.lastUpdated = new Date().toISOString();

    this.domains.set(normalized, record);
  }

  getConfidence(domain: string): number {
    const normalized = domain.trim().toLowerCase();
    const record = this.domains.get(normalized);
    return record?.confidence ?? 0.5;
  }

  adjustConfidence(domain: string, delta: number): void {
    const normalized = domain.trim().toLowerCase();
    if (!normalized) return;

    const record = this.domains.get(normalized) ?? this.createDefaultRecord();
    record.confidence = this.normalizeConfidence(record.confidence + delta);
    record.lastUpdated = new Date().toISOString();

    this.domains.set(normalized, record);
  }

  isCautious(domain: string): boolean {
    return this.getConfidence(domain) < CAUTIOUS_THRESHOLD;
  }

  getTopDomains(n: number): Array<{ domain: string; confidence: number }> {
    return Array.from(this.domains.entries())
      .map(([domain, record]) => ({ domain, confidence: record.confidence }))
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, n);
  }

  getDomainStats(domain: string): DomainExpertiseRecord | undefined {
    const normalized = domain.trim().toLowerCase();
    return this.domains.get(normalized);
  }

  getAllDomains(): Map<string, DomainExpertiseRecord> {
    return new Map(this.domains);
  }
}