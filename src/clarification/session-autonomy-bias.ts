export class SessionAutonomyBias {
  private _dismissCount = 0;

  get dismissCount(): number {
    return this._dismissCount;
  }

  recordDismissal(): void {
    this._dismissCount++;
  }

  toPromptContext(): string {
    if (this._dismissCount === 0) return '';
    if (this._dismissCount === 1) {
      return 'user dismissed 1 clarification question this session — lean toward PROCEED when reasonable.';
    }
    return `user dismissed ${this._dismissCount} clarification questions this session — strongly prefer PROCEED unless truly impossible to proceed.`;
  }
}
