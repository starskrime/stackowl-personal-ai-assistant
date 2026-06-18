import { describe, it, expect } from "vitest";
import { platform as osPlatform } from "node:os";
import { OpenerImpl } from "../../src/platform/capabilities/opener.js";

describe("OpenerImpl", () => {
  it("open() returns a launched result with a via indicator (dry-run mode)", async () => {
    const opener = new OpenerImpl({ dryRun: true });
    const r = await opener.open("https://example.com");
    expect(typeof r.launched).toBe("boolean");
    expect(typeof r.via).toBe("string");
    expect(r.via.length).toBeGreaterThan(0);
  });

  it("via reflects the platform's expected opener", async () => {
    const opener = new OpenerImpl({ dryRun: true });
    const r = await opener.open("https://example.com");
    if (osPlatform() === "darwin") expect(r.via).toBe("open");
    else if (osPlatform() === "win32") expect(r.via).toBe("start");
    else expect(["xdg-open", "gnome-open", "kde-open"]).toContain(r.via);
  });
});
