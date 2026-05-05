import { describe, it, expect, vi } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import {
  GitStatusCollector,
  FileSystemCollector,
} from "../../src/signals/collectors.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

describe("SignalPool boot wiring", () => {
  it("can be constructed with the standard collector set", () => {
    const bus = new GatewayEventBus();
    const pool = new SignalPool({
      bus,
      classifier: {
        classify: async () => ({ keep: false, confidence: 0 }),
      },
      verifier: {
        verify: async () => ({ verdict: "NEUTRAL", reason: "" }),
      } as any,
      goalGraph: {
        getActive: () => [],
        getTopPriority: () => undefined,
      } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    pool.addCollector(new GitStatusCollector("/tmp"));
    pool.addCollector(new FileSystemCollector("/tmp"));
    expect(() => {
      pool.start();
      pool.stop();
    }).not.toThrow();
  });
});
