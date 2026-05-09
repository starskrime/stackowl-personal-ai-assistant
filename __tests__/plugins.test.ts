import { describe, it, expect, vi, beforeEach } from "vitest";
import { PluginRegistry } from "../src/plugins/registry.js";
import { ServiceRegistry } from "../src/plugins/services.js";
import { PluginSandbox } from "../src/plugins/sandbox.js";
import { HookPipeline } from "../src/plugins/hook-pipeline.js";
import { PluginLifecycleManager } from "../src/plugins/lifecycle.js";
import type {
  PluginManifest,
  PluginInstance,
  PluginState,
} from "../src/plugins/types.js";
import type { ToolRegistry } from "../src/tools/registry.js";
import type { ToolImplementation } from "../src/tools/registry.js";
import type { EventBus } from "../src/events/bus.js";

vi.mock("../src/logger.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/logger.js")>();
  return {
    ...actual,
    log: {
      engine: {
        info: vi.fn(),
        warn: vi.fn(),
        debug: vi.fn(),
        error: vi.fn(),
      },
    },
    Logger: class MockLogger {
      info = vi.fn();
      warn = vi.fn();
      debug = vi.fn();
      error = vi.fn();
    },
  };
});

const mockToolRegistry = (): ToolRegistry =>
  ({
    register: vi.fn(),
    unregister: vi.fn().mockReturnValue(true),
    has: vi.fn().mockReturnValue(false),
    getAllDefinitions: vi.fn().mockReturnValue([]),
    getDefinitions: vi.fn().mockResolvedValue([]),
    getDefinitionsByCategory: vi.fn().mockReturnValue(new Map()),
    getByCategory: vi.fn().mockReturnValue([]),
    listAll: vi.fn().mockReturnValue([]),
    getBySource: vi.fn().mockReturnValue([]),
    execute: vi.fn().mockResolvedValue("{}"),
    setIntentRouter: vi.fn(),
    setTracker: vi.fn(),
    getTracker: vi.fn().mockReturnValue(null),
    getIntentRouter: vi.fn().mockReturnValue(null),
    reindexTools: vi.fn(),
    setPermission: vi.fn(),
    loadPermissions: vi.fn(),
  }) as unknown as ToolRegistry;

const mockEventBus = (): EventBus =>
  ({
    on: vi.fn(),
    off: vi.fn(),
    emit: vi.fn(),
    removeAllListeners: vi.fn(),
    listenerCount: vi.fn().mockReturnValue(0),
  }) as unknown as EventBus;

const createMockTool = (name: string): ToolImplementation => ({
  definition: {
    name,
    description: `Test tool ${name}`,
    parameters: { type: "object", properties: {} },
  },
  execute: vi.fn().mockResolvedValue(`result of ${name}`),
});

const createMockPluginInstance = (
  name: string,
  overrides: Partial<PluginInstance> = {},
): PluginInstance => ({
  manifest: {
    name,
    version: "1.0.0",
    description: `Test plugin ${name}`,
    entryPoint: "index.js",
    provides: {},
    requires: {},
  },
  state: "unloaded" as PluginState,
  init: vi.fn().mockResolvedValue(undefined),
  start: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue(undefined),
  destroy: vi.fn().mockResolvedValue(undefined),
  ...overrides,
});

const createMockSandbox = (pluginName: string): PluginSandbox => {
  const toolRegistry = mockToolRegistry();
  const eventBus = mockEventBus();
  const serviceRegistry = new ServiceRegistry();
  return new PluginSandbox(
    pluginName,
    toolRegistry,
    eventBus,
    serviceRegistry,
    {},
  );
};

const createManifest = (
  name: string,
  deps: {
    plugins?: { name: string; optional?: boolean }[];
    services?: string[];
    env?: string[];
  } = {},
): PluginManifest => ({
  name,
  version: "1.0.0",
  description: `Test plugin ${name}`,
  entryPoint: "index.js",
  provides: { tools: [`${name}-tool`] },
  requires: {
    plugins: deps.plugins,
    services: deps.services,
    env: deps.env,
  },
});

