/**
 * StackOwl — OpenCLAW-Style Browser Tool
 *
 * Provides full browser automation similar to OpenCLAW's browser tool.
 * Uses Puppeteer for Chrome DevTools Protocol control.
 */

import puppeteer, { type Browser, type Page } from "puppeteer";
import type { ToolImplementation, ToolContext } from "../../tools/registry.js";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

interface BrowserProfile {
  id: string;
  browser: Browser;
  page: Page;
  port: number;
}

const DEFAULT_PROFILE = "default";
const PROFILE_BASE_PORT = 18800;

export class BrowserTool implements ToolImplementation {
  private profiles: Map<string, BrowserProfile> = new Map();
  private profilePorts: Map<string, number> = new Map();
  private nextPort: number = PROFILE_BASE_PORT;
  private workspacePath: string;

  constructor(workspacePath: string = "./workspace") {
    this.workspacePath = workspacePath;
    const profilesDir = join(workspacePath, ".browser-profiles");
    if (!existsSync(profilesDir)) {
      mkdirSync(profilesDir, { recursive: true });
    }
  }

  definition = {
    name: "browser",
    description: `Control a headless browser for web automation. Actions: status, start, stop, snapshot, act, navigate, screenshot.

Examples:
- status: Check browser status
- start: Start a new browser session  
- snapshot: Get page content as AI-readable text
- act: Click/type/press on elements (use refs from snapshot)
- navigate: Go to a URL
- screenshot: Take a visual screenshot`,
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          description:
            "Action to perform: status, start, stop, snapshot, act, navigate, screenshot, open, close",
        },
        profile: {
          type: "string",
          description: 'Browser profile name (default: "default")',
        },
        url: {
          type: "string",
          description: "URL for navigate/open actions",
        },
        ref: {
          type: "string",
          description:
            "Element reference from snapshot (e.g., '12' or 'e12') for act",
        },
        act: {
          type: "string",
          description: "Action type: click, type, press, hover, select, fill",
        },
        value: {
          type: "string",
          description: "Value for type/fill/press actions",
        },
        text: {
          type: "string",
          description: "Text content for act",
        },
      },
      required: ["action"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = args["action"] as string;
    const profile = (args["profile"] as string) || DEFAULT_PROFILE;

    try {
      switch (action) {
        case "status":
          return this.handleStatus(profile);
        case "start":
          return await this.handleStart(profile);
        case "stop":
          return await this.handleStop(profile);
        case "snapshot":
          return await this.handleSnapshot(profile);
        case "navigate":
        case "open":
          const url = args["url"] as string;
          if (!url) return "ERROR: URL is required for navigate/open";
          return await this.handleNavigate(profile, url);
        case "screenshot":
          return await this.handleScreenshot(profile, context);
        case "act":
          return await this.handleAct(profile, args);
        case "close":
          return await this.handleStop(profile);
        default:
          return `ERROR: Unknown action "${action}". Use: status, start, stop, snapshot, navigate, screenshot, act`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `ERROR: ${msg}`;
    }
  }

  private async getProfilePort(profile: string): Promise<number> {
    if (!this.profilePorts.has(profile)) {
      this.profilePorts.set(profile, this.nextPort++);
    }
    return this.profilePorts.get(profile)!;
  }

  private async handleStatus(profile: string): Promise<string> {
    const p = this.profiles.get(profile);
    if (p) {
      const url = p.page.url();
      return JSON.stringify({ status: "running", profile, url });
    }
    return JSON.stringify({ status: "stopped", profile });
  }

  private async handleStart(profile: string): Promise<string> {
    if (this.profiles.has(profile)) {
      const p = this.profiles.get(profile)!;
      return JSON.stringify({
        status: "already_running",
        profile,
        url: p.page.url(),
      });
    }

    const port = await this.getProfilePort(profile);
    const userDataDir = join(this.workspacePath, ".browser-profiles", profile);

    const browser = await puppeteer.launch({
      headless: true,
      args: [
        `--remote-debugging-port=${port}`,
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
      ],
      userDataDir,
      defaultViewport: { width: 1280, height: 800 },
    });

    const page = (await browser.pages())[0] || (await browser.newPage());

    this.profiles.set(profile, { id: profile, browser, page, port });

    return JSON.stringify({
      status: "started",
      profile,
      port,
      url: page.url(),
    });
  }

  private async handleStop(profile: string): Promise<string> {
    const p = this.profiles.get(profile);
    if (!p) {
      return JSON.stringify({ status: "stopped", profile });
    }

    await p.browser.close();
    this.profiles.delete(profile);

    return JSON.stringify({ status: "stopped", profile });
  }

  private async handleNavigate(profile: string, url: string): Promise<string> {
    const p = this.profiles.get(profile);
    if (!p) {
      return JSON.stringify({
        error: "Browser not started. Use browser action=start first.",
      });
    }

    await p.page.goto(url, { waitUntil: "networkidle2", timeout: 30000 });

    return JSON.stringify({ status: "navigated", url, profile });
  }

  private async handleSnapshot(profile: string): Promise<string> {
    const p = this.profiles.get(profile);
    if (!p) {
      return JSON.stringify({ error: "Browser not started" });
    }

    // Get accessibility tree for AI-readable content
    const snapshot = await p.page.accessibility.snapshot();

    // Get clickable elements with refs
    const elements = await p.page.evaluate(() => {
      const clickable = Array.from(
        document.querySelectorAll(
          'a, button, input, select, textarea, [role="button"], [onclick], [tabindex]',
        ),
      );
      return clickable
        .map((el, i) => {
          const rect = el.getBoundingClientRect();
          return {
            ref: i + 1,
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || "").slice(0, 100),
            type: (el as HTMLInputElement).type || "text",
            id: el.id,
            classes: el.className.slice(0, 50),
            visible: rect.width > 0 && rect.height > 0,
          };
        })
        .filter((e) => e.visible);
    });

    return JSON.stringify({
      url: p.page.url(),
      accessibilityTree: snapshot,
      clickableElements: elements.slice(0, 50),
    });
  }

  private async handleScreenshot(
    profile: string,
    context: ToolContext,
  ): Promise<string> {
    const p = this.profiles.get(profile);
    if (!p) {
      return JSON.stringify({ error: "Browser not started" });
    }

    const screenshotDir = join(
      context.cwd || this.workspacePath,
      "screenshots",
    );
    if (!existsSync(screenshotDir)) {
      mkdirSync(screenshotDir, { recursive: true });
    }

    const filename = `screenshot_${Date.now()}.png`;
    const filepath = join(screenshotDir, filename);

    await p.page.screenshot({ path: filepath, fullPage: true });

    return JSON.stringify({
      status: "screenshot_captured",
      filepath,
      filename,
      url: `screenshots/${filename}`,
    });
  }

  private async handleAct(
    profile: string,
    args: Record<string, unknown>,
  ): Promise<string> {
    const p = this.profiles.get(profile);
    if (!p) {
      return JSON.stringify({ error: "Browser not started" });
    }

    const act = args["act"] as string;
    const ref = args["ref"] as string;

    if (!ref && !act) {
      return JSON.stringify({ error: "ref or act is required" });
    }

    // Parse ref - could be "12" or "e12"
    const refNum = parseInt(ref?.replace("e", "") || "0", 10);

    // Get element from page
    const result = await p.page.evaluate(async (refId: number) => {
      const elements = Array.from(
        document.querySelectorAll(
          'a, button, input, select, textarea, [role="button"], [onclick]',
        ),
      );
      const el = elements[refId - 1];
      if (!el) return { error: "Element not found" };

      const tag = el.tagName.toLowerCase();

      // Click
      if (el instanceof HTMLElement) {
        el.click();
      }

      return { success: true, tag, ref: refId };
    }, refNum);

    return JSON.stringify(result);
  }

  async cleanup(): Promise<void> {
    for (const [profile] of this.profiles) {
      await this.handleStop(profile);
    }
  }
}
