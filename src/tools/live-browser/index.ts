/**
 * StackOwl — Element 7 T22 — Unified live_browser tool
 *
 * One tool, one schema, action-based dispatch. The tool detects which
 * browser is frontmost on the user's screen and routes to the matching
 * driver:
 *   - Safari → SafariDriver (JXA via osascript)
 *   - Chrome → ChromeDriver (CDP via puppeteer + bootstrap on first call)
 *
 * The LLM never has to know which browser is in front — that's the whole
 * point of unifying. Tab-lifecycle actions (switch_tab/new_tab/close_tab)
 * are Chrome-only because Safari's JXA path doesn't expose tab handles
 * cleanly enough to make this safe.
 *
 * Every external dependency (frontmost detection, both drivers, Chrome
 * bootstrap) is injectable so unit tests don't touch real browsers.
 */
import type { ToolContext, ToolDefinition } from "../../providers/base.js";
import type { ToolImplementation } from "../registry.js";

export interface SafariDriverLike {
  listTabs(): Promise<Array<{ title: string; url: string }>>;
  activeTabUrl(): Promise<string | null>;
  activeTabText(): Promise<string>;
  navigate(url: string): Promise<void>;
  click(selector: string): Promise<void>;
  fill(selector: string, value: string): Promise<void>;
  scroll(deltaPx: number): Promise<void>;
  back(): Promise<void>;
  forward(): Promise<void>;
}

export interface ChromeDriverLike extends SafariDriverLike {
  newTab(url?: string): Promise<void>;
  closeTab(index: number): Promise<void>;
  switchTab(index: number): Promise<void>;
}

export interface LiveBrowserDeps {
  /** Returns "safari" | "chrome" | null. */
  detectFrontmost: () => Promise<"safari" | "chrome" | null>;
  safariDriverFactory: () => SafariDriverLike;
  chromeDriverFactory: () => ChromeDriverLike;
  /** Ensures Chrome is running with --remote-debugging-port=9222. */
  ensureChromeBootstrap: () => Promise<boolean>;
}

const SAFARI_ACTIONS = new Set([
  "tabs",
  "active_url",
  "active_text",
  "navigate",
  "click",
  "fill",
  "scroll",
  "back",
  "forward",
]);

const CHROME_ONLY_ACTIONS = new Set(["switch_tab", "new_tab", "close_tab"]);

const ALL_ACTIONS = new Set<string>([...SAFARI_ACTIONS, ...CHROME_ONLY_ACTIONS]);

interface ErrorEnvelope {
  success: false;
  data: null;
  error: { code: string; message: string; suggestion?: string };
}

interface SuccessEnvelope<T> {
  success: true;
  data: T;
}

function err(code: string, message: string, suggestion?: string): string {
  const env: ErrorEnvelope = {
    success: false,
    data: null,
    error: suggestion ? { code, message, suggestion } : { code, message },
  };
  return JSON.stringify(env);
}