describe("PluginRegistry", () => {
  let registry: PluginRegistry;

  beforeEach(() => {
    registry = new PluginRegistry();
  });

  describe("register", () => {
    it("should register a plugin with manifest, instance, and sandbox", () => {
      const manifest = createManifest("test-plugin");
      const instance = createMockPluginInstance("test-plugin");
      const sandbox = createMockSandbox("test-plugin");

      registry.register(manifest, instance, sandbox, "/path/to/plugin");

      const managed = registry.get("test-plugin");
      expect(managed).toBeDefined();
      expect(managed?.manifest.name).toBe("test-plugin");
      expect(managed?.instance).toBe(instance);
      expect(managed?.sandbox).toBe(sandbox);
      expect(managed?.state).toBe("unloaded");
    });

    it("should replace existing plugin with same name", () => {
      const manifest1 = createManifest("test-plugin");
      const manifest2 = { ...manifest1, version: "2.0.0" };
      const instance1 = createMockPluginInstance("test-plugin");
      const instance2 = createMockPluginInstance("test-plugin");
      const sandbox1 = createMockSandbox("test-plugin");
      const sandbox2 = createMockSandbox("test-plugin");

      registry.register(manifest1, instance1, sandbox1, "/path1");
      registry.register(manifest2, instance2, sandbox2, "/path2");

      const managed = registry.get("test-plugin");
      expect(managed?.manifest.version).toBe("2.0.0");
      expect(managed?.instance).toBe(instance2);
    });
  });

  describe("unregister", () => {
    it("should unregister plugin and call sandbox teardown", async () => {
      const manifest = createManifest("test-plugin");
      const instance = createMockPluginInstance("test-plugin");
      const sandbox = createMockSandbox("test-plugin");
      const teardownSpy = vi.spyOn(sandbox, "teardown");

      registry.register(manifest, instance, sandbox, "/path/to/plugin");
      await registry.unregister("test-plugin");

      expect(teardownSpy).toHaveBeenCalled();
      expect(registry.get("test-plugin")).toBeUndefined();
    });

    it("should do nothing for non-existent plugin", async () => {
      await expect(registry.unregister("non-existent")).resolves.not.toThrow();
    });
  });

  describe("get", () => {
    it("should return undefined for non-existent plugin", () => {
      expect(registry.get("non-existent")).toBeUndefined();
    });
  });

  describe("list", () => {
    it("should return all registered plugins", () => {
      registry.register(
        createManifest("plugin-1"),
        createMockPluginInstance("plugin-1"),
        createMockSandbox("plugin-1"),
        "/p1",
      );
      registry.register(
        createManifest("plugin-2"),
        createMockPluginInstance("plugin-2"),
        createMockSandbox("plugin-2"),
        "/p2",
      );

      const plugins = registry.list();
      expect(plugins).toHaveLength(2);
    });
  });

  describe("setState", () => {
    it("should update plugin state", () => {
      const manifest = createManifest("test-plugin");
      registry.register(
        manifest,
        createMockPluginInstance("test-plugin"),
        createMockSandbox("test-plugin"),
        "/path",
      );

      registry.setState("test-plugin", "ready");

      expect(registry.get("test-plugin")?.state).toBe("ready");
    });

    it("should not throw for non-existent plugin", () => {
      expect(() => registry.setState("non-existent", "ready")).not.toThrow();
    });
  });

  describe("resolveLoadOrder", () => {
    it("should return plugins in dependency order", () => {
      const manifestA = createManifest("plugin-a");
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });
      const manifestC = createManifest("plugin-c", {
        plugins: [{ name: "plugin-b" }],
      });

      registry.register(
        manifestC,
        createMockPluginInstance("plugin-c"),
        createMockSandbox("plugin-c"),
        "/c",
      );
      registry.register(
        manifestA,
        createMockPluginInstance("plugin-a"),
        createMockSandbox("plugin-a"),
        "/a",
      );
      registry.register(
        manifestB,
        createMockPluginInstance("plugin-b"),
        createMockSandbox("plugin-b"),
        "/b",
      );

      const order = registry.resolveLoadOrder();

      expect(order.indexOf("plugin-a")).toBeLessThan(order.indexOf("plugin-b"));
      expect(order.indexOf("plugin-b")).toBeLessThan(order.indexOf("plugin-c"));
    });

    it("should handle optional dependencies", () => {
      const manifestA = createManifest("plugin-a");
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "optional-plugin", optional: true }],
      });

      registry.register(
        manifestA,
        createMockPluginInstance("plugin-a"),
        createMockSandbox("plugin-a"),
        "/a",
      );
      registry.register(
        manifestB,
        createMockPluginInstance("plugin-b"),
        createMockSandbox("plugin-b"),
        "/b",
      );

      const order = registry.resolveLoadOrder();
      expect(order).toContain("plugin-a");
      expect(order).toContain("plugin-b");
    });

    it("should throw on circular dependency", () => {
      const manifestA = createManifest("plugin-a", {
        plugins: [{ name: "plugin-b" }],
      });
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });

      registry.register(
        manifestA,
        createMockPluginInstance("plugin-a"),
        createMockSandbox("plugin-a"),
        "/a",
      );
      registry.register(
        manifestB,
        createMockPluginInstance("plugin-b"),
        createMockSandbox("plugin-b"),
        "/b",
      );

      expect(() => registry.resolveLoadOrder()).toThrow(
        "Circular dependency detected",
      );
    });

    it("should return empty array when no plugins registered", () => {
      expect(registry.resolveLoadOrder()).toEqual([]);
    });
  });

  describe("checkDependencies", () => {
    it("should return satisfied=true when all dependencies met", () => {
      const manifestA = createManifest("plugin-a");
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });

      registry.register(
        manifestA,
        createMockPluginInstance("plugin-a"),
        createMockSandbox("plugin-a"),
        "/a",
      );
      registry.register(
        manifestB,
        createMockPluginInstance("plugin-b"),
        createMockSandbox("plugin-b"),
        "/b",
      );

      const result = registry.checkDependencies("plugin-b");
      expect(result.satisfied).toBe(true);
      expect(result.missing).toHaveLength(0);
    });

    it("should return satisfied=false with missing required plugins", () => {
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });

      registry.register(
        manifestB,
        createMockPluginInstance("plugin-b"),
        createMockSandbox("plugin-b"),
        "/b",
      );

      const result = registry.checkDependencies("plugin-b");
      expect(result.satisfied).toBe(false);
      expect(result.missing).toContain("plugin-a");
    });

    it("should ignore optional missing plugins", () => {
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "optional-plugin", optional: true }],
      });

      registry.register(
        manifestB,
        createMockPluginInstance("plugin-b"),
        createMockSandbox("plugin-b"),
        "/b",
      );

      const result = registry.checkDependencies("plugin-b");
      expect(result.satisfied).toBe(true);
    });

    it("should check required environment variables", () => {
      const manifest = createManifest("test-plugin", { env: ["MY_VAR"] });
      registry.register(
        manifest,
        createMockPluginInstance("test-plugin"),
        createMockSandbox("test-plugin"),
        "/path",
      );

      const result = registry.checkDependencies("test-plugin");
      expect(result.satisfied).toBe(false);
      expect(result.missing).toContain("env:MY_VAR");
    });

    it("should return false for non-existent plugin", () => {
      const result = registry.checkDependencies("non-existent");
      expect(result.satisfied).toBe(false);
      expect(result.missing).toContain("non-existent");
    });
  });
});

