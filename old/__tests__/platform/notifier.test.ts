import { describe, it, expect, vi } from "vitest";
import { NotifierImpl } from "../../src/platform/capabilities/notifier.js";

describe("NotifierImpl fallback chain", () => {
  it("delivers via 'native' when node-notifier succeeds", async () => {
    const nativeStub = {
      notify: vi.fn((_opts, cb) => cb(null, "delivered")),
    };
    const n = new NotifierImpl({ nativeImpl: nativeStub, systemLogPath: null });
    const r = await n.notify({ title: "hi", body: "test" });
    expect(r.via).toBe("native");
    expect(r.delivered).toBe(true);
  });

  it("falls back to 'system' when native throws", async () => {
    const nativeStub = {
      notify: vi.fn((_opts, cb) => cb(new Error("no notifier"))),
    };
    const systemEvents: string[] = [];
    const n = new NotifierImpl({
      nativeImpl: nativeStub,
      systemLogPath: null,
      systemEventEmitter: (msg) => systemEvents.push(msg),
    });
    const r = await n.notify({ title: "hi", body: "test" });
    expect(r.via).toBe("system");
    expect(systemEvents.length).toBe(1);
  });

  it("falls back to 'stderr' when both native and system fail", async () => {
    const nativeStub = {
      notify: vi.fn((_opts, cb) => cb(new Error("no notifier"))),
    };
    const stderrSink: string[] = [];
    const n = new NotifierImpl({
      nativeImpl: nativeStub,
      systemLogPath: null,
      systemEventEmitter: () => { throw new Error("event bus down"); },
      stderrSink: (msg) => stderrSink.push(msg),
    });
    const r = await n.notify({ title: "hi", body: "test" });
    expect(r.via).toBe("stderr");
    expect(stderrSink.length).toBe(1);
  });

  it("capabilities() reports native and system availability", () => {
    const n = new NotifierImpl({ nativeImpl: { notify: () => {} } });
    expect(n.capabilities().native).toBe(true);
  });
});
