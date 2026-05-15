import { describe, it, expect } from "vitest";
import {
  ConcurrencyGate,
  ConcurrencyTimeoutError,
  CircuitOpenError,
} from "../../src/ratelimit/concurrency-gate.js";

describe("ConcurrencyGate", () => {
  it("acquires and releases a slot immediately when under limit", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    const release = await gate.acquire();
    expect(gate.inflight).toBe(1);
    release();
    expect(gate.inflight).toBe(0);
  });

  it("blocks a second caller until the first releases when maxConcurrent=1", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 1, queueTimeoutMs: 1000 });
    const release1 = await gate.acquire();
    expect(gate.inflight).toBe(1);

    const p2 = gate.acquire();
    await new Promise((r) => setTimeout(r, 0)); // flush microtasks
    expect(gate.queued).toBe(1);

    release1();
    const release2 = await p2;
    expect(gate.inflight).toBe(1);
    expect(gate.queued).toBe(0);
    release2();
    expect(gate.inflight).toBe(0);
  });

  it("rejects with ConcurrencyTimeoutError after queueTimeoutMs", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 1, queueTimeoutMs: 50 });
    const release1 = await gate.acquire();
    await expect(gate.acquire()).rejects.toBeInstanceOf(ConcurrencyTimeoutError);
    release1();
  });

  it("rejects immediately with CircuitOpenError when circuit is already open", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    gate.notifyCircuitOpen();
    await expect(gate.acquire()).rejects.toBeInstanceOf(CircuitOpenError);
  });

  it("drains queue with CircuitOpenError when circuit opens while callers are waiting", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 1, queueTimeoutMs: 1000 });
    const release1 = await gate.acquire();
    const p2 = gate.acquire();
    await new Promise((r) => setTimeout(r, 0));
    expect(gate.queued).toBe(1);

    gate.notifyCircuitOpen();
    await expect(p2).rejects.toBeInstanceOf(CircuitOpenError);
    expect(gate.queued).toBe(0);
    release1();
  });

  it("allows acquisition after notifyCircuitClosed", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    gate.notifyCircuitOpen();
    gate.notifyCircuitClosed();
    const release = await gate.acquire();
    expect(gate.inflight).toBe(1);
    release();
  });

  it("release is idempotent — double-release does not decrement below zero", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    const release = await gate.acquire();
    release();
    release(); // must not crash or go negative
    expect(gate.inflight).toBe(0);
  });
});
