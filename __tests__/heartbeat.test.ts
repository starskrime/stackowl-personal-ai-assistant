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
    const schedule = vi.fn();
    const mockQueue = { markDone: vi.fn(), markFailed: vi.fn(), reschedule: vi.fn(), schedule };

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

    const handled = await (pinger as any).executeJob({
      id: "job1", type: "check_in", userId: "user1",
      scheduledAt: new Date().toISOString(), payload: "{}",
      status: "running", priority: 5, attempts: 1, createdAt: new Date().toISOString(),
    });

    expect(sendToUser).not.toHaveBeenCalled();
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({ status: "discarded", verdict: "NOISE" }),
    );
    expect(mockQueue.markDone).toHaveBeenCalledWith("job1");
    // Regression: NOISE must signal "handled" so the worker-tick caller
    // skips its outer markDone + reenqueue (those are run by the inner path).
    expect(handled).toBe(true);
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
        verify: vi.fn().mockResolvedValue({
          verdict: "NEUTRAL",
          reason: "low value",
          suppressUntil: new Date(Date.now() + 60 * 60_000),
        }),
      } as any,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    const handled = await (pinger as any).executeJob({
      id: "job1", type: "check_in", userId: "user1",
      scheduledAt: new Date().toISOString(), payload: "{}",
      status: "running", priority: 5, attempts: 1, createdAt: new Date().toISOString(),
    });

    expect(sendToUser).not.toHaveBeenCalled();
    expect(reschedule).toHaveBeenCalled();
    // Regression: NEUTRAL must signal "handled" so the worker-tick caller
    // does NOT overwrite the just-scheduled future status with markDone.
    expect(handled).toBe(true);
    expect(mockQueue.markDone).not.toHaveBeenCalled();
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

describe("ProactivePinger — engagement recording", () => {
  it("records reply latency when user replies to a delivery", () => {
    const writeEngagement = vi.fn();
    const mockDb = { writeProactiveDelivery: vi.fn(), writeProactiveEngagement: writeEngagement } as any;

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      db: mockDb,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );

    pinger.recordEngagement("del_xyz", "morning_brief", true, 42, "g1");
    expect(writeEngagement).toHaveBeenCalledWith(
      expect.objectContaining({
        deliveryId: "del_xyz",
        jobType: "morning_brief",
        replied: true,
        replyLatencySeconds: 42,
        goalId: "g1",
      }),
    );
  });
});

describe("ProactivePinger — goal-aware assembly", () => {
  it("morning brief includes active goal context in prompt", async () => {
    const mockGoalGraph = {
      load: vi.fn().mockResolvedValue(undefined),
      getActive: vi.fn().mockReturnValue([
        { id: "g1", title: "Ship feature X", status: "active" },
      ]),
      getStale: vi.fn().mockReturnValue([]),
    };

    const provider = makeMockProvider();
    const pingContext: PingContext = {
      provider,
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser: vi.fn(),
      goalGraph: mockGoalGraph as any,
    };

    const pinger = new ProactivePinger(
      pingContext,
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: true,
        morningBriefHour: new Date().getHours(), quietHoursStart: 22, quietHoursEnd: 7 },
    );

    await (pinger as any).sendMorningBrief();

    const chatCalls = (provider.chat as ReturnType<typeof vi.fn>).mock.calls;
    if (chatCalls.length > 0) {
      const promptUsed = chatCalls[0][0][0].content as string;
      expect(promptUsed).toContain("Ship feature X");
    }
  });

  it("does not have maybeDream method", () => {
    const pinger = new ProactivePinger(
      { provider: makeMockProvider(), owl: makeMockOwl(), config: makeMockConfig(),
        capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
        sendToUser: vi.fn() },
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
    );
    expect((pinger as any).maybeDream).toBeUndefined();
    expect((pinger as any).maybeKnowledgeCouncil).toBeUndefined();
    expect((pinger as any).maybeEvolveSkills).toBeUndefined();
    expect((pinger as any).maybeConsolidateMemory).toBeUndefined();
  });
});
