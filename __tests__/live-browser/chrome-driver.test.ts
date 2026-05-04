/**
 * StackOwl — Element 7 T20 — Chrome CDP driver
 *
 * Wraps the existing BrowserBridge (CDP/Puppeteer) with the same surface
 * SafariDriver exposes so the unified `live_browser` tool can dispatch
 * action-by-name without caring which browser is frontmost. Adds tab
 * lifecycle (listTabs / newTab / closeTab / switchTab) on top of the
 * single-page navigate/click/fill primitives the bridge already had.
 *
 * Tests stub the CDP backend so we can exercise the driver without
 * spawning Chromium.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  ChromeDriver,
  type ChromeBackend,
  type PageLike,
} from "../../src/tools/live-browser/chrome-driver.js";

class FakePage implements PageLike {
  closed = false;
  brought = 0;
  scrolledBy = 0;
  navigations: string[] = [];
  clicks: string[] = [];
  fills: Array<{ selector: string; value: string }> = [];
  jsCalls: string[] = [];
  goneBack = 0;
  goneForward = 0;

  constructor(
    private readonly _title: string,
    private _url: string,
  ) {}

  async title(): Promise<string> {
    return this._title;
  }
  url(): string {
    return this._url;
  }
  async goto(url: string): Promise<void> {
    this.navigations.push(url);
    this._url = url;
  }
  async click(selector: string): Promise<void> {
    this.clicks.push(selector);
  }
  async type(selector: string, value: string): Promise<void> {
    this.fills.push({ selector, value });
  }
  async evaluate<T>(fn: (...args: unknown[]) => T, ...args: unknown[]): Promise<T> {
    this.jsCalls.push(fn.toString());
    if (typeof fn === "function") {
      // Approximate evaluate: just run the function (it operates on a fake DOM).
      return fn(...args);
    }
    return undefined as unknown as T;
  }
  async bringToFront(): Promise<void> {
    this.brought++;
  }
  async close(): Promise<void> {
    this.closed = true;
  }
  async goBack(): Promise<void> {
    this.goneBack++;
  }
  async goForward(): Promise<void> {
    this.goneForward++;
  }
  async innerText(): Promise<string> {
    return `text of ${this._url}`;
  }
}

class FakeBackend implements ChromeBackend {
  pagesList: FakePage[];
  active: FakePage;
  newCalls: Array<string | undefined> = [];

  constructor(pages: FakePage[]) {
    this.pagesList = pages;
    this.active = pages[0]!;
  }
  async pages(): Promise<PageLike[]> {
    return this.pagesList.filter((p) => !p.closed);
  }
  async activePage(): Promise<PageLike> {
    return this.active;
  }
  async newPage(url?: string): Promise<PageLike> {
    this.newCalls.push(url);
    const p = new FakePage("blank", url ?? "about:blank");
    this.pagesList.push(p);
    this.active = p;
    return p;
  }
  async activateTab(index: number): Promise<void> {
    const open = this.pagesList.filter((p) => !p.closed);
    const target = open[index];
    if (!target) return;
    await target.bringToFront();
    this.active = target;
  }
}

describe("ChromeDriver — CDP wrapper", () => {
  let backend: FakeBackend;
  let driver: ChromeDriver;
  let p0: FakePage, p1: FakePage;

  beforeEach(() => {
    p0 = new FakePage("Tab 0", "https://a.example");
    p1 = new FakePage("Tab 1", "https://b.example");
    backend = new FakeBackend([p0, p1]);
    driver = new ChromeDriver(backend);
  });

  it("listTabs returns title + url for every open page", async () => {
    const tabs = await driver.listTabs();
    expect(tabs).toEqual([
      { title: "Tab 0", url: "https://a.example" },
      { title: "Tab 1", url: "https://b.example" },
    ]);
  });

  it("activeTabUrl reads from the active page", async () => {
    expect(await driver.activeTabUrl()).toBe("https://a.example");
  });

  it("navigate goes through page.goto on the active page", async () => {
    await driver.navigate("https://example.com/x");
    expect(p0.navigations).toEqual(["https://example.com/x"]);
  });

  it("click delegates to page.click on active tab", async () => {
    await driver.click("button#submit");
    expect(p0.clicks).toEqual(["button#submit"]);
  });

  it("fill delegates to page.type on active tab", async () => {
    await driver.fill("input[name=q]", "hello");
    expect(p0.fills).toEqual([{ selector: "input[name=q]", value: "hello" }]);
  });

  it("switchTab brings the matching index to front and updates active", async () => {
    await driver.switchTab(1);
    expect(p1.brought).toBe(1);
    expect(backend.active).toBe(p1);
  });

  it("switchTab is a no-op for an out-of-range index", async () => {
    await driver.switchTab(99);
    expect(p0.brought).toBe(0);
    expect(p1.brought).toBe(0);
  });

  it("newTab creates a page via the backend (with optional URL)", async () => {
    await driver.newTab("https://new.example");
    expect(backend.newCalls).toEqual(["https://new.example"]);
    expect((await driver.listTabs()).length).toBe(3);
  });

  it("closeTab closes the page at index", async () => {
    await driver.closeTab(0);
    expect(p0.closed).toBe(true);
    const remaining = await driver.listTabs();
    expect(remaining).toEqual([{ title: "Tab 1", url: "https://b.example" }]);
  });

  it("back / forward dispatch goBack / goForward on active page", async () => {
    await driver.back();
    await driver.forward();
    expect(p0.goneBack).toBe(1);
    expect(p0.goneForward).toBe(1);
  });
});
