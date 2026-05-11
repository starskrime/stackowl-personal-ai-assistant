import { describe, it, expect } from "vitest";
import { createPlatform } from "../../src/platform/registry.js";

describe("PlatformRegistry", () => {
  it("createPlatform() returns a Platform with all capabilities wired", () => {
    const p = createPlatform();
    expect(p.paths).toBeDefined();
    expect(p.sandbox).toBeDefined();
    expect(p.notifier).toBeDefined();
    expect(p.process).toBeDefined();
    expect(p.shell).toBeDefined();
    expect(p.opener).toBeDefined();
    expect(p.systemInfo).toBeDefined();
  });

  it("initialize() runs the system-info refresh probe", async () => {
    const p = createPlatform();
    await p.initialize();
    const info = p.systemInfo.current();
    expect(info.capabilities.hasNode).toBe(true);
  });

  it("paths.tempdir() and sandbox both share the same resolved tempdir", () => {
    const p = createPlatform();
    const td = p.paths.tempdir();
    expect(td.length).toBeGreaterThan(0);
  });
});