describe("ServiceRegistry", () => {
  let registry: ServiceRegistry;

  beforeEach(() => {
    registry = new ServiceRegistry();
  });

  describe("provide", () => {
    it("should register a service implementation", () => {
      const impl = { foo: "bar" };
      registry.provide("my-service", "test-plugin", impl);

      expect(registry.has("my-service")).toBe(true);
      expect(registry.getProvider("my-service")).toBe("test-plugin");
    });

    it("should overwrite existing service with warning", () => {
      const impl1 = { v: 1 };
      const impl2 = { v: 2 };
      registry.provide("my-service", "plugin-a", impl1);
      registry.provide("my-service", "plugin-b", impl2);

      expect(registry.getProvider("my-service")).toBe("plugin-b");
    });
  });

  describe("consume", () => {
    it("should return registered service implementation", () => {
      const impl = { answer: 42 };
      registry.provide("math-service", "math-plugin", impl);

      const consumed = registry.consume<typeof impl>("math-service");
      expect(consumed).toEqual({ answer: 42 });
    });

    it("should return undefined for non-existent service", () => {
      expect(registry.consume("non-existent")).toBeUndefined();
    });
  });

  describe("has", () => {
    it("should return true for registered service", () => {
      registry.provide("test-service", "plugin", { data: true });
      expect(registry.has("test-service")).toBe(true);
    });

    it("should return false for non-existent service", () => {
      expect(registry.has("non-existent")).toBe(false);
    });
  });

  describe("getProvider", () => {
    it("should return provider plugin name", () => {
      registry.provide("my-service", "provider-plugin", {});
      expect(registry.getProvider("my-service")).toBe("provider-plugin");
    });

    it("should return undefined for non-existent service", () => {
      expect(registry.getProvider("non-existent")).toBeUndefined();
    });
  });

  describe("removeByProvider", () => {
    it("should remove all services provided by a plugin", () => {
      registry.provide("service-a", "plugin-x", { a: 1 });
      registry.provide("service-b", "plugin-y", { b: 2 });
      registry.provide("service-c", "plugin-x", { c: 3 });

      const removed = registry.removeByProvider("plugin-x");

      expect(removed).toBe(2);
      expect(registry.has("service-a")).toBe(false);
      expect(registry.has("service-c")).toBe(false);
      expect(registry.has("service-b")).toBe(true);
    });

    it("should return 0 when no services from provider", () => {
      registry.provide("service-a", "plugin-a", { a: 1 });
      expect(registry.removeByProvider("plugin-b")).toBe(0);
    });
  });

  describe("listAll", () => {
    it("should return all registered services", () => {
      registry.provide("service-a", "plugin-a", { a: 1 });
      registry.provide("service-b", "plugin-b", { b: 2 });

      const list = registry.listAll();
      expect(list).toContainEqual({ name: "service-a", provider: "plugin-a" });
      expect(list).toContainEqual({ name: "service-b", provider: "plugin-b" });
    });
  });
});

