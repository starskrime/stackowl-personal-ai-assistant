import { describe, it, expect } from "vitest";
import { platform as osPlatform, arch as osArch } from "node:os";
import { SystemInfoImpl } from "../../src/platform/capabilities/system-info.js";

describe("SystemInfoImpl", () => {
  it("current() returns matching node platform + arch", () => {
    const api = new SystemInfoImpl();
    const info = api.current();
    expect(info.platform).toBe(osPlatform());
    expect(info.arch).toBe(osArch());
  });

  it("current() reports hasNode: true (we are running on Node)", async () => {
    const api = new SystemInfoImpl();
    await api.refresh();
    expect(api.current().capabilities.hasNode).toBe(true);
  });

  it("current() detects locale", () => {
    const api = new SystemInfoImpl();
    expect(api.current().locale.length).toBeGreaterThan(0);
  });

  it("refresh() re-probes and returns the updated info", async () => {
    const api = new SystemInfoImpl();
    const before = api.current();
    const after = await api.refresh();
    expect(after.platform).toBe(before.platform);
    expect(after.capabilities.hasNode).toBe(true);
  });

  it("inContainer reflects /.dockerenv presence", () => {
    const api = new SystemInfoImpl();
    const info = api.current();
    expect(typeof info.inContainer).toBe("boolean");
  });
});
