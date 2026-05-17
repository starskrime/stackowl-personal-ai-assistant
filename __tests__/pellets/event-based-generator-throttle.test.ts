import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EventBasedPelletGenerator } from "../../src/pellets/event-based-generator.js";
import type { PelletStore, Pellet } from "../../src/pellets/store.js";
import type { EventBus } from "../../src/events/bus.js";

// ─── Fixed time anchor ────────────────────────────────────────────

const PINNED_TIME = new Date("2025-01-01T00:00:00Z").getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(PINNED_TIME);
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

// ─── Helpers ──────────────────────────────────────────────────────

const CLASSIFICATION_RESPONSE = JSON.stringify({
  isDecision: true,
  isInsight: false,
  isCorrection: false,
});

const PELLET_RESPONSE = JSON.stringify({
  slug: "test-pellet",
  title: "Test Pellet",
  tags: ["decision-capture"],
  owlsInvolved: ["Noctua"],
  content: "## Key Insight\nSomething significant happened.",
});

const TOOL_PAYLOAD = {
  sessionId: "sess1",
  channelId: "cli",
  userId: "u1",
  content: "I have completed the task using the shell tool.",
  owlName: "Noctua",
  toolsUsed: ["shell"],
};

function createMockEventBus(): EventBus {
  const handlers = new Map<string, Set<Function>>();
  return {
    emit: vi.fn((type: string, payload: unknown) => {
      const typeHandlers = handlers.get(type);
      if (typeHandlers) {
        for (const handler of typeHandlers) {
          handler(payload);
        }
      }
    }),
    on: vi.fn((type: string, handler: Function) => {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type)!.add(handler);
    }),
    off: vi.fn((type: string, handler: Function) => {
      handlers.get(type)?.delete(handler);
    }),
    once: vi.fn(),
    listenerCount: vi.fn(() => 0),
    removeAllListeners: vi.fn(),
  };
}

function createMockPelletStore(): PelletStore {
  return {
    init: vi.fn().mockResolvedValue(undefined),
    save: vi.fn().mockImplementation(async (_pellet: Pellet) => ({
      verdict: "CREATE" as const,
      reasoning: "test",
    })),
    get: vi.fn().mockResolvedValue(null),
    listAll: vi.fn().mockResolvedValue([]),
    search: vi.fn().mockResolvedValue([]),
    count: vi.fn().mockResolvedValue(0),
    delete: vi.fn().mockResolvedValue(true),
    buildGraph: vi.fn().mockResolvedValue(undefined),
    getDeduplicator: vi.fn(),
    getKuzuGraph: vi.fn() as any,
    kuzuGraph: {} as any,
  } as unknown as PelletStore;
}

/**
 * The router is called for two different operations:
 * 1. "classification" tier — returns CLASSIFICATION_RESPONSE
 * 2. "synthesis" tier (via PelletGenerator.generate) — returns PELLET_RESPONSE
 */
function createMockRouter() {
  return {
    resolve: vi.fn().mockImplementation(async (tier: string) => {
      if (tier === "classification") return CLASSIFICATION_RESPONSE;
      return PELLET_RESPONSE;
    }),
  };
}

function makeGenerator() {
  const eventBus = createMockEventBus();
  const pelletStore = createMockPelletStore();
  const router = createMockRouter();
  const gen = new EventBasedPelletGenerator(eventBus, pelletStore, router);
  gen.subscribe();
  return { gen, eventBus, pelletStore, router };
}

// ─── Tests ────────────────────────────────────────────────────────

describe("EventBasedPelletGenerator message classification cooldown", () => {
  it("classifies the first tool-using response", async () => {
    const { eventBus, router } = makeGenerator();

    await (eventBus.emit as ReturnType<typeof vi.fn>).mock.calls; // flush
    await (eventBus as any).emit("message:responded", TOOL_PAYLOAD);

    // router.resolve should have been called at least once for classification
    const classificationCalls = (router.resolve as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([tier]: [string]) => tier === "classification",
    );
    expect(classificationCalls.length).toBe(1);
  });

  it("skips classification if called again within the 2-minute cooldown", async () => {
    const { eventBus, router } = makeGenerator();

    // First call — should classify
    await (eventBus as any).emit("message:responded", TOOL_PAYLOAD);

    // Advance time by only 30 seconds (still within cooldown)
    vi.advanceTimersByTime(30_000);

    // Second call — should be skipped
    await (eventBus as any).emit("message:responded", TOOL_PAYLOAD);

    const classificationCalls = (router.resolve as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([tier]: [string]) => tier === "classification",
    );
    expect(classificationCalls.length).toBe(1);
  });

  it("classifies again after cooldown has elapsed", async () => {
    const { eventBus, router } = makeGenerator();

    // First call
    await (eventBus as any).emit("message:responded", TOOL_PAYLOAD);

    // Advance past the 2-minute cooldown
    vi.advanceTimersByTime(3 * 60_000);

    // Second call — cooldown expired, should classify again
    await (eventBus as any).emit("message:responded", TOOL_PAYLOAD);

    const classificationCalls = (router.resolve as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([tier]: [string]) => tier === "classification",
    );
    expect(classificationCalls.length).toBe(2);
  });

  it("skips without LLM call if no tools were used", async () => {
    const { eventBus, router } = makeGenerator();

    await (eventBus as any).emit("message:responded", {
      ...TOOL_PAYLOAD,
      toolsUsed: [],
    });

    const classificationCalls = (router.resolve as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([tier]: [string]) => tier === "classification",
    );
    expect(classificationCalls.length).toBe(0);
  });
});