describe("PluginSandbox", () => {
  let toolRegistry: ToolRegistry;
  let eventBus: EventBus;
  let serviceRegistry: ServiceRegistry;
  let sandbox: PluginSandbox;

  beforeEach(() => {
    toolRegistry = mockToolRegistry();
    eventBus = mockEventBus();
    serviceRegistry = new ServiceRegistry();
    sandbox = new PluginSandbox(
      "test-plugin",
      toolRegistry,
      eventBus,
      serviceRegistry,
      { key: "value" },
    );
  });

  describe("registerTool", () => {
    it("should register tool with namespaced name", () => {
      const tool = createMockTool("my-tool");
      sandbox.registerTool(tool);

      expect(toolRegistry.register).toHaveBeenCalledWith(
        expect.objectContaining({
          definition: expect.objectContaining({
            name: "plugin_test-plugin_my-tool",
          }),
          source: "plugin",
        }),
      );
    });
  });

  describe("unregisterTool", () => {
    it("should unregister namespaced tool", () => {
      sandbox.unregisterTool("my-tool");

      expect(toolRegistry.unregister).toHaveBeenCalledWith(
        "plugin_test-plugin_my-tool",
      );
    });
  });

  describe("on/emit", () => {
    it("should subscribe to events", () => {
      const handler = vi.fn();
      sandbox.on("session:created", handler);

      expect(eventBus.on).toHaveBeenCalledWith("session:created", handler);
    });

    it("should emit events through the bus", () => {
      sandbox.emit("session:created", {
        sessionId: "s1",
        owlName: "Owl",
        channelId: "c1",
      });

      expect(eventBus.emit).toHaveBeenCalledWith(
        "session:created",
        expect.any(Object),
      );
    });
  });

  describe("onMessage", () => {
    it("should register ACP message handler", () => {
      const handler = vi.fn();
      sandbox.onMessage("my-channel", handler);

      const handlers = sandbox.getACPHandlers();
      expect(handlers).toContainEqual({ channel: "my-channel", handler });
    });
  });

  describe("provideService/getService", () => {
    it("should provide a service", () => {
      sandbox.provideService("my-service", { data: 123 });

      expect(serviceRegistry.has("my-service")).toBe(true);
      expect(serviceRegistry.getProvider("my-service")).toBe("test-plugin");
    });

    it("should consume a service", () => {
      const impl = { answer: 42 };
      sandbox.provideService("math-svc", impl);

      const consumed = sandbox.getService<typeof impl>("math-svc");
      expect(consumed).toEqual({ answer: 42 });
    });

    it("should return undefined for non-existent service", () => {
      expect(sandbox.getService("non-existent")).toBeUndefined();
    });
  });

  describe("getConfig", () => {
    it("should get specific config value", () => {
      expect(sandbox.getConfig("key")).toBe("value");
    });

    it("should return undefined for non-existent key", () => {
      expect(sandbox.getConfig("non-existent")).toBeUndefined();
    });
  });

  describe("getAllConfig", () => {
    it("should return all config", () => {
      expect(sandbox.getAllConfig()).toEqual({ key: "value" });
    });
  });

  describe("teardown", () => {
    it("should unregister all tools", () => {
      const tool = createMockTool("tool-a");
      sandbox.registerTool(tool);
      sandbox.teardown();

      expect(toolRegistry.unregister).toHaveBeenCalledWith(
        "plugin_test-plugin_tool-a",
      );
    });

    it("should remove event handlers", () => {
      const handler = vi.fn();
      sandbox.on("session:created", handler);
      sandbox.teardown();

      expect(eventBus.off).toHaveBeenCalledWith("session:created", handler);
    });

    it("should remove services provided by this plugin", () => {
      sandbox.provideService("my-service", { data: 1 });
      sandbox.teardown();

      expect(serviceRegistry.has("my-service")).toBe(false);
    });
  });
});

