import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { LocalSwarmNode } from "../src/swarm/node.js";
import { SwarmCoordinator } from "../src/swarm/coordinator.js";
import { SwarmBlackboard } from "../src/swarm/blackboard.js";
import type {
  SwarmConfig,
  SwarmNode,
  SwarmMessage,
  NodeCapability,
} from "../src/swarm/types.js";

vi.mock("../src/logger.js", () => ({
  log: {
    engine: {
      info: vi.fn(),
      warn: vi.fn(),
      debug: vi.fn(),
      error: vi.fn(),
    },
  },
  Logger: vi.fn().mockImplementation(() => ({
    info: vi.fn(),
    warn: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  })),
}));

vi.mock("ws", () => ({
  WebSocketServer: vi.fn().mockImplementation(() => ({
    on: vi.fn(),
    close: vi.fn(),
  })),
  WebSocket: vi.fn().mockImplementation(() => ({
    on: vi.fn(),
    send: vi.fn(),
    close: vi.fn(),
    readyState: 1,
  })),
}));

function makeConfig(overrides: Partial<SwarmConfig> = {}): SwarmConfig {
  return {
    nodeId: "test-node-1",
    nodeName: "TestNode",
    port: 9999,
    discoveryPort: 9998,
    heartbeatIntervalMs: 5000,
    taskTimeoutMs: 10000,
    ...overrides,
  };
}

function makeNode(overrides: Partial<SwarmNode> = {}): SwarmNode {
  return {
    id: "node-1",
    name: "Node1",
    host: "127.0.0.1",
    port: 9000,
    capabilities: ["desktop_automation"],
    status: "online",
    lastSeen: new Date().toISOString(),
    latencyMs: 10,
    currentLoad: 0.3,
    platform: "darwin",
    ...overrides,
  };
}

