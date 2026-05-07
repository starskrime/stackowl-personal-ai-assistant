import { describe, it, expect, vi } from "vitest";
import { ReflexionEngine } from "../../src/evolution/reflexion.js";
import type { ModelProvider } from "../../src/providers/base.js";

describe("ReflexionEngine — wiring contract", () => {
  it("can be constructed with (provider, sessionStore, pelletStore)", () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "{}", model: "test", usage: undefined }),
    } as unknown as ModelProvider;
    const mockSessionStore = { listSessions: vi.fn().mockResolvedValue([]) } as any;
    const mockPelletStore = { save: vi.fn().mockResolvedValue(undefined) } as any;

    const engine = new ReflexionEngine(mockProvider, mockSessionStore, mockPelletStore);
    expect(engine).toBeDefined();
    expect(typeof engine.reflectOnFailure).toBe("function");
    expect(typeof engine.dream).toBe("function");
  });

  it("reflectOnFailure accepts the exact context shape PostProcessor passes", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: '{"analysis":"test","heuristic":"Use absolute paths"}',
        model: "test",
        usage: undefined,
      }),
    } as unknown as ModelProvider;
    const mockSessionStore = { listSessions: vi.fn().mockResolvedValue([]) } as any;
    const mockPelletStore = { save: vi.fn().mockResolvedValue(undefined) } as any;

    const engine = new ReflexionEngine(mockProvider, mockSessionStore, mockPelletStore);

    await expect(
      engine.reflectOnFailure({
        userMessage: "list my files",
        toolsAttempted: "run_shell_command",
        reason: "loop_exhausted",
        sessionId: "sess-001",
      }),
    ).resolves.not.toThrow();

    expect(mockPelletStore.save).toHaveBeenCalledOnce();
    const savedPellet = mockPelletStore.save.mock.calls[0][0];
    expect(savedPellet.tags).toContain("reflexion");
    expect(savedPellet.content).toBe("Use absolute paths");
  });
});