describe("HookPipeline", () => {
  let pipeline: HookPipeline;

  beforeEach(() => {
    pipeline = new HookPipeline();
  });

  describe("register", () => {
    it("should register a hook handler", () => {
      const handler = vi.fn();
      pipeline.register("beforeEngine", "test-plugin", handler);

      expect(pipeline.has("beforeEngine")).toBe(true);
    });

    it("should sort hooks by priority ascending", () => {
      const handler1 = vi.fn();
      const handler2 = vi.fn();
      const handler3 = vi.fn();

      pipeline.register("testHook", "plugin-c", handler3, 300);
      pipeline.register("testHook", "plugin-a", handler1, 100);
      pipeline.register("testHook", "plugin-b", handler2, 200);

      const list = pipeline.listAll();
      expect(list[0].plugins).toEqual(["plugin-a", "plugin-b", "plugin-c"]);
    });
  });

  describe("removeByPlugin", () => {
    it("should remove all hooks for a plugin", () => {
      pipeline.register("hook-a", "plugin-x", vi.fn());
      pipeline.register("hook-b", "plugin-x", vi.fn());
      pipeline.register("hook-a", "plugin-y", vi.fn());

      pipeline.removeByPlugin("plugin-x");

      const list = pipeline.listAll();
      const hookA = list.find((h) => h.hookName === "hook-a");
      expect(hookA?.plugins).toEqual(["plugin-y"]);
    });

    it("should delete hook entirely when last plugin removed", () => {
      pipeline.register("hook-a", "plugin-x", vi.fn());
      pipeline.removeByPlugin("plugin-x");

      expect(pipeline.has("hook-a")).toBe(false);
    });
  });

  describe("executeBefore", () => {
    it("should return null when no hooks registered", async () => {
      const result = await pipeline.executeBefore<string>("non-existent");
      expect(result).toBeNull();
    });

    it("should return first non-null result", async () => {
      const handler1 = vi.fn().mockResolvedValue(null);
      const handler2 = vi.fn().mockResolvedValue("short-circuit!");
      const handler3 = vi.fn().mockResolvedValue("never-called");

      pipeline.register("beforeEngine", "plugin-a", handler1, 100);
      pipeline.register("beforeEngine", "plugin-b", handler2, 200);
      pipeline.register("beforeEngine", "plugin-c", handler3, 300);

      const result = await pipeline.executeBefore<string>("beforeEngine");

      expect(result).toBe("short-circuit!");
      expect(handler3).not.toHaveBeenCalled();
    });

    it("should continue if handler returns null", async () => {
      const handler1 = vi.fn().mockResolvedValue(null);
      const handler2 = vi.fn().mockResolvedValue("result");
      const handler3 = vi.fn().mockResolvedValue(null);

      pipeline.register("beforeEngine", "plugin-a", handler1, 100);
      pipeline.register("beforeEngine", "plugin-b", handler2, 200);
      pipeline.register("beforeEngine", "plugin-c", handler3, 300);

      const result = await pipeline.executeBefore<string>("beforeEngine");

      expect(result).toBe("result");
    });

    it("should log warning on handler error and continue", async () => {
      const handler1 = vi.fn().mockRejectedValue(new Error("handler error"));
      const handler2 = vi.fn().mockResolvedValue("success");

      pipeline.register("beforeEngine", "plugin-a", handler1, 100);
      pipeline.register("beforeEngine", "plugin-b", handler2, 200);

      const result = await pipeline.executeBefore<string>("beforeEngine");

      expect(result).toBe("success");
    });
  });

  describe("executeAfter", () => {
    it("should return initial value when no hooks registered", async () => {
      const result = await pipeline.executeAfter<string>(
        "non-existent",
        "initial",
      );
      expect(result).toBe("initial");
    });

    it("should chain handlers sequentially", async () => {
      const handler1 = vi
        .fn()
        .mockImplementation((val) => Promise.resolve(`${val}-a`));
      const handler2 = vi
        .fn()
        .mockImplementation((val) => Promise.resolve(`${val}-b`));

      pipeline.register("afterEngine", "plugin-a", handler1, 100);
      pipeline.register("afterEngine", "plugin-b", handler2, 200);

      const result = await pipeline.executeAfter<string>(
        "afterEngine",
        "initial",
      );

      expect(result).toBe("initial-a-b");
      expect(handler1).toHaveBeenCalledWith("initial");
      expect(handler2).toHaveBeenCalledWith("initial-a");
    });

    it("should pass additional args to handlers", async () => {
      const handler = vi.fn().mockResolvedValue("result");
      pipeline.register("afterEngine", "plugin-a", handler, 100);

      await pipeline.executeAfter<string>(
        "afterEngine",
        "initial",
        "arg1",
        "arg2",
      );

      expect(handler).toHaveBeenCalledWith("initial", "arg1", "arg2");
    });

    it("should use previous result if handler returns undefined", async () => {
      const handler1 = vi.fn().mockResolvedValue(undefined);
      const handler2 = vi.fn().mockResolvedValue("final");

      pipeline.register("afterEngine", "plugin-a", handler1, 100);
      pipeline.register("afterEngine", "plugin-b", handler2, 200);

      const result = await pipeline.executeAfter<string>(
        "afterEngine",
        "initial",
      );

      expect(result).toBe("final");
    });

    it("should log warning on handler error and continue", async () => {
      const handler1 = vi.fn().mockRejectedValue(new Error("handler error"));
      const handler2 = vi.fn().mockResolvedValue("success");

      pipeline.register("afterEngine", "plugin-a", handler1, 100);
      pipeline.register("afterEngine", "plugin-b", handler2, 200);

      const result = await pipeline.executeAfter<string>(
        "afterEngine",
        "initial",
      );

      expect(result).toBe("success");
    });
  });

  describe("has", () => {
    it("should return true when hooks registered", () => {
      pipeline.register("testHook", "plugin", vi.fn());
      expect(pipeline.has("testHook")).toBe(true);
    });

    it("should return false when no hooks registered", () => {
      expect(pipeline.has("non-existent")).toBe(false);
    });
  });

  describe("listAll", () => {
    it("should return all hooks with plugin names", () => {
      pipeline.register("hook-a", "plugin-1", vi.fn());
      pipeline.register("hook-b", "plugin-2", vi.fn());
      pipeline.register("hook-a", "plugin-3", vi.fn());

      const list = pipeline.listAll();

      const hookA = list.find((h) => h.hookName === "hook-a");
      expect(hookA?.plugins).toContain("plugin-1");
      expect(hookA?.plugins).toContain("plugin-3");
    });
  });
});

