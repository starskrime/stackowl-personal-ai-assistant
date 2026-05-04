/**
 * StackOwl — Element 7 T22 — Unified live_browser tool
 *
 * Single tool with action-based dispatch that auto-targets whichever browser
 * is frontmost. Safari → JXA driver; Chrome → CDP driver (with bootstrap).
 * Tests inject the frontmost detector and both driver factories so we can
 * verify routing without touching real browsers.
 */
import { describe, it, expect } from "vitest";
import { createLiveBrowserTool } from "../../src/tools/live-browser/index.js";
import type {
  LiveBrowserDeps,
  SafariDriverLike,
  ChromeDriverLike,
} from "../../src/tools/live-browser/index.js";
import type { ToolContext } from "../../src/providers/base.js";

class FakeSafariDriver implements SafariDriverLike {
  calls: Array<{ method: string; args: unknown[] }> = [];
  constructor(
    private readonly tabsResp: Array<{ title: string; url: string }> = [
      { title: "S0", url: "https://s0.example" },
    ],
    private readonly urlResp: string | null = "https://s0.example",
    private readonly textResp: string = "safari body",
  ) {}
  async listTabs() {
    this.calls.push({ method: "listTabs", args: [] });
    return this.tabsResp;
  }
  async activeTabUrl() {
    this.calls.push({ method: "activeTabUrl", args: [] });
    return this.urlResp;
  }
  async activeTabText() {
    this.calls.push({ method: "activeTabText", args: [] });
    return this.textResp;
  }
  async navigate(url: string) {
    this.calls.push({ method: "navigate", args: [url] });
  }
  async click(selector: string) {
    this.calls.push({ method: "click", args: [selector] });
  }
  async fill(selector: string, value: string) {
    this.calls.push({ method: "fill", args: [selector, value] });
  }
  async scroll(deltaPx: number) {
    this.calls.push({ method: "scroll", args: [deltaPx] });
  }
  async back() {
    this.calls.push({ method: "back", args: [] });
  }
  async forward() {
    this.calls.push({ method: "forward", args: [] });
  }
}

class FakeChromeDriver implements ChromeDriverLike {
  calls: Array<{ method: string; args: unknown[] }> = [];
  constructor(
    private readonly tabsResp: Array<{ title: string; url: string }> = [
      { title: "C0", url: "https://c0.example" },
      { title: "C1", url: "https://c1.example" },
    ],
    private readonly urlResp: string | null = "https://c0.example",
    private readonly textResp: string = "chrome body",
  ) {}
  async listTabs() {
    this.calls.push({ method: "listTabs", args: [] });
    return this.tabsResp;
  }
  async activeTabUrl() {
    this.calls.push({ method: "activeTabUrl", args: [] });
    return this.urlResp;
  }
  async activeTabText() {
    this.calls.push({ method: "activeTabText", args: [] });
    return this.textResp;
  }
  async navigate(url: string) {
    this.calls.push({ method: "navigate", args: [url] });
  }
  async click(selector: string) {
    this.calls.push({ method: "click", args: [selector] });
  }
  async fill(selector: string, value: string) {
    this.calls.push({ method: "fill", args: [selector, value] });
  }
  async scroll(deltaPx: number) {
    this.calls.push({ method: "scroll", args: [deltaPx] });
  }
  async back() {
    this.calls.push({ method: "back", args: [] });
  }
  async forward() {
    this.calls.push({ method: "forward", args: [] });
  }
  async newTab(url?: string) {
    this.calls.push({ method: "newTab", args: [url] });
  }
  async closeTab(index: number) {
    this.calls.push({ method: "closeTab", args: [index] });
  }
  async switchTab(index: number) {
    this.calls.push({ method: "switchTab", args: [index] });
  }
}

function emptyCtx(): ToolContext {
  return {} as ToolContext;
}

function makeDeps(overrides: Partial<LiveBrowserDeps> = {}): {
  deps: LiveBrowserDeps;
  safari: FakeSafariDriver;
  chrome: FakeChromeDriver;
  bootstrapCalls: number;
} {
  const safari = new FakeSafariDriver();
  const chrome = new FakeChromeDriver();
  let bootstrapCalls = 0;

  const deps: LiveBrowserDeps = {
    detectFrontmost: async () => "safari",
    safariDriverFactory: () => safari,
    chromeDriverFactory: () => chrome,
    ensureChromeBootstrap: async () => {
      bootstrapCalls++;
      return true;
    },
    ...overrides,
  };
  return {
    deps,
    safari,
    chrome,
    get bootstrapCalls() {
      return bootstrapCalls;
    },
  } as ReturnType<typeof makeDeps>;
}

