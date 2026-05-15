import { log } from "../logger.js";

export interface ConcurrencyGateOptions {
  /** Max in-flight calls at any moment. */
  maxConcurrent: number;
  /** Max ms a caller waits before rejecting. */
  queueTimeoutMs: number;
}

export class ConcurrencyTimeoutError extends Error {
  constructor() {
    super("Timed out waiting for a provider concurrency slot");
    this.name = "ConcurrencyTimeoutError";
  }
}

export class CircuitOpenError extends Error {
  constructor() {
    super("Provider circuit is open — call rejected fast");
    this.name = "CircuitOpenError";
  }
}

export class ConcurrencyGate {
  private _inflight = 0;
  private _queue: Array<{
    resolve: () => void;
    reject: (e: Error) => void;
    timer: NodeJS.Timeout;
  }> = [];
  private _circuitOpen = false;

  constructor(private readonly opts: ConcurrencyGateOptions) {}

  /**
   * Acquire a slot. Returns a release function.
   * Throws CircuitOpenError immediately if circuit is open.
   * Throws ConcurrencyTimeoutError if no slot opens within queueTimeoutMs.
   */
  async acquire(): Promise<() => void> {
    log.engine.debug("concurrency-gate.acquire: entry", {
      inflight: this._inflight,
      queued: this._queue.length,
      circuitOpen: this._circuitOpen,
    });

    if (this._circuitOpen) {
      log.engine.warn("concurrency-gate.acquire: circuit open — fast fail");
      throw new CircuitOpenError();
    }

    if (this._inflight < this.opts.maxConcurrent) {
      this._inflight++;
      log.engine.debug("concurrency-gate.acquire: slot acquired immediately", { inflight: this._inflight });
      return this._makeRelease();
    }

    log.engine.debug("concurrency-gate.acquire: queuing caller", { queued: this._queue.length + 1 });
    return new Promise<() => void>((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this._queue.findIndex((w) => w.timer === timer);
        if (idx !== -1) this._queue.splice(idx, 1);
        log.engine.warn("concurrency-gate.acquire: timeout", {
          queueTimeoutMs: this.opts.queueTimeoutMs,
        });
        reject(new ConcurrencyTimeoutError());
      }, this.opts.queueTimeoutMs);

      this._queue.push({
        resolve: () => {
          clearTimeout(timer);
          this._inflight++;
          log.engine.debug("concurrency-gate.acquire: queued caller unblocked", {
            inflight: this._inflight,
          });
          resolve(this._makeRelease());
        },
        reject,
        timer,
      });
    });
  }

  /** Circuit opened — drain all queued waiters with CircuitOpenError. */
  notifyCircuitOpen(): void {
    this._circuitOpen = true;
    const waiters = this._queue.splice(0);
    log.engine.warn("concurrency-gate.notifyCircuitOpen: draining queue", {
      waiters: waiters.length,
    });
    for (const w of waiters) {
      clearTimeout(w.timer);
      w.reject(new CircuitOpenError());
    }
  }

  /** Circuit closed — new acquire() calls may proceed. */
  notifyCircuitClosed(): void {
    this._circuitOpen = false;
    log.engine.debug("concurrency-gate.notifyCircuitClosed");
  }

  private _makeRelease(): () => void {
    let released = false;
    return () => {
      if (released) return; // idempotent
      released = true;
      this._inflight--;
      log.engine.debug("concurrency-gate.release", { inflight: this._inflight });
      this._dequeue();
    };
  }

  private _dequeue(): void {
    if (this._queue.length > 0 && this._inflight < this.opts.maxConcurrent) {
      const waiter = this._queue.shift()!;
      waiter.resolve();
    }
  }

  get inflight(): number { return this._inflight; }
  get queued(): number { return this._queue.length; }
}
