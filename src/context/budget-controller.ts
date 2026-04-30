export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.8);
}

export class BudgetController {
  private _consumed = 0;

  constructor(private globalCeiling: number = 8_000) {}

  get remaining(): number { return Math.max(0, this.globalCeiling - this._consumed); }
  get consumed(): number { return this._consumed; }

  reset(): void { this._consumed = 0; }

  apply(layerName: string, text: string, maxTokens: number): string {
    if (this.remaining <= 0) return "";

    const cap = Math.min(maxTokens, this.remaining);
    const capChars = Math.floor(cap * 3.8);

    if (text.length <= capChars) {
      this._consumed += estimateTokens(text);
      return text;
    }

    // Try to trim at sentence boundary
    const trimmed = this.trimAtBoundary(text, capChars);
    this._consumed += estimateTokens(trimmed);
    return trimmed;
  }

  private trimAtBoundary(text: string, maxChars: number): string {
    const hard = text.slice(0, maxChars - 12); // reserve room for suffix
    const lastSentence = Math.max(
      hard.lastIndexOf(". "),
      hard.lastIndexOf("! "),
      hard.lastIndexOf("? "),
      hard.lastIndexOf(".\n"),
    );
    if (lastSentence > maxChars * 0.5) {
      return hard.slice(0, lastSentence + 1) + " …[trimmed]";
    }
    return hard + "…[trimmed]";
  }
}