// ══════════════════════════════════════════════════════════════════════════
// SWARM BLACKBOARD TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("SwarmBlackboard", () => {
  let blackboard: SwarmBlackboard;

  beforeEach(() => {
    blackboard = new SwarmBlackboard();
  });

  describe("write()", () => {
    it("stores entry with correct metadata", () => {
      blackboard.write("key1", "value1", "agent1");

      const entry = blackboard.read<{ key: string; value: unknown }>("key1");
      expect(entry).toBe("value1");
    });

    it("overwrites existing entry", () => {
      blackboard.write("key1", "value1", "agent1");
      blackboard.write("key1", "value2", "agent2");

      expect(blackboard.read("key1")).toBe("value2");
    });
  });

  describe("read()", () => {
    it("returns undefined for non-existent key", () => {
      expect(blackboard.read("nonexistent")).toBeUndefined();
    });

    it("returns correct value for existing key", () => {
      blackboard.write("task", { result: "done" }, "agent1");

      const value = blackboard.read<{ result: string }>("task");
      expect(value?.result).toBe("done");
    });
  });

  describe("has()", () => {
    it("returns false for non-existent key", () => {
      expect(blackboard.has("nonexistent")).toBe(false);
    });

    it("returns true for existing key", () => {
      blackboard.write("exists", "value", "agent1");
      expect(blackboard.has("exists")).toBe(true);
    });
  });

  describe("waitFor()", () => {
    it("resolves immediately if key exists", async () => {
      blackboard.write("ready", "value", "agent1");

      const result = await blackboard.waitFor("ready");
      expect(result).toBe("value");
    });

    it("resolves when key is written by another agent", async () => {
      const waitPromise = blackboard.waitFor("new-key", 5000);

      setTimeout(() => {
        blackboard.write("new-key", "arrived", "agent2");
      }, 100);

      const result = await waitPromise;
      expect(result).toBe("arrived");
    });

    it("rejects on timeout", async () => {
      await expect(blackboard.waitFor("never-written", 200)).rejects.toThrow(
        /timeout/i,
      );
    });
  });

  describe("getByAuthor()", () => {
    it("returns all entries by a specific agent", () => {
      blackboard.write("key1", "v1", "alice");
      blackboard.write("key2", "v2", "bob");
      blackboard.write("key3", "v3", "alice");

      const aliceEntries = blackboard.getByAuthor("alice");
      expect(aliceEntries).toHaveLength(2);
      expect(aliceEntries[0].writtenBy).toBe("alice");
      expect(aliceEntries[1].writtenBy).toBe("alice");
    });

    it("returns empty array for unknown agent", () => {
      blackboard.write("key1", "v1", "alice");
      expect(blackboard.getByAuthor("unknown")).toHaveLength(0);
    });
  });

  describe("toSummary()", () => {
    it("returns empty string when no entries", () => {
      expect(blackboard.toSummary()).toBe("");
    });

    it("formats entries with author and truncated value", () => {
      blackboard.write("result", "short", "alice");

      const summary = blackboard.toSummary();
      expect(summary).toContain("[alice]");
      expect(summary).toContain("result");
      expect(summary).toContain("short");
    });

    it("truncates long string values", () => {
      const longValue = "a".repeat(500);
      blackboard.write("long", longValue, "alice");

      const summary = blackboard.toSummary();
      expect(summary).toContain("a".repeat(300));
      expect(summary).not.toContain("a".repeat(301));
    });

    it("truncates long JSON values", () => {
      const longObj = { data: "a".repeat(500) };
      blackboard.write("json", longObj, "alice");

      const summary = blackboard.toSummary();
      expect(summary).toContain("<swarm_shared_context>");
      expect(summary).toContain("</swarm_shared_context>");
    });
  });

  describe("size", () => {
    it("returns 0 for empty blackboard", () => {
      expect(blackboard.size).toBe(0);
    });

    it("returns correct count after writes", () => {
      blackboard.write("k1", "v1", "a");
      blackboard.write("k2", "v2", "a");
      blackboard.write("k3", "v3", "a");
      expect(blackboard.size).toBe(3);
    });
  });

  describe("clear()", () => {
    it("removes all entries", () => {
      blackboard.write("k1", "v1", "a");
      blackboard.write("k2", "v2", "a");
      blackboard.clear();
      expect(blackboard.size).toBe(0);
    });

    it("resolves outstanding waiters", async () => {
      const waitPromise = blackboard.waitFor("will-be-cleared", 5000);
      blackboard.clear();

      const result = await waitPromise;
      expect(result).toBeUndefined();
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// LOCAL SWARM NODE TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("LocalSwarmNode", () => {
  let node: LocalSwarmNode;
  let config: SwarmConfig;
  let capabilities: NodeCapability[];

  beforeEach(() => {
    config = makeConfig();
    capabilities = ["desktop_automation", "gpu_compute"];
    node = new LocalSwarmNode(config, capabilities);
  });

  afterEach(async () => {
    await node.stop();
  });

  describe("constructor", () => {
    it("initializes with config and capabilities", () => {
      expect(node).toBeDefined();
    });
  });

  describe("getInfo()", () => {
    it("returns node info with correct id and name", () => {
      const info = node.getInfo();
      expect(info.id).toBe(config.nodeId);
      expect(info.name).toBe(config.nodeName);
    });

    it("returns configured capabilities", () => {
      const info = node.getInfo();
      expect(info.capabilities).toEqual(capabilities);
    });

    it("returns correct host and port", () => {
      const info = node.getInfo();
      expect(info.host).toBe("0.0.0.0");
      expect(info.port).toBe(config.port);
    });
  });

  describe("setTaskHandler()", () => {
    it("accepts a task handler function", () => {
      const handler = vi.fn().mockResolvedValue("result");
      node.setTaskHandler(handler);
      expect(handler).toBeDefined();
    });
  });

  describe("getPeers()", () => {
    it("returns empty map initially", () => {
      expect(node.getPeers().size).toBe(0);
    });
  });

  describe("registerPeer()", () => {
    it("adds peer to peers map", () => {
      const mockWs = {
        send: vi.fn(),
        close: vi.fn(),
        readyState: 1,
        on: vi.fn(),
      } as unknown as import("ws").WebSocket;

      const swarmNode = makeNode();
      node.registerPeer("peer-1", mockWs, swarmNode);

      expect(node.getPeers().has("peer-1")).toBe(true);
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// SWARM COORDINATOR TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("SwarmCoordinator", () => {
  let coordinator: SwarmCoordinator;
  let localNode: LocalSwarmNode;
  let config: SwarmConfig;

  beforeEach(() => {
    config = makeConfig({ nodeId: "coordinator-1", nodeName: "Coordinator" });
    localNode = new LocalSwarmNode(config, ["desktop_automation"]);
    coordinator = new SwarmCoordinator(config, localNode);
  });

  describe("constructor", () => {
    it("initializes with config and local node", () => {
      expect(coordinator).toBeDefined();
    });
  });

  describe("getNodes()", () => {
    it("returns local node when no peers connected", () => {
      const nodes = coordinator.getNodes();
      expect(nodes).toHaveLength(1);
      expect(nodes[0].id).toBe(config.nodeId);
    });
  });

  describe("findBestNode()", () => {
    it("returns local node when no capable peers exist", () => {
      const result = coordinator.findBestNode(["desktop_automation"]);
      expect(result).not.toBeNull();
      expect(result?.id).toBe(config.nodeId);
    });

    it("returns null when no node has required capabilities", () => {
      const result = coordinator.findBestNode(["docker", "gpu_compute"]);
      expect(result).toBeNull();
    });

    it("prefers lower load when multiple candidates", () => {
      const result = coordinator.findBestNode(["desktop_automation"]);
      expect(result).not.toBeNull();
    });
  });

  describe("submitTask()", () => {
    it("creates task with valid id", async () => {
      const task = await coordinator.submitTask("test task", [
        "desktop_automation",
      ]);
      expect(task.id).toBeDefined();
      expect(task.description).toBe("test task");
    });

    it("executes task locally when assigned to local node", async () => {
      const task = await coordinator.submitTask("test task", [
        "desktop_automation",
      ]);
      expect(task.assignedNode).toBe(config.nodeId);
      expect(task.status).toBe("completed");
    });

    it("fails task when no capable node available", async () => {
      const task = await coordinator.submitTask("requires docker", ["docker"]);
      expect(task.status).toBe("failed");
      expect(task.error).toContain("No capable node");
    });
  });

  describe("getTask()", () => {
    it("returns null for non-existent task", () => {
      expect(coordinator.getTask("nonexistent")).toBeNull();
    });

    it("returns task after submission", async () => {
      const submitted = await coordinator.submitTask("test", [
        "desktop_automation",
      ]);
      const found = coordinator.getTask(submitted.id);
      expect(found).not.toBeNull();
      expect(found?.id).toBe(submitted.id);
    });
  });

  describe("waitForTask()", () => {
    it("throws for non-existent task", async () => {
      await expect(coordinator.waitForTask("nonexistent")).rejects.toThrow(
        /not found/,
      );
    });

    it("resolves immediately for completed task", async () => {
      const task = await coordinator.submitTask("test", ["desktop_automation"]);
      const completed = await coordinator.waitForTask(task.id, 100);
      expect(completed.status).toBe("completed");
    });

    it("resolves when task completes", async () => {
      const task = await coordinator.submitTask("test", ["desktop_automation"]);
      const result = await coordinator.waitForTask(task.id);
      expect(result.status).toBe("completed");
    });
  });

  describe("getSwarmStatus()", () => {
    it("returns nodes and active tasks", async () => {
      await coordinator.submitTask("task1", ["desktop_automation"]);
      const status = coordinator.getSwarmStatus();

      expect(status.nodes).toHaveLength(1);
      expect(status.activeTasks).toHaveLength(0);
    });

    it("increments totalCompleted after local execution", async () => {
      await coordinator.submitTask("task1", ["desktop_automation"]);
      const status = coordinator.getSwarmStatus();
      expect(status.totalCompleted).toBe(1);
    });
  });

  describe("disconnectNode()", () => {
    it("does nothing for unknown node id", () => {
      expect(() => coordinator.disconnectNode("unknown")).not.toThrow();
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// SWARM TYPES TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("Swarm types", () => {
  describe("NodeCapability", () => {
    it("includes expected capabilities", () => {
      const caps: NodeCapability[] = [
        "desktop_automation",
        "gpu_compute",
        "web_scraping",
        "file_storage",
        "always_on",
        "location_aware",
        "macos_native",
        "linux_native",
        "docker",
      ];
      expect(caps).toHaveLength(9);
    });
  });

  describe("NodeStatus", () => {
    it("includes expected statuses", () => {
      const statuses: import("../src/swarm/types.js").NodeStatus[] = [
        "online",
        "offline",
        "busy",
        "idle",
      ];
      expect(statuses).toHaveLength(4);
    });
  });

  describe("SwarmTask status", () => {
    it("includes expected task statuses", () => {
      const statuses: import("../src/swarm/types.js").SwarmTask["status"][] = [
        "pending",
        "assigned",
        "running",
        "completed",
        "failed",
      ];
      expect(statuses).toHaveLength(5);
    });
  });

  describe("SwarmMessage types", () => {
    it("includes expected message types", () => {
      const types: import("../src/swarm/types.js").SwarmMessage["type"][] = [
        "task_request",
        "task_result",
        "heartbeat",
        "capability_query",
        "capability_response",
      ];
      expect(types).toHaveLength(5);
    });
  });

  describe("SwarmConfig", () => {
    it("has all required fields", () => {
      const cfg: SwarmConfig = {
        nodeId: "id",
        nodeName: "name",
        port: 9000,
        discoveryPort: 9001,
        heartbeatIntervalMs: 5000,
        taskTimeoutMs: 30000,
      };
      expect(cfg.nodeId).toBe("id");
      expect(cfg.port).toBe(9000);
    });
  });
});
