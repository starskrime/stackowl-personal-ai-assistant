/**
 * StackOwl — Element 7 T18 — Frontmost browser detector
 *
 * Detects which browser is the frontmost application on macOS via
 * osascript. Maps the raw process name to a normalized "safari" | "chrome"
 * tag the live_browser tool dispatches on. Returns null when the frontmost
 * application is not a known browser, when osascript fails, or when called
 * on a non-darwin platform.
 */
import { describe, it, expect } from "vitest";
import { detectFrontmostBrowser } from "../../src/tools/live-browser/frontmost.js";

const fixedRunner = (out: string) => async () => out;
const failingRunner = async () => {
  throw new Error("osascript not available");
};

describe("detectFrontmostBrowser", () => {
  it("returns 'safari' when Safari is frontmost", async () => {
    const result = await detectFrontmostBrowser({
      runner: fixedRunner("Safari"),
      platform: "darwin",
    });
    expect(result).toBe("safari");
  });

  it("returns 'chrome' for Google Chrome / Chromium / Brave / Arc", async () => {
    for (const name of ["Google Chrome", "Chromium", "Brave Browser", "Arc"]) {
      const result = await detectFrontmostBrowser({
        runner: fixedRunner(name),
        platform: "darwin",
      });
      expect(result).toBe("chrome");
    }
  });

  it("returns null for unrelated apps", async () => {
    const result = await detectFrontmostBrowser({
      runner: fixedRunner("Terminal"),
      platform: "darwin",
    });
    expect(result).toBeNull();
  });

  it("returns null when the osascript command throws", async () => {
    const result = await detectFrontmostBrowser({
      runner: failingRunner,
      platform: "darwin",
    });
    expect(result).toBeNull();
  });

  it("returns null on non-darwin platforms without invoking the runner", async () => {
    let called = false;
    const result = await detectFrontmostBrowser({
      runner: async () => {
        called = true;
        return "Safari";
      },
      platform: "linux",
    });
    expect(result).toBeNull();
    expect(called).toBe(false);
  });

  it("trims whitespace and ignores trailing newlines from osascript output", async () => {
    const result = await detectFrontmostBrowser({
      runner: fixedRunner("  Safari\n"),
      platform: "darwin",
    });
    expect(result).toBe("safari");
  });
});