function ok<T>(data: T): string {
  const env: SuccessEnvelope<T> = { success: true, data };
  return JSON.stringify(env);
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

const DEFINITION: ToolDefinition = {
  name: "live_browser",
  description:
    "Control the user's actual frontmost browser (Safari or Chrome). " +
    "Auto-detects which browser is in front and dispatches the action. " +
    "Use this — not browser_launch — when the user asks about their open tabs or current page.",
  parameters: {
    type: "object",
    properties: {
      action: {
        type: "string",
        description:
          "What to do. One of: tabs, active_url, active_text, navigate, click, fill, scroll, back, forward, switch_tab, new_tab, close_tab.",
        enum: [
          "tabs",
          "active_url",
          "active_text",
          "navigate",
          "click",
          "fill",
          "scroll",
          "back",
          "forward",
          "switch_tab",
          "new_tab",
          "close_tab",
        ],
      },
      url: { type: "string", description: "URL for navigate or new_tab." },
      selector: { type: "string", description: "CSS selector for click or fill." },
      value: { type: "string", description: "Value to type for fill." },
      index: { type: "number", description: "Tab index for switch_tab or close_tab." },
      delta_px: { type: "number", description: "Pixels to scroll for scroll." },
    },
    required: ["action"],
  },
  capabilities: ["browser_control", "live_browser"],
  platforms: ["darwin"],
};

export function createLiveBrowserTool(deps: LiveBrowserDeps): ToolImplementation {
  return {
    definition: DEFINITION,
    category: "system",
    source: "builtin",
    async execute(args: Record<string, unknown>, _ctx: ToolContext): Promise<string> {
      const action = asString(args.action);
      if (!action || !ALL_ACTIONS.has(action)) {
        return err(
          "UNKNOWN_ACTION",
          `Unknown action "${String(args.action)}".`,
          `Use one of: ${Array.from(ALL_ACTIONS).join(", ")}`,
        );
      }

      const frontmost = await deps.detectFrontmost();
      if (!frontmost) {
        return err(
          "NO_FRONTMOST_BROWSER",
          "Neither Safari nor Chrome is the frontmost application.",
          "Bring Safari or Chrome to the front, then retry.",
        );
      }

      if (frontmost === "safari" && CHROME_ONLY_ACTIONS.has(action)) {
        return err(
          "UNSUPPORTED_ON_SAFARI",
          `Action "${action}" is only available on Chrome.`,
          "Switch to Chrome (bring it to the front) and retry.",
        );
      }

      let driver: SafariDriverLike | ChromeDriverLike;
      if (frontmost === "chrome") {
        const ready = await deps.ensureChromeBootstrap();
        if (!ready) {
          return err(
            "BOOTSTRAP_FAILED",
            "Chrome could not be bootstrapped with --remote-debugging-port=9222.",
            "Quit Chrome, then retry — or relaunch Chrome manually with the debug port.",
          );
        }
        driver = deps.chromeDriverFactory();
      } else {
        driver = deps.safariDriverFactory();
      }

      try {
        switch (action) {
          case "tabs": {
            return ok(await driver.listTabs());
          }
          case "active_url": {
            return ok({ url: await driver.activeTabUrl() });
          }
          case "active_text": {
            return ok({ text: await driver.activeTabText() });
          }
          case "navigate": {
            const url = asString(args.url);
            if (!url) return err("INVALID_ARGS", "navigate requires a non-empty url.");
            await driver.navigate(url);
            return ok({ navigated: url });
          }
          case "click": {
            const selector = asString(args.selector);
            if (!selector) return err("INVALID_ARGS", "click requires a non-empty selector.");
            await driver.click(selector);
            return ok({ clicked: selector });
          }
          case "fill": {
            const selector = asString(args.selector);
            const value = asString(args.value);
            if (!selector || value === null)
              return err("INVALID_ARGS", "fill requires a non-empty selector and value.");
            await driver.fill(selector, value);
            return ok({ filled: selector });
          }
          case "scroll": {
            const dy = asNumber(args.delta_px);
            if (dy === null) return err("INVALID_ARGS", "scroll requires numeric delta_px.");
            await driver.scroll(dy);
            return ok({ scrolled: dy });
          }
          case "back": {
            await driver.back();
            return ok({ navigated: "back" });
          }
          case "forward": {
            await driver.forward();
            return ok({ navigated: "forward" });
          }
          case "switch_tab": {
            const idx = asNumber(args.index);
            if (idx === null) return err("INVALID_ARGS", "switch_tab requires numeric index.");
            await (driver as ChromeDriverLike).switchTab(idx);
            return ok({ switched_to: idx });
          }
          case "new_tab": {
            const url = asString(args.url) ?? undefined;
            await (driver as ChromeDriverLike).newTab(url);
            return ok({ opened: url ?? "about:blank" });
          }
          case "close_tab": {
            const idx = asNumber(args.index);
            if (idx === null) return err("INVALID_ARGS", "close_tab requires numeric index.");
            await (driver as ChromeDriverLike).closeTab(idx);
            return ok({ closed: idx });
          }
          default:
            return err("UNKNOWN_ACTION", `Unhandled action "${action}".`);
        }
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        return err("DRIVER_ERROR", message);
      }
    },
  };
}
