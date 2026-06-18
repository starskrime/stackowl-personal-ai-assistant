/**
 * StackOwl — Element 7 T19 — Safari JXA driver
 *
 * Drives the user's *live* Safari via osascript -l JavaScript (JXA). One
 * adapter method per action; the unified live_browser tool dispatches into
 * these. Tests inject a JxaRunner that captures scripts so we can verify
 * the dispatched JXA without spawning processes.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { SafariDriver } from "../../src/tools/live-browser/safari-driver.js";

interface RunnerCall {
  script: string;
}

function makeRunner(returnValue: string) {
  const calls: RunnerCall[] = [];
  return {
    calls,
    runner: async (script: string) => {
      calls.push({ script });
      return returnValue;
    },
  };
}

describe("SafariDriver — JXA wrapper", () => {
  let runner: ReturnType<typeof makeRunner>;
  let driver: SafariDriver;

  beforeEach(() => {
    runner = makeRunner("");
    driver = new SafariDriver(runner.runner);
  });

  it("listTabs parses JSON returned by JXA", async () => {
    const r = makeRunner(
      JSON.stringify([
        { title: "tab one", url: "https://a.example" },
        { title: "tab two", url: "https://b.example" },
      ]),
    );
    const d = new SafariDriver(r.runner);
    const tabs = await d.listTabs();
    expect(tabs).toEqual([
      { title: "tab one", url: "https://a.example" },
      { title: "tab two", url: "https://b.example" },
    ]);
    expect(r.calls[0]?.script).toContain("Application('Safari')");
    expect(r.calls[0]?.script).toContain("JSON.stringify");
  });

  it("listTabs returns [] when JXA emits invalid JSON", async () => {
    const r = makeRunner("garbage");
    const d = new SafariDriver(r.runner);
    expect(await d.listTabs()).toEqual([]);
  });

  it("activeTabUrl trims whitespace from JXA output", async () => {
    const r = makeRunner("  https://example.com\n");
    const d = new SafariDriver(r.runner);
    expect(await d.activeTabUrl()).toBe("https://example.com");
  });

  it("activeTabUrl returns null on empty output", async () => {
    const r = makeRunner("");
    const d = new SafariDriver(r.runner);
    expect(await d.activeTabUrl()).toBeNull();
  });

  it("navigate sets the URL of the front document via JXA", async () => {
    await driver.navigate("https://newpage.example");
    expect(runner.calls).toHaveLength(1);
    expect(runner.calls[0]?.script).toContain("https://newpage.example");
    expect(runner.calls[0]?.script).toContain("Safari");
  });

  it("runJS embeds the script via Safari's `do JavaScript`", async () => {
    await driver.runJS("document.title");
    expect(runner.calls[0]?.script).toContain("doJavaScript");
    expect(runner.calls[0]?.script).toContain("document.title");
  });

  it("click delegates to runJS with a synthetic click on selector", async () => {
    await driver.click("button#submit");
    const script = runner.calls[0]?.script ?? "";
    expect(script).toContain("button#submit");
    expect(script).toContain("click()");
  });

  it("fill delegates to runJS to set value + dispatch input event", async () => {
    await driver.fill("input[name=q]", "hello");
    const script = runner.calls[0]?.script ?? "";
    expect(script).toContain("input[name=q]");
    expect(script).toContain("hello");
    expect(script).toContain("dispatchEvent");
  });

  it("escapes single quotes in URLs to keep JXA syntax intact", async () => {
    await driver.navigate("https://x.example/?q=it's");
    const script = runner.calls[0]?.script ?? "";
    // The URL must survive JXA's single-quoted string syntax.
    expect(script).not.toMatch(/'.*it's.*'/);
  });
});
