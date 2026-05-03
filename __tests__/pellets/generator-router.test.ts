import { describe, it, expect, vi, beforeEach } from "vitest";
import { PelletGenerator } from "../../src/pellets/generator.js";

describe("PelletGenerator — IntelligenceRouter path", () => {
  let mockRouter: any;
  let generator: PelletGenerator;

  beforeEach(() => {
    mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        JSON.stringify({
          slug: "test-pellet-abc123",
          title: "Test Pellet",
          tags: ["testing"],
          owlsInvolved: ["Noctua"],
          content: "## Key Insight\nThis is a test.",
          provenance: ["test", "session-1"],
        })
      ),
    };
    generator = new PelletGenerator(mockRouter);
  });

  it("calls router.resolve with generation prompt", async () => {
    const pellet = await generator.generate("turn 1: user: hello\nassistant: hi", "test-session");
    expect(mockRouter.resolve).toHaveBeenCalledOnce();
    const [tier, prompt] = mockRouter.resolve.mock.calls[0];
    expect(tier).toBe("generation");
    expect(prompt).toContain("turn 1: user: hello");
  });

  it("returns a pellet with successCount=0, failureCount=0, provenance", async () => {
    const pellet = await generator.generate("some content", "src-name");
    expect(pellet!.successCount).toBe(0);
    expect(pellet!.failureCount).toBe(0);
    expect(Array.isArray(pellet!.provenance)).toBe(true);
  });

  it("returns null for empty conversation", async () => {
    const result = await generator.generate("", "empty");
    expect(result).toBeNull();
  });

  it("does not use console.log", async () => {
    const spy = vi.spyOn(console, "log");
    await generator.generate("some content", "src");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
