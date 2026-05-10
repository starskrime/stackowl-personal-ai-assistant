export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

export class ProviderCircuitBreaker {
  private state: CircuitState = "CLOSED";
  private failures = 0;
  private openedAt = 0;

  constructor(
    private readonly failureThreshold = 5,
    private readonly recoveryTimeoutMs = 30_000,
  ) {}

  /**
   * Returns true when the provider should be skipped for routing.
   * Transitions OPEN → HALF_OPEN when the recovery timeout has elapsed.
   */
  isOpen(): boolean {
    if (this.state === "CLOSED") return false;
    if (this.state === "OPEN") {
      if (Date.now() - this.openedAt >= this.recoveryTimeoutMs) {
        this.state = "HALF_OPEN";
        return false; // let one probe request through
      }
      return true;
    }
    // HALF_OPEN — one probe is already allowed through
    return false;
  }

  /**
   * Record the result of a provider API call.
   * Transitions: success → CLOSED (reset failures); failure → OPEN (at threshold).
   */
  recordResult(success: boolean): void {
    if (success) {
      this.failures = 0;
      this.state = "CLOSED";
    } else {
      this.failures++;
      if (this.state === "HALF_OPEN" || this.failures >= this.failureThreshold) {
        this.state = "OPEN";
        this.openedAt = Date.now();
        this.failures = 0;
      }
    }
  }

  getState(): CircuitState {
    return this.state;
  }

  /** For testing: fast-forward the recovery clock. */
  _forceOpenedAt(timestamp: number): void {
    this.openedAt = timestamp;
  }
}