describe("live_browser tool — frontmost-aware dispatch", () => {
  it("routes tabs action to Safari when Safari is frontmost", async () => {
    const { deps, safari } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "tabs" }, emptyCtx());
    expect(safari.calls[0]?.method).toBe("listTabs");
    expect(out).toContain("https://s0.example");
  });

  it("routes tabs action to Chrome when Chrome is frontmost (and bootstraps)", async () => {
    const { deps, chrome } = makeDeps({ detectFrontmost: async () => "chrome" });
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "tabs" }, emptyCtx());
    expect(chrome.calls[0]?.method).toBe("listTabs");
    expect(out).toContain("https://c0.example");
  });

  it("calls ensureChromeBootstrap exactly once on Chrome path", async () => {
    let calls = 0;
    const { deps } = makeDeps({
      detectFrontmost: async () => "chrome",
      ensureChromeBootstrap: async () => {
        calls++;
        return true;
      },
    });
    const tool = createLiveBrowserTool(deps);
    await tool.execute({ action: "tabs" }, emptyCtx());
    await tool.execute({ action: "active_url" }, emptyCtx());
    expect(calls).toBe(2);
  });

  it("returns structured error when no supported browser is frontmost", async () => {
    const { deps } = makeDeps({ detectFrontmost: async () => null });
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "tabs" }, emptyCtx());
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NO_FRONTMOST_BROWSER");
  });

  it("returns structured error when Chrome bootstrap fails", async () => {
    const { deps } = makeDeps({
      detectFrontmost: async () => "chrome",
      ensureChromeBootstrap: async () => false,
    });
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "tabs" }, emptyCtx());
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("BOOTSTRAP_FAILED");
  });

  it("rejects unknown action with structured error", async () => {
    const { deps } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "warp_drive" }, emptyCtx());
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("UNKNOWN_ACTION");
  });

  it("rejects Safari + tab-lifecycle action (not supported on Safari path)", async () => {
    const { deps } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "switch_tab", index: 1 }, emptyCtx());
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("UNSUPPORTED_ON_SAFARI");
  });

  it("dispatches navigate with url to the frontmost driver", async () => {
    const { deps, safari } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    await tool.execute(
      { action: "navigate", url: "https://example.com" },
      emptyCtx(),
    );
    expect(safari.calls[0]).toEqual({
      method: "navigate",
      args: ["https://example.com"],
    });
  });

  it("dispatches click with selector", async () => {
    const { deps, safari } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    await tool.execute(
      { action: "click", selector: "button#go" },
      emptyCtx(),
    );
    expect(safari.calls[0]).toEqual({
      method: "click",
      args: ["button#go"],
    });
  });

  it("dispatches fill with selector + value", async () => {
    const { deps, safari } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    await tool.execute(
      { action: "fill", selector: "input#q", value: "hello" },
      emptyCtx(),
    );
    expect(safari.calls[0]).toEqual({
      method: "fill",
      args: ["input#q", "hello"],
    });
  });

  it("dispatches scroll with delta_px", async () => {
    const { deps, safari } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    await tool.execute({ action: "scroll", delta_px: 320 }, emptyCtx());
    expect(safari.calls[0]).toEqual({
      method: "scroll",
      args: [320],
    });
  });

  it("dispatches Chrome-only switch_tab/new_tab/close_tab", async () => {
    const { deps, chrome } = makeDeps({ detectFrontmost: async () => "chrome" });
    const tool = createLiveBrowserTool(deps);
    await tool.execute({ action: "switch_tab", index: 1 }, emptyCtx());
    await tool.execute(
      { action: "new_tab", url: "https://new.example" },
      emptyCtx(),
    );
    await tool.execute({ action: "close_tab", index: 0 }, emptyCtx());
    const methods = chrome.calls.map((c) => c.method);
    expect(methods).toEqual(["switchTab", "newTab", "closeTab"]);
  });

  it("returns active_url and active_text from the frontmost driver", async () => {
    const { deps } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    const url = await tool.execute({ action: "active_url" }, emptyCtx());
    const text = await tool.execute({ action: "active_text" }, emptyCtx());
    expect(url).toContain("https://s0.example");
    expect(text).toContain("safari body");
  });

  it("validates required args (e.g. navigate without url)", async () => {
    const { deps } = makeDeps();
    const tool = createLiveBrowserTool(deps);
    const out = await tool.execute({ action: "navigate" }, emptyCtx());
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("INVALID_ARGS");
  });
});
