import { describe, it, expect, vi } from "vitest";
import { RateLimitedProvider } from "../../src/ratelimit/provider-limiter.js";
import { ConcurrencyGate } from "../../src/ratelimit/concurrency-gate.js";
import { RateLimiter } from "../../src/ratelimit/limiter.js";
import type { ModelProvider, ChatResponse } from "../../src/providers/base.js";

function makeProvider(name = "test"): ModelProvider {
  return {
    name,
    chat: vi.fn().mockResolvedValue({ content: "ok", toolCalls: [], usage: {} } as ChatResponse),
    chatWithTools: vi.fn().mockResolvedValue({ content: "ok", toolCalls: [], usage: {} } as ChatResponse),
    chatStream: vi.fn(async function* () { yield { type: "text", text: "ok" }; }),
    chatWithToolsStream: vi.fn(async function* () { yield { type: "text", text: "ok" }; }),
    embed: vi.fn().mockResolvedValue({ embedding: [] }),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

function makeGate(maxConcurrent = 10) {
  return new ConcurrencyGate({ maxConcurrent, queueTimeoutMs: 1000 });
}

function makeLimiter() {
  return new RateLimiter([{ name: "test-minute", maxRequests: 1000, windowMs: 60_000 }]);
}

describe("RateLimitedProvider", () => {
  it("calls the inner provider on chat()", async () => {
    const inner = makeProvider();
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", makeGate());
    await wrapped.chat([], "model");
    expect(inner.chat).toHaveBeenCalledOnce();
  });

  it("calls the inner provider on chatWithTools()", async () => {
    const inner = makeProvider();
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", makeGate());
    await wrapped.chatWithTools([], [], "model");
    expect(inner.chatWithTools).toHaveBeenCalledOnce();
  });

  it("serializes concurrent calls when maxConcurrent=1", async () => {
    const gate = makeGate(1);
    const limiter = makeLimiter();
    const order: number[] = [];
    let resolveFirst!: () => void;
    const inner = makeProvider();
    (inner.chat as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(
        () => new Promise<ChatResponse>((res) => {
          resolveFirst = () => {
            order.push(1);
            res({ content: "first", toolCalls: [], usage: {} });
          };
        }),
      )
      .mockImplementationOnce(async () => {
        order.push(2);
        return { content: "second", toolCalls: [], usage: {} };
      });

    const wrapped = new RateLimitedProvider(inner, limiter, "test", gate);
    const p1 = wrapped.chat([], "model");
    const p2 = wrapped.chat([], "model");

    await new Promise((r) => setTimeout(r, 0));
    expect(gate.queued).toBe(1); // p2 waiting

    resolveFirst();
    await p1;
    await p2;
    expect(order).toEqual([1, 2]); // p2 ran after p1
  });

  it("releases the gate slot even if inner.chat throws", async () => {
    const gate = makeGate(1);
    const inner = makeProvider();
    (inner.chat as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("boom"));
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", gate);

    await expect(wrapped.chat([], "model")).rejects.toThrow("boom");
    expect(gate.inflight).toBe(0); // gate released despite throw
  });

  it("skips the gate for embed()", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 0, queueTimeoutMs: 10 }); // would block immediately
    const inner = makeProvider();
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", gate);
    await wrapped.embed("text");
    expect(inner.embed).toHaveBeenCalled();
    expect(gate.inflight).toBe(0);
  });
});
