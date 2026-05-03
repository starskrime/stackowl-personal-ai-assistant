/**
 * ProactivePinger — Task 6 tests
 * Verifier wiring + retry escalation + delivery recording
 */

import { describe, it, expect, vi } from "vitest";
import { ProactivePinger } from "../src/heartbeat/proactive.js";
import type { PingContext } from "../src/heartbeat/proactive.js";

// ─── Shared mock helpers ─────────────────────────────────────────

function makeMockProvider() {
  return {
    chat: vi.fn().mockResolvedValue({ content: '{"verdict":"ADVANCES","reason":"test"}' }),
    stream: vi.fn(),
    name: "mock",
  } as any;
}

function makeMockOwl() {
  return {
    id: "owl-test",
    name: "TestOwl",
    systemPrompt: "You are a test owl.",
    dna: {
      challengeLevel: 0.5,
      verbosity: 0.5,
      learnedPreferences: [],
      expertiseGrowth: {},
    },
  } as any;
}

function makeMockConfig() {
  return {
    workspace: "/tmp/test-workspace",
    providers: {},
    parliament: { maxRounds: 3, maxOwls: 3 },
    heartbeat: { intervalMinutes: 30, quietHours: { start: 22, end: 7 } },
    owlDna: { evolutionBatchSize: 10, decayRatePerWeek: 0.01 },
    smartRouting: { enable: false, availableModels: [] },
  } as any;
}

// ─── Test suites ─────────────────────────────────────────────────

describe("ProactivePinger — retry escalation", () => {
  it("reschedules with backoff when retry_count < 3", async () => {
    const reschedule = vi.fn();
    const markFailed = vi.fn();
    const mockQueue = {
      getDueJobs: vi.fn().mockReturnValue([]),
      markRunning: vi.fn(),
      markDone: vi.fn(),
      markFailed,
      reschedule,
      schedule: vi.fn(),
      getNextScheduled: vi.fn().mockReturnValue(null),
      getRetryCount: vi.fn().mockReturnValue(1),
      incrementRetry: vi.fn(),
    };

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      jobQueue: mockQueue as any,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    await (pinger as any).handleUndeliverable("job1", "no transport available");
    expect(mockQueue.incrementRetry).toHaveBeenCalledWith("job1");
    expect(reschedule).toHaveBeenCalled();
    expect(markFailed).not.toHaveBeenCalled();
  });

  it("marks job failed when retry_count >= 3", async () => {
    const reschedule = vi.fn();
    const markFailed = vi.fn();
    const writeDelivery = vi.fn();
    const mockQueue = {
      reschedule,
      markFailed,
      getRetryCount: vi.fn().mockReturnValue(3),
      incrementRetry: vi.fn(),
    };
    const mockDb = { writeProactiveDelivery: writeDelivery, writeProactiveEngagement: vi.fn() } as any;

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      jobQueue: mockQueue as any,
      db: mockDb,
      userId: "user1",
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    await (pinger as any).handleUndeliverable("job1", "no transport available");
    expect(markFailed).toHaveBeenCalledWith("job1", expect.any(String));
    expect(reschedule).not.toHaveBeenCalled();
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: "job1", status: "failed" }),
    );
  });
});

describe("ProactivePinger — DeliveryVerifier integration", () => {
  it("discards NOISE verdict and writes discarded delivery row", async () => {
    const writeDelivery = vi.fn();
    const sendToUser = vi.fn();
    const mockDb = { writeProactiveDelivery: writeDelivery, writeProactiveEngagement: vi.fn() } as any;
    const mockQueue = { markDone: vi.fn(), markFailed: vi.fn(), reschedule: vi.fn() };

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser,
      jobQueue: mockQueue as any,
      db: mockDb,
      userId: "user1",
      deliveryVerifier: {
        verify: vi.fn().mockResolvedValue({ verdict: "NOISE", reason: "duplicate" }),
      } as any,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    await (pinger as any).executeJob({
      id: "job1", type: "check_in", userId: "user1",
      scheduledAt: new Date().toISOString(), payload: "{}",
      status: "running", priority: 5, attempts: 1, createdAt: new Date().toISOString(),
    });

    expect(sendToUser).not.toHaveBeenCalled();
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({ status: "discarded", verdict: "NOISE" }),
    );
    expect(mockQueue.markDone).toHaveBeenCalledWith("job1");
  });

  it("reschedules NEUTRAL verdict and skips delivery", async () => {
    const sendToUser = vi.fn();
    const reschedule = vi.fn();
    const mockQueue = { markDone: vi.fn(), markFailed: vi.fn(), reschedule };

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser,
      jobQueue: mockQueue as any,
      userId: "user1",
      deliveryVerifier: {
        verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL", reason: "low value", suppressMinutes: 60 }),
      } as any,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    await (pinger as any).executeJob({
      id: "job1", type: "check_in", userId: "user1",
      scheduledAt: new Date().toISOString(), payload: "{}",
      status: "running", priority: 5, attempts: 1, createdAt: new Date().toISOString(),
    });

    expect(sendToUser).not.toHaveBeenCalled();
    expect(reschedule).toHaveBeenCalled();
  });
});

describe("ProactivePinger — delivery recording", () => {
  it("writes proactive_deliveries row after delivery", async () => {
    const writeDelivery = vi.fn();
    const mockDb = { writeProactiveDelivery: writeDelivery, writeProactiveEngagement: vi.fn() } as any;

    const mockGatewayBus = { publish: vi.fn() };
    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser: vi.fn(),
      gatewayEventBus: mockGatewayBus as any,
      userId: "user1",
      db: mockDb,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    await (pinger as any).deliverProactive("Hello world", "check_in", "job_123");
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({
        jobId: "job_123",
        userId: "user1",
        status: "delivered",
        channel: expect.any(String),
      })
    );
  });
});