describe("PluginLifecycleManager", () => {
  let registry: PluginRegistry;
  let serviceRegistry: ServiceRegistry;
  let hookPipeline: HookPipeline;
  let toolRegistry: ToolRegistry;
  let eventBus: EventBus;
  let manager: PluginLifecycleManager;

  beforeEach(() => {
    registry = new PluginRegistry();
    serviceRegistry = new ServiceRegistry();
    hookPipeline = new HookPipeline();
    toolRegistry = mockToolRegistry();
    eventBus = mockEventBus();
    manager = new PluginLifecycleManager(
      registry,
      serviceRegistry,
      hookPipeline,
      toolRegistry,
      eventBus,
    );
  });

  describe("startAll", () => {
    it("should initialize and start all plugins in load order", async () => {
      const manifestA = createManifest("plugin-a");
      const instanceA = createMockPluginInstance("plugin-a");
      const sandboxA = createMockSandbox("plugin-a");

      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });
      const instanceB = createMockPluginInstance("plugin-b");
      const sandboxB = createMockSandbox("plugin-b");

      registry.register(manifestA, instanceA, sandboxA, "/a");
      registry.register(manifestB, instanceB, sandboxB, "/b");

      await manager.startAll();

      expect(instanceA.init).toHaveBeenCalledWith(sandboxA);
      expect(instanceB.init).toHaveBeenCalledWith(sandboxB);
      expect(instanceA.start).toHaveBeenCalled();
      expect(instanceB.start).toHaveBeenCalled();
    });

    it("should skip plugins with missing dependencies", async () => {
      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });
      const instanceB = createMockPluginInstance("plugin-b");
      const sandboxB = createMockSandbox("plugin-b");

      registry.register(manifestB, instanceB, sandboxB, "/b");

      await manager.startAll();

      expect(instanceB.init).not.toHaveBeenCalled();
      expect(registry.get("plugin-b")?.state).toBe("error");
    });
  });

  describe("stopAll", () => {
    it("should stop all plugins in reverse order", async () => {
      const manifestA = createManifest("plugin-a");
      const instanceA = createMockPluginInstance("plugin-a");
      const sandboxA = createMockSandbox("plugin-a");

      const manifestB = createManifest("plugin-b", {
        plugins: [{ name: "plugin-a" }],
      });
      const instanceB = createMockPluginInstance("plugin-b");
      const sandboxB = createMockSandbox("plugin-b");

      registry.register(manifestA, instanceA, sandboxA, "/a");
      registry.register(manifestB, instanceB, sandboxB, "/b");

      registry.setState("plugin-a", "ready");
      registry.setState("plugin-b", "ready");

      await manager.stopAll();

      const stopOrder =
        vi.mocked(instanceB.stop).mock.invocationCallOrder[0] <
        vi.mocked(instanceA.stop).mock.invocationCallOrder[0];
      const destroyOrder =
        vi.mocked(instanceB.destroy).mock.invocationCallOrder[0] <
        vi.mocked(instanceA.destroy).mock.invocationCallOrder[0];
      expect(stopOrder).toBe(true);
      expect(destroyOrder).toBe(true);
    });
  });

  describe("reloadPlugin", () => {
    it("should stop, destroy, and attempt reload (fails without file)", async () => {
      const manifest = createManifest("test-plugin");
      const instance = createMockPluginInstance("test-plugin");
      const sandbox = createMockSandbox("test-plugin");

      registry.register(manifest, instance, sandbox, "/path/to/plugin");
      registry.setState("test-plugin", "ready");

      await expect(manager.reloadPlugin("test-plugin")).rejects.toThrow();
      expect(instance.stop).toHaveBeenCalled();
      expect(instance.destroy).toHaveBeenCalled();
    });

    it("should warn and return for non-existent plugin", async () => {
      await manager.reloadPlugin("non-existent");

      expect(registry.get("non-existent")).toBeUndefined();
    });
  });
});
