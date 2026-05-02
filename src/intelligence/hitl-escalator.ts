export class HITLEscalator {
  private blockedAttempts = 0;
  private attemptSummaries: string[] = [];

  onBlocked(toolName: string, reason: string, _subgoal: string): void {
    this.blockedAttempts++;
    this.attemptSummaries.push(`${toolName}: ${reason}`);
  }

  shouldEscalate(challengeLevel: number): boolean {
    const threshold = Math.max(1, Math.min(5, Math.round(challengeLevel / 2)));
    return this.blockedAttempts >= threshold;
  }

  buildNarration(): string {
    const count = this.blockedAttempts;
    const lines = [
      `I've tried ${count} approach${count !== 1 ? "es" : ""}:`,
      ...this.attemptSummaries.map((s, i) => `  ${i + 1}. ${s}`),
      `I'm genuinely stuck. Let me ask you one focused question.`,
    ];
    return lines.join("\n");
  }

  buildQuestion(alternatives: string[]): string {
    if (alternatives.length < 2) return "How should I proceed?";
    return `Should I try (A) ${alternatives[0]} or (B) ${alternatives[1]}?`;
  }

  reset(): void {
    this.blockedAttempts = 0;
    this.attemptSummaries = [];
  }
}
