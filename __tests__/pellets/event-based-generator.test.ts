import { describe, it, expect, beforeEach, vi } from "vitest";
import { EventBasedPelletGenerator } from "../../src/pellets/event-based-generator.js";
import type { PelletStore, Pellet } from "../../src/pellets/store.js";
import type { EventBus } from "../../src/events/bus.js";

const createMockEventBus = (): EventBus => {
  const handlers = new Map<string, Set<Function>>();

  return {
    emit: vi.fn((type: string, payload: any) => {
      const typeHandlers = handlers.get(type);
      if (typeHandlers) {
        for (const handler of typeHandlers) {
          handler(payload);
        }
      }
    }),
    on: vi.fn((type: string, handler: Function) => {
      if (!handlers.has(type)) {
        handlers.set(type, new Set());
      }
      handlers.get(type)!.add(handler);
    }),
    off: vi.fn((type: string, handler: Function) => {
      handlers.get(type)?.delete(handler);
    }),
    once: vi.fn(),
    listenerCount: vi.fn(() => 0),
    removeAllListeners: vi.fn(),
  };
};

const createMockPelletStore = (): PelletStore => {
  const pellets: Map<string, Pellet> = new Map();

  return {
    init: vi.fn().mockResolvedValue(undefined),
    save: vi.fn().mockImplementation(async (pellet: Pellet) => {
      pellets.set(pellet.id, pellet);
      return { verdict: "CREATE" as const, reasoning: "test" };
    }),
    get: vi.fn().mockImplementation(async (id: string) => pellets.get(id) ?? null),
    listAll: vi.fn().mockResolvedValue([...pellets.values()]),
    search: vi.fn().mockResolvedValue([...pellets.values()]),
    count: vi.fn().mockResolvedValue(pellets.size),
    delete: vi.fn().mockImplementation(async (id: string) => pellets.delete(id)),
    buildGraph: vi.fn().mockResolvedValue(undefined),
    getDeduplicator: vi.fn(),
    getKuzuGraph: vi.fn() as any,
    kuzuGraph: {} as any,
  } as unknown as PelletStore;
};

describe("EventBasedPelletGenerator", () => {
  let generator: EventBasedPelletGenerator;
  let mockEventBus: EventBus;
  let mockPelletStore: PelletStore;

  beforeEach(() => {
    mockEventBus = createMockEventBus();
    mockPelletStore = createMockPelletStore();
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        JSON.stringify({
          slug: "test-pellet",
          title: "Test Pellet",
          tags: ["test"],
          owlsInvolved: ["TestOwl"],
          content: "This is test content.",
        }),
      ),
    };

    generator = new EventBasedPelletGenerator(
      mockEventBus,
      mockPelletStore,
      mockRouter,
    );
  });

  describe("subscribe", () => {
    it("should subscribe to relevant events", () => {
      generator.subscribe();
      expect(mockEventBus.on).toHaveBeenCalledWith("session:ended", expect.any(Function));
      expect(mockEventBus.on).toHaveBeenCalledWith("tool:result", expect.any(Function));
      expect(mockEventBus.on).toHaveBeenCalledWith("capability:gap", expect.any(Function));
      expect(mockEventBus.on).toHaveBeenCalledWith("evolution:triggered", expect.any(Function));
      expect(mockEventBus.on).toHaveBeenCalledWith("message:responded", expect.any(Function));
    });
  });

  describe("unsubscribe", () => {
    it("should unsubscribe from events", () => {
      generator.subscribe();
      generator.unsubscribe();
      expect(mockEventBus.off).toHaveBeenCalled();
    });
  });

  describe("generateFromEvent", () => {
    it("should generate a pellet from event data", async () => {
      const data = {
        sourceName: "test:session",
        sourceMaterial: "Test session content",
        tags: ["test-tag"],
        owlsInvolved: ["TestOwl"],
      };

      const pellet = await generator.generateFromEvent(data, "test-type");

      expect(pellet).toBeDefined();
      expect(pellet?.id).toBe("test-pellet");
      expect(mockPelletStore.save).toHaveBeenCalled();
    });

    it("should merge tags from event data", async () => {
      const data = {
        sourceName: "test:session",
        sourceMaterial: "Test session content",
        tags: ["event-tag-1", "event-tag-2"],
        owlsInvolved: ["TestOwl"],
      };

      const pellet = await generator.generateFromEvent(data, "test-type");

      expect(pellet).toBeDefined();
      expect(pellet?.tags).toContain("event-tag-1");
      expect(pellet?.tags).toContain("event-tag-2");
    });
  });
});
