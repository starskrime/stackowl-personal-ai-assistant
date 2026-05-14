/**
 * TDD suite — /owl CLI v2 command handlers
 *
 * Tests every handler (list, show, delete, pin, unpin, create, from-bmad)
 * and the dispatch path (resolveCommand + dispatcher flow).
 *
 * Mocks:
 *  - logger (suppress debug output in tests)
 *  - dispatchOwlCommand (isolate handler logic from gateway internals)
 *  - UiBridge.prompt() (so interactive wizards resolve immediately)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { SpecializedOwlRegistry } from "../../../../../src/owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../../../../../src/owls/specialized-types.js";
import { UiBridge } from "../../../../../src/cli/v2/events/bridge.js";
import type { CommandContext } from "../../../../../src/cli/v2/commands/registry.js";
import type { UiState } from "../../../../../src/cli/v2/state/store.js";
import {
  handleOwlList,
  handleOwlShow,
  handleOwlDelete,
  handleOwlPin,
  handleOwlUnpin,
  handleOwlCreate,
  handleOwlFromBmad,
} from "../../../../../src/cli/v2/commands/handlers/owl.js";

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock("../../../../../src/logger.js", () => ({
  log: {
    cli: { debug: vi.fn(), warn: vi.fn(), info: vi.fn(), error: vi.fn() },
    engine: { debug: vi.fn(), warn: vi.fn(), info: vi.fn(), error: vi.fn() },
  },
}));

vi.mock("../../../../../src/gateway/commands/owl-command.js", () => ({
  dispatchOwlCommand: vi.fn(),
}));

import { dispatchOwlCommand } from "../../../../../src/gateway/commands/owl-command.js";
const mockDispatch = dispatchOwlCommand as ReturnType<typeof vi.fn>;

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeSpec(overrides: Partial<SpecializedOwlSpec> = {}): SpecializedOwlSpec {
  return {
    name: "Alice",
    type: "specialist",
    role: "Business Analyst",
    emoji: "📊",
    personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
    expertise: ["business analysis"],
    model: { provider: "anthropic", model: "claude-sonnet-4-6" },
    permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
    routingRules: { keywords: [] },
    skills: { allowed: [] },
    additionalPrompt: "",
    source: "bmad",
    bmadSkillName: "bmad-agent-analyst",
    ...overrides,
  };
}

function makeRegistry(owls?: Partial<SpecializedOwlSpec>[]): SpecializedOwlRegistry {
  const r = new SpecializedOwlRegistry();
  const specs = owls ?? [
    { name: "Alice", emoji: "📊", source: "bmad" },
    { name: "Bob",   emoji: "🤖", source: "custom", type: "coordinator" },
  ];
  for (const o of specs) r.registerSpec(makeSpec(o));
  // loadAll() calls this.specs.clear() then reads from filesystem.
  // Stub it to be a no-op so pre-registered specs survive in tests.
  vi.spyOn(r, "loadAll").mockResolvedValue();
  return r;
}

function makeBridge(): UiBridge {
  const bridge = new UiBridge();
  vi.spyOn(bridge, "prompt").mockResolvedValue("wizard-answer");
  vi.spyOn(bridge, "changeOwl").mockImplementation(() => {});
  vi.spyOn(bridge, "closePanel").mockImplementation(() => {});
  return bridge;
}

function makeGateway(registry: SpecializedOwlRegistry | null): Record<string, unknown> {
  return {
    getSpecializedRegistry: () => registry,
    getDb: () => undefined,
    getOwl: () => null,
    getWorkspacePath: () => "/test-workspace",
  };
}

function makeCtx(
  gateway: Record<string, unknown>,
  bridge: UiBridge,
  activeOwlName = "",
): CommandContext {
  return {
    bridge,
    getStore: () => ({ activeOwlName }) as UiState,
    getMemoryRepo: () => { throw new Error("not used"); },
    getMcpManager: () => { throw new Error("not used"); },
    getOwlGateway: () => gateway as ReturnType<CommandContext["getOwlGateway"]>,
  };
}

// ─── handleOwlList ────────────────────────────────────────────────────────────

describe("handleOwlList", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlList(ctx, []);
    expect(result.kind).toBe("system-message");
  });

  it("text contains all registered owl names and emojis", async () => {
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("Alice");
    expect(result.text).toContain("📊");
    expect(result.text).toContain("Bob");
    expect(result.text).toContain("🤖");
  });

  it("marks the active owl with '← active'", async () => {
    ctx = makeCtx(makeGateway(makeRegistry()), bridge, "Alice");
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("← active");
  });

  it("text contains /owl switch hint", async () => {
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("/owl switch");
  });

  it("returns system-message with empty-state text when no owls", async () => {
    ctx = makeCtx(makeGateway(makeRegistry([])), bridge);
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("No owls");
  });
});

// ─── handleOwlShow ────────────────────────────────────────────────────────────

describe("handleOwlShow (/owl show <name>)", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
    mockDispatch.mockResolvedValue("Alice\nBusiness Analyst\nSource: bmad");
  });

  it("returns kind='panel'", async () => {
    const result = await handleOwlShow(ctx, ["alice"]);
    expect(result.kind).toBe("panel");
  });

  it("panel title includes the owl name arg", async () => {
    const result = await handleOwlShow(ctx, ["alice"]);
    if (result.kind !== "panel") throw new Error("expected panel");
    expect(result.payload.title).toContain("alice");
  });

  it("calls dispatchOwlCommand with verb='show' and forwarded args", async () => {
    await handleOwlShow(ctx, ["alice"]);
    expect(mockDispatch).toHaveBeenCalledWith("show", ["alice"], expect.any(Object));
  });

  it("returns kind='error' when registry is null", async () => {
    const ctxNoReg = makeCtx(makeGateway(null), bridge);
    const result = await handleOwlShow(ctxNoReg, ["alice"]);
    expect(result.kind).toBe("error");
  });
});

// ─── handleOwlDelete ──────────────────────────────────────────────────────────

describe("handleOwlDelete (/owl delete <name>)", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
    mockDispatch.mockResolvedValue("Deleted custom owl: Bob");
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlDelete(ctx, ["bob"]);
    expect(result.kind).toBe("system-message");
  });

  it("system-message text comes from dispatchOwlCommand result", async () => {
    const result = await handleOwlDelete(ctx, ["bob"]);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("Deleted custom owl: Bob");
  });

  it("calls dispatchOwlCommand with verb='delete' and forwarded args", async () => {
    await handleOwlDelete(ctx, ["bob"]);
    expect(mockDispatch).toHaveBeenCalledWith("delete", ["bob"], expect.any(Object));
  });

  it("returns kind='error' when registry is null", async () => {
    const ctxNoReg = makeCtx(makeGateway(null), bridge);
    const result = await handleOwlDelete(ctxNoReg, ["bob"]);
    expect(result.kind).toBe("error");
  });
});

// ─── handleOwlPin ─────────────────────────────────────────────────────────────

describe("handleOwlPin (/owl pin <name>)", () => {
  let ctx: CommandContext;

  beforeEach(() => {
    ctx = makeCtx(makeGateway(makeRegistry()), makeBridge());
    mockDispatch.mockResolvedValue("Pinned Alice for this session.");
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlPin(ctx, ["alice"]);
    expect(result.kind).toBe("system-message");
  });

  it("calls dispatchOwlCommand with verb='pin'", async () => {
    await handleOwlPin(ctx, ["alice"]);
    expect(mockDispatch).toHaveBeenCalledWith("pin", ["alice"], expect.any(Object));
  });

  it("returns kind='error' when registry is null", async () => {
    const ctxNoReg = makeCtx(makeGateway(null), makeBridge());
    const result = await handleOwlPin(ctxNoReg, ["alice"]);
    expect(result.kind).toBe("error");
  });
});

// ─── handleOwlUnpin ───────────────────────────────────────────────────────────

describe("handleOwlUnpin (/owl unpin)", () => {
  let ctx: CommandContext;

  beforeEach(() => {
    ctx = makeCtx(makeGateway(makeRegistry()), makeBridge());
    mockDispatch.mockResolvedValue("Unpinned active owl.");
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlUnpin(ctx, []);
    expect(result.kind).toBe("system-message");
  });

  it("calls dispatchOwlCommand with verb='unpin' and empty args", async () => {
    await handleOwlUnpin(ctx, []);
    expect(mockDispatch).toHaveBeenCalledWith("unpin", [], expect.any(Object));
  });

  it("returns kind='error' when registry is null", async () => {
    const ctxNoReg = makeCtx(makeGateway(null), makeBridge());
    const result = await handleOwlUnpin(ctxNoReg, []);
    expect(result.kind).toBe("error");
  });
});

// ─── handleOwlCreate ──────────────────────────────────────────────────────────

describe("handleOwlCreate (/owl create)", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
    mockDispatch.mockResolvedValue("Created owl: MyOwl");
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlCreate(ctx, []);
    expect(result.kind).toBe("system-message");
  });

  it("calls dispatchOwlCommand with verb='create', empty args, and channelAdapter", async () => {
    await handleOwlCreate(ctx, []);
    expect(mockDispatch).toHaveBeenCalledWith(
      "create",
      [],
      expect.objectContaining({ channelAdapter: expect.objectContaining({ ask: expect.any(Function) }) }),
    );
  });

  it("channelAdapter.ask routes through bridge.prompt", async () => {
    mockDispatch.mockClear();
    await handleOwlCreate(ctx, []);
    const lastCall = mockDispatch.mock.lastCall!;
    const owlCtxArg = lastCall[2] as { channelAdapter: { ask: (userId: string, prompt: { text: string }) => Promise<string> } };
    expect(owlCtxArg.channelAdapter).toBeDefined();
    const answer = await owlCtxArg.channelAdapter.ask("local", { text: "What is your owl name?" });
    expect(bridge.prompt).toHaveBeenCalledWith("What is your owl name?", expect.any(Object));
    expect(answer).toBe("wizard-answer");
  });

  it("returns kind='error' when registry is null", async () => {
    const ctxNoReg = makeCtx(makeGateway(null), bridge);
    const result = await handleOwlCreate(ctxNoReg, []);
    expect(result.kind).toBe("error");
  });
});

// ─── handleOwlFromBmad ────────────────────────────────────────────────────────

describe("handleOwlFromBmad (/owl from-bmad [<name>])", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
    mockDispatch.mockResolvedValue("Created owl from BMAD template.");
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlFromBmad(ctx, []);
    expect(result.kind).toBe("system-message");
  });

  it("calls dispatchOwlCommand with verb='from-bmad' and channelAdapter", async () => {
    await handleOwlFromBmad(ctx, ["alice"]);
    expect(mockDispatch).toHaveBeenCalledWith(
      "from-bmad",
      ["alice"],
      expect.objectContaining({ channelAdapter: expect.objectContaining({ ask: expect.any(Function) }) }),
    );
  });

  it("channelAdapter.ask routes through bridge.prompt", async () => {
    mockDispatch.mockClear();
    await handleOwlFromBmad(ctx, []);
    const lastCall = mockDispatch.mock.lastCall!;
    const owlCtxArg = lastCall[2] as { channelAdapter: { ask: (userId: string, prompt: { text: string }) => Promise<string> } };
    expect(owlCtxArg.channelAdapter).toBeDefined();
    const answer = await owlCtxArg.channelAdapter.ask("local", { text: "Pick a template:" });
    expect(bridge.prompt).toHaveBeenCalledWith("Pick a template:", expect.any(Object));
    expect(answer).toBe("wizard-answer");
  });

  it("returns kind='error' when registry is null", async () => {
    const ctxNoReg = makeCtx(makeGateway(null), bridge);
    const result = await handleOwlFromBmad(ctxNoReg, []);
    expect(result.kind).toBe("error");
  });
});
