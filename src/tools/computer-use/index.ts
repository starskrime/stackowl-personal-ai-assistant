/**
 * StackOwl — Computer Use Tool (Cross-Platform)
 *
 * Full desktop automation: mouse, keyboard, screenshots, app control,
 * UI element discovery, multi-step planning, workflow recipes, and
 * screen state diffing.
 *
 * Platforms: macOS · Windows · Linux (X11 / Wayland)
 *
 * Workflow:
 *   1. analyze_screen → see what's on screen as text with [ref:N] coordinates
 *   2. click / type / key → interact with UI elements
 *   3. analyze_screen → verify result
 *
 * Human-like mode (human_like: true):
 *   - Mouse: Bézier-curved paths + Gaussian jitter + Fitts-law timing
 *   - Keyboard: WPM-distributed delays + typo+correction simulation
 *   - Pre-click hover pause, between-action thinking pauses
 *
 * macOS: Persistent JXA worker process (~5ms/action, was ~700ms).
 * Windows: PowerShell + inline C# Win32 SendInput.
 * Linux: xdotool (X11) or ydotool (Wayland).
 */

import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";
import * as mac from "./macos.js";
import { DriverManager } from "./driver/manager.js";
import {
  generateHumanPath,
  humanizeTarget,
  humanMoveDuration,
  stepDelays,
  preClickHover,
  thinkingPause,
} from "./human/motion.js";
import {
  expandTypingSequence,
  preTypePause,
  DEFAULT_TYPING_PROFILE,
} from "./human/typing.js";
import {
  readScreen,
  readScreenRegion,
  waitForElement,
  formatScreenAsText,
  formatScreenMinimal,
} from "./screen-reader.js";
import { diffScreenStates } from "./screen-diff.js";
import { RecipeStore } from "./recipes.js";
import { ActionPlanner } from "./planner.js";
import { BrowserBridge } from "./browser/cdp.js";
import type { ScreenState } from "./screen-reader.js";

export {
  readScreen,
  formatScreenAsText,
  formatScreenMinimal,
} from "./screen-reader.js";
export { readScreenRegion, waitForElement } from "./screen-reader.js";
export { diffScreenStates } from "./screen-diff.js";
export { RecipeStore } from "./recipes.js";
export { ActionPlanner } from "./planner.js";
export { BrowserBridge } from "./browser/cdp.js";
export type { ScreenState, ScreenElement } from "./screen-reader.js";
export type { ScreenDiff } from "./screen-diff.js";
export type { Recipe, RecipeStep } from "./recipes.js";

// ─── Shared state for screen diffing ────────────────────────────────────────

let lastScreenState: ScreenState | null = null;

// ─── Tool Definition ─────────────────────────────────────────────────────────

export const ComputerUseTool: ToolImplementation = {
  definition: {
    name: "computer_use",
    description:
      "Control the computer like a real human — move mouse naturally, click, type, press keys, scroll, " +
      "open apps/URLs, drag, read what's on screen, plan multi-step workflows, and learn from successful sequences. " +
      "Works on macOS, Windows, and Linux. " +
      "BEST FOR: any desktop interaction, filling forms, web browsing (bypasses ALL bot detection), " +
      "automating repetitive tasks. " +
      "WORKFLOW: analyze_screen → read text output with [ref:N] click coordinates → click/type → analyze_screen to verify. " +
      "ADVANCED: Use plan_and_execute for multi-step tasks — it plans, executes with verification, and saves as a reusable recipe. " +
      "Use screen_diff to see what changed. Use wait_for_element to wait for UI to load. " +
      "Set human_like:true for undetectable automation (Bézier curves, realistic typing speed, natural pauses). " +
      "analyze_screen returns a TEXT description of everything visible (buttons, links, text fields, content) " +
      "with coordinates — NO vision model needed, works with any text model.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            "Action to perform. Desktop: " +
            "screenshot, analyze_screen, analyze_region, screen_diff, " +
            "click, double_click, right_click, ax_click, " +
            "move, move_smooth, drag, scroll, " +
            "type, key, hotkey, " +
            "open_app, open_url, front_app, " +
            "find_elements, wait_for_element, cursor_position, screen_size, wait, " +
            "plan_and_execute, list_recipes. " +
            "Browser/CDP (for Chrome/Safari/Firefox tasks): " +
            "browser_connect, browser_launch, browser_click, browser_type, " +
            "browser_navigate, browser_get_dom, browser_eval, browser_screenshot, browser_disconnect. " +
            "ROUTING GUIDE: " +
            "- In a web browser? Use browser_* actions (DOM-based, 10x faster/reliable vs AX tree). " +
            "- Know the button/link label? Use ax_click (no coordinate drift, works despite DPI/scaling). " +
            "- Native desktop app? Use analyze_screen → click/type (AX tree grounding). " +
            "- Multi-step workflow? Use plan_and_execute.",
        },
        x: {
          type: "number",
          description: "X coordinate (for click, move, drag start)",
        },
        y: {
          type: "number",
          description: "Y coordinate (for click, move, drag start)",
        },
        to_x: {
          type: "number",
          description: "Target X coordinate (for drag)",
        },
        to_y: {
          type: "number",
          description: "Target Y coordinate (for drag)",
        },
        text: {
          type: "string",
          description:
            "Text to type (for type action), app name (for open_app), URL (for open_url), " +
            "search text (for find_elements/wait_for_element), " +
            "or task description (for plan_and_execute)",
        },
        key: {
          type: "string",
          description:
            "Key name for key action (enter, tab, escape, up, down, left, right, f1-f12, a-z, 0-9). " +
            "For hotkey action: combo like 'cmd+c', 'cmd+shift+s', 'ctrl+alt+delete'",
        },
        modifiers: {
          type: "string",
          description:
            "Comma-separated modifier keys for key action: 'cmd,shift' or 'ctrl,alt'. Options: cmd, shift, alt, ctrl",
        },
        direction: {
          type: "string",
          description: "Scroll direction: up, down, left, right",
        },
        amount: {
          type: "number",
          description:
            "Scroll amount (default 3), wait duration in ms, or timeout for wait_for_element (default 10000)",
        },
        region: {
          type: "string",
          description:
            'Region as JSON: \'{"x":0,"y":0,"width":500,"height":300}\'. ' +
            "Used by screenshot (capture region) and analyze_region (read region only).",
        },
        app_name: {
          type: "string",
          description:
            "Application name for find_elements, analyze_screen, wait_for_element (scope to that app)",
        },
        role: {
          type: "string",
          description:
            "UI element role filter for find_elements/wait_for_element (e.g., AXButton, AXTextField, AXLink). " +
            "Also used by ax_click to narrow the AX tree search.",
        },
        selector: {
          type: "string",
          description:
            "CSS selector for browser_* actions (e.g., '#submit-btn', 'input[name=q]', 'button.primary'). " +
            "Used by browser_click and browser_type. If omitted, browser_click falls back to text matching.",
        },
        port: {
          type: "number",
          description:
            "CDP port for browser_connect (default 9222). Chrome must be running with --remote-debugging-port=<port>.",
        },
        script: {
          type: "string",
          description:
            "JavaScript expression for browser_eval, evaluated in the page context. " +
            "Return value must be JSON-serializable. Example: 'document.title'",
        },
        human_like: {
          type: "boolean",
          description:
            "If true, type with random delays between characters (more human-like). Default false.",
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;

    try {
      switch (action) {
        // ── Screenshot ──────────────────────────────────────────────
        case "screenshot": {
          const cwd = context.cwd || process.cwd();
          const outDir = resolve(cwd, "screenshots");
          if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });

          const filename = `screen_${Date.now()}.png`;
          const outPath = join(outDir, filename);

          const regionStr = args.region as string | undefined;
          const region = regionStr
            ? (JSON.parse(regionStr) as {
                x: number;
                y: number;
                width: number;
                height: number;
              })
            : undefined;
          const driver = await DriverManager.getInstance().getDriver();
          await driver.screenshot(outPath, region);

          if (!existsSync(outPath)) {
            return "Screenshot failed — file not created. Ensure macOS screencapture is available.";
          }

          // Auto-include text analysis of the screen
          let textAnalysis = "";
          try {
            const state = await readScreen();
            lastScreenState = state;
            textAnalysis =
              "\n\n--- SCREEN CONTENT (text) ---\n" + formatScreenAsText(state);
          } catch {
            // Accessibility might not be available
          }

          const dims = await mac.getScreenSize();
          return (
            `Screenshot saved: ${outPath}\n` +
            `Screen: ${dims.width}x${dims.height} (scale: ${dims.scaleFactor}x)\n` +
            `Use send_file to deliver to user.` +
            textAnalysis
          );
        }

        // ── Analyze Screen (text-only — no image needed) ────────────
        case "analyze_screen": {
          const appName = args.app_name as string | undefined;
          const minimal = args.human_like as boolean;
          const state = await readScreen(appName);
          lastScreenState = state;

          const text = minimal
            ? formatScreenMinimal(state)
            : formatScreenAsText(state);

          return text;
        }

        // ── Analyze Region (focused read) ─────────────────────────
        case "analyze_region": {
          const regionStr = args.region as string;
          if (!regionStr)
            return "Error: analyze_region requires region parameter as JSON.";
          const region = JSON.parse(regionStr) as {
            x: number;
            y: number;
            width: number;
            height: number;
          };
          const appName = args.app_name as string | undefined;
          const state = await readScreenRegion(region, appName);
          const text = formatScreenAsText(state);
          return `Region (${region.x},${region.y} ${region.width}x${region.height}):\n${text}`;
        }

        // ── Screen Diff ───────────────────────────────────────────
        case "screen_diff": {
          const currentState = await readScreen(
            args.app_name as string | undefined,
          );
          if (!lastScreenState) {
            lastScreenState = currentState;
            return "No previous screen state to compare. This is the first read — future screen_diff calls will show changes.";
          }
          const diff = diffScreenStates(lastScreenState, currentState);
          lastScreenState = currentState;
          return diff.hasChanges
            ? `CHANGES DETECTED:\n${diff.summary}`
            : "No visible changes since last screen read.";
        }

        // ── Wait For Element ──────────────────────────────────────
        case "wait_for_element": {
          const searchText = args.text as string;
          const role = args.role as string | undefined;
          const appName = args.app_name as string | undefined;
          const timeout = (args.amount as number) || 10_000;

          if (!searchText && !role) {
            return "Error: wait_for_element requires text and/or role parameter.";
          }

          const found = await waitForElement(
            { text: searchText, role, app: appName },
            timeout,
          );

          if (found) {
            const cx = Math.round(found.position.x + found.size.width / 2);
            const cy = Math.round(found.position.y + found.size.height / 2);
            return (
              `Element found: ${found.role} "${found.label}"` +
              (found.value ? ` = "${found.value}"` : "") +
              ` @ (${cx}, ${cy})\n` +
              `Click coordinates: (${cx}, ${cy})`
            );
          }
          return `Element not found within ${timeout}ms.${searchText ? ` Searched for: "${searchText}"` : ""}${role ? ` Role: ${role}` : ""}`;
        }

        // ── Plan and Execute ──────────────────────────────────────
        case "plan_and_execute": {
          const task = args.text as string;
          if (!task)
            return "Error: plan_and_execute requires text parameter with task description.";

          // Get provider from engine context
          const provider = context.engineContext?.provider;
          if (!provider) {
            return "Error: plan_and_execute requires a model provider (not available in current context).";
          }

          const cwd = context.cwd || process.cwd();
          const recipeStore = new RecipeStore(cwd);
          await recipeStore.init();
          const planner = new ActionPlanner(provider, recipeStore);

          // Read current screen
          const currentScreen = await readScreen();
          lastScreenState = currentScreen;

          // Plan
          const plan = await planner.plan(task, currentScreen);
          if (plan.steps.length === 0) {
            return `Could not plan steps for: "${task}"\nReason: ${plan.reasoning}\nTry breaking the task into individual actions.`;
          }

          // Execute with verification
          const onProgress = context.engineContext?.onProgress;
          const result = await planner.execute(
            plan,
            async (act, actArgs) => {
              // Re-enter this tool's execute for each step
              return await ComputerUseTool.execute(
                { action: act, ...actArgs },
                context,
              );
            },
            onProgress,
          );

          // Save as recipe on success
          if (result.success && result.completedSteps.length >= 2) {
            const frontApp = await mac.getFrontApp();
            planner.saveAsRecipe(task, [frontApp], result.completedSteps, []);
          }

          // Build response
          const lines: string[] = [];
          lines.push(`Task: "${task}"`);
          lines.push(`Plan: ${plan.reasoning}`);
          lines.push(
            `Result: ${result.success ? "SUCCESS" : "FAILED"} (${result.stepsCompleted}/${result.totalSteps} steps)`,
          );
          if (result.error) lines.push(`Error: ${result.error}`);
          if (result.screenChanges)
            lines.push(`Screen: ${result.screenChanges}`);
          if (result.success && result.completedSteps.length >= 2) {
            lines.push(`Recipe saved for future reuse.`);
          }

          return lines.join("\n");
        }

        // ── List Recipes ──────────────────────────────────────────
        case "list_recipes": {
          const cwd = context.cwd || process.cwd();
          const recipeStore = new RecipeStore(cwd);
          await recipeStore.init();
          const recipes = recipeStore.listAll();
          if (recipes.length === 0) {
            return "No saved recipes. Use plan_and_execute to create workflows — successful ones are saved automatically.";
          }
          return recipes
            .map(
              (r) =>
                `- "${r.task}" (${r.steps.length} steps, used ${r.successCount}x, apps: ${r.apps.join(", ")})`,
            )
            .join("\n");
        }

        // ── Mouse Click ─────────────────────────────────────────────
        case "click":
        case "double_click":
        case "right_click": {
          const rawX = args.x as number;
          const rawY = args.y as number;
          if (rawX == null || rawY == null)
            return `Error: ${action} requires x and y coordinates.`;

          const humanLike = args.human_like as boolean;
          const driver = await DriverManager.getInstance().getDriver();

          // Determine click type
          const button = action === "right_click" ? "right" : "left";
          const count = action === "double_click" ? 2 : 1;

          if (humanLike) {
            // Apply Bézier move + target jitter + pre-click hover
            const current = await driver.getCursorPosition();
            const target = humanizeTarget({ x: rawX, y: rawY });
            const path = generateHumanPath(current, target);
            const dur = humanMoveDuration(current, target);
            const delays = stepDelays(dur, path.length);

            for (let i = 0; i < path.length; i++) {
              await driver.mouseMove(path[i].x, path[i].y);
              await new Promise((r) => setTimeout(r, delays[i]));
            }
            // Pre-click hover pause
            await new Promise((r) => setTimeout(r, preClickHover()));
            await driver.mouseClick(target.x, target.y, button, count);
          } else {
            await driver.mouseClick(rawX, rawY, button, count);
          }

          const label =
            action === "double_click"
              ? "Double-clicked"
              : action === "right_click"
                ? "Right-clicked"
                : "Clicked";
          return `${label} at (${rawX}, ${rawY})`;
        }

        // ── Mouse Move ──────────────────────────────────────────────
        case "move": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: move requires x and y coordinates.";
          const driver = await DriverManager.getInstance().getDriver();
          await driver.mouseMove(x, y);
          return `Moved cursor to (${x}, ${y})`;
        }

        case "move_smooth": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: move_smooth requires x and y coordinates.";
          const driver = await DriverManager.getInstance().getDriver();
          const current = await driver.getCursorPosition();
          const target = { x, y };
          const path = generateHumanPath(current, target, {
            curveIntensity: 0.35,
            overshoot: true,
          });
          const dur = humanMoveDuration(current, target);
          const delays = stepDelays(dur, path.length);
          for (let i = 0; i < path.length; i++) {
            await driver.mouseMove(path[i].x, path[i].y);
            await new Promise((r) => setTimeout(r, delays[i]));
          }
          return `Smoothly moved cursor to (${x}, ${y})`;
        }

        // ── Drag ────────────────────────────────────────────────────
        case "drag": {
          const x = args.x as number;
          const y = args.y as number;
          const toX = args.to_x as number;
          const toY = args.to_y as number;
          if (x == null || y == null || toX == null || toY == null) {
            return "Error: drag requires x, y, to_x, and to_y coordinates.";
          }
          const driver = await DriverManager.getInstance().getDriver();
          await driver.mouseDrag(x, y, toX, toY);
          return `Dragged from (${x}, ${y}) to (${toX}, ${toY})`;
        }

        // ── Scroll ──────────────────────────────────────────────────
        case "scroll": {
          const dir = (args.direction as string) || "down";
          const amt = (args.amount as number) || 3;
          if (!["up", "down", "left", "right"].includes(dir)) {
            return `Error: scroll direction must be up, down, left, or right. Got "${dir}".`;
          }
          const driver = await DriverManager.getInstance().getDriver();
          await driver.scroll(dir as "up" | "down" | "left" | "right", amt);
          return `Scrolled ${dir} by ${amt}`;
        }

        // ── Keyboard: Type Text ─────────────────────────────────────
        case "type": {
          const text = args.text as string;
          if (!text) return "Error: type requires text parameter.";
          const humanLike = args.human_like as boolean;
          const driver = await DriverManager.getInstance().getDriver();

          if (humanLike) {
            // Pre-type focus pause
            await new Promise((r) => setTimeout(r, preTypePause()));
            // Expand into per-char keystroke sequence with realistic timing
            const keystrokes = expandTypingSequence(text, DEFAULT_TYPING_PROFILE);
            for (const ks of keystrokes) {
              await new Promise((r) => setTimeout(r, ks.delay));
              await driver.typeChar(ks.char);
            }
          } else {
            await driver.typeText(text);
          }
          return `Typed: "${text.length > 80 ? text.slice(0, 80) + "..." : text}"`;
        }

        // ── Keyboard: Press Key ─────────────────────────────────────
        case "key": {
          const key = args.key as string;
          if (!key)
            return "Error: key action requires key parameter (e.g., 'enter', 'tab', 'a').";
          const modifiersRaw = (args.modifiers as string) || "";
          const modifiers = modifiersRaw
            ? modifiersRaw.split(",").map((m) => m.trim())
            : [];
          const driver = await DriverManager.getInstance().getDriver();
          await driver.pressKey(key, modifiers);
          const modStr = modifiers.length > 0 ? modifiers.join("+") + "+" : "";
          return `Pressed: ${modStr}${key}`;
        }

        // ── Keyboard: Hotkey Combo ──────────────────────────────────
        case "hotkey": {
          const combo = args.key as string;
          if (!combo)
            return "Error: hotkey requires key parameter (e.g., 'cmd+c', 'cmd+shift+s').";
          const parts = combo.toLowerCase().split("+").map((p) => p.trim());
          const key = parts.pop()!;
          const modifiers = parts;
          const driver = await DriverManager.getInstance().getDriver();
          await driver.pressKey(key, modifiers);
          return `Pressed hotkey: ${combo}`;
        }

        // ── App Control ─────────────────────────────────────────────
        case "open_app": {
          const appName = args.text as string;
          if (!appName)
            return "Error: open_app requires text parameter with app name.";
          const driver = await DriverManager.getInstance().getDriver();
          await driver.openApp(appName);
          return `Opened/activated: ${appName}`;
        }

        case "open_url": {
          const url = args.text as string;
          if (!url) return "Error: open_url requires text parameter with URL.";
          const driver = await DriverManager.getInstance().getDriver();
          await driver.openUrl(url);
          return `Opened URL in default browser: ${url}`;
        }

        case "front_app": {
          const driver = await DriverManager.getInstance().getDriver();
          const name = await driver.getFrontApp();
          return `Front application: ${name}`;
        }

        // ── UI Element Discovery ────────────────────────────────────
        case "find_elements": {
          const appName = args.app_name as string;
          if (!appName)
            return "Error: find_elements requires app_name parameter.";
          const searchText = args.text as string | undefined;
          const role = args.role as string | undefined;
          const elements = await mac.findUIElements(appName, searchText, role);

          if (elements.length === 0) {
            return `No UI elements found${searchText ? ` matching "${searchText}"` : ""}${role ? ` with role "${role}"` : ""} in ${appName}.`;
          }

          const formatted = elements
            .map((el, i) => {
              const center = {
                x: Math.round(el.position.x + el.size.width / 2),
                y: Math.round(el.position.y + el.size.height / 2),
              };
              return (
                `[${i + 1}] ${el.role} — "${el.title || el.description}"\n` +
                `    Position: (${el.position.x}, ${el.position.y}) Size: ${el.size.width}x${el.size.height}\n` +
                `    Center (click here): (${center.x}, ${center.y})`
              );
            })
            .join("\n");

          return `Found ${elements.length} element(s) in ${appName}:\n\n${formatted}`;
        }

        // ── Info ────────────────────────────────────────────────────
        case "cursor_position": {
          const driver = await DriverManager.getInstance().getDriver();
          const pos = await driver.getCursorPosition();
          return `Cursor position: (${Math.round(pos.x)}, ${Math.round(pos.y)})`;
        }

        case "screen_size": {
          const driver = await DriverManager.getInstance().getDriver();
          const dims = await driver.getScreenSize();
          return `Screen: ${dims.width}x${dims.height} pixels (scale factor: ${dims.scaleFactor}x)`;
        }

        // ── Wait ────────────────────────────────────────────────────
        case "wait": {
          const ms = (args.amount as number) || 1000;
          await new Promise((r) => setTimeout(r, ms));
          return `Waited ${ms}ms`;
        }

        // ── Think (human-like pause) ─────────────────────────────────
        case "think_pause": {
          const complexity = (args.text as "instant" | "simple" | "reading" | "complex") || "simple";
          const ms = thinkingPause(complexity);
          await new Promise((r) => setTimeout(r, ms));
          return `Paused ${ms}ms (${complexity})`;
        }

        // ── AX Direct Press ──────────────────────────────────────────
        // Activates a UI element by label via the accessibility API.
        // No coordinates needed — immune to DPI/Retina drift, window movement.
        // PREFER this over click when you know the element's visible label.
        case "ax_click": {
          const appName = args.app_name as string | undefined;
          const label = args.text as string | undefined;
          const role = args.role as string | undefined;

          if (!label) return "Error: ax_click requires text parameter (element label to find).";
          if (!appName) return "Error: ax_click requires app_name parameter (e.g., 'Safari', 'Finder').";

          const driver = await DriverManager.getInstance().getDriver();
          if (!driver.axPress) {
            // Driver doesn't support AX press — fall back to coordinate-based click
            // via analyze_screen to locate the element first
            return (
              "ax_click not supported on this platform. " +
              "Use analyze_screen to find coordinates, then click(x, y)."
            );
          }

          await driver.axPress(appName, label, role);
          return `AX-pressed: "${label}" in ${appName}${role ? ` (role: ${role})` : ""}`;
        }

        // ── Browser / CDP Actions ─────────────────────────────────────
        // Use these when the front app is a web browser.
        // They talk directly to the browser DOM via CDP — no AX tree,
        // no screenshots needed. Much faster and more reliable for web.

        case "browser_connect": {
          const port = (args.port as number) || 9222;
          await BrowserBridge.getInstance().connect(port);
          return (
            `Connected to Chrome via CDP on port ${port}.\n` +
            `Use browser_get_dom to see the current page, browser_click/browser_type to interact.`
          );
        }

        case "browser_launch": {
          const url = args.text as string | undefined;
          await BrowserBridge.getInstance().launch(url, false);
          return (
            `Launched Chromium${url ? ` and navigated to: ${url}` : " (blank page)"}.\n` +
            `Use browser_get_dom to see the page, browser_click/browser_type to interact.\n` +
            `Use browser_navigate to go to a URL.`
          );
        }

        case "browser_disconnect": {
          await BrowserBridge.getInstance().disconnect();
          return "Browser CDP connection closed.";
        }

        case "browser_navigate": {
          const url = args.text as string;
          if (!url) return "Error: browser_navigate requires text parameter with URL.";
          const nav = await BrowserBridge.getInstance().navigate(url);
          return `Navigated to: ${nav.url}\nPage title: ${nav.title}`;
        }

        case "browser_get_dom": {
          // Returns structured text snapshot of the current web page.
          // Equivalent to analyze_screen for web content — buttons, links, inputs, headings.
          const text = await BrowserBridge.getInstance().getPageText();
          return text;
        }

        case "browser_click": {
          const selector = args.selector as string | undefined;
          const text = args.text as string | undefined;
          if (!selector && !text) {
            return "Error: browser_click requires selector (CSS) or text (visible text) parameter.";
          }
          await BrowserBridge.getInstance().click(selector, text);
          return `Browser clicked: ${selector ? `selector "${selector}"` : `text "${text}"`}`;
        }

        case "browser_type": {
          const selector = args.selector as string;
          const text = args.text as string;
          if (!selector) return "Error: browser_type requires selector parameter.";
          if (text == null) return "Error: browser_type requires text parameter.";
          await BrowserBridge.getInstance().fill(selector, text);
          return `Browser typed into "${selector}": "${text.length > 60 ? text.slice(0, 60) + "..." : text}"`;
        }

        case "browser_eval": {
          const script = args.script as string;
          if (!script) return "Error: browser_eval requires script parameter.";
          const result = await BrowserBridge.getInstance().evaluate(script);
          return `Result: ${JSON.stringify(result, null, 2)}`;
        }

        case "browser_screenshot": {
          const cwd = context.cwd || process.cwd();
          const outDir = resolve(cwd, "screenshots");
          const outPath = join(outDir, `browser_${Date.now()}.png`);
          await BrowserBridge.getInstance().screenshot(outPath);
          return `Browser screenshot saved: ${outPath}\nUse send_file to deliver to user.`;
        }

        default:
          return (
            `Unknown action: "${action}". Available actions:\n` +
            `  Desktop mouse: click, double_click, right_click, ax_click, move, move_smooth, drag, scroll\n` +
            `  Desktop keyboard: type, key, hotkey\n` +
            `  Desktop apps: open_app, open_url, front_app\n` +
            `  Desktop screen: screenshot, analyze_screen, analyze_region, screen_diff\n` +
            `  Desktop elements: find_elements, wait_for_element, cursor_position, screen_size\n` +
            `  Desktop automation: plan_and_execute, list_recipes\n` +
            `  Browser (CDP): browser_connect, browser_launch, browser_navigate, browser_get_dom,\n` +
            `                  browser_click, browser_type, browser_eval, browser_screenshot, browser_disconnect\n` +
            `  Utility: wait, think_pause`
          );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);

      // macOS: Accessibility permission error
      if (
        msg.includes("not allowed assistive access") ||
        msg.includes("accessibility")
      ) {
        return (
          `PERMISSION ERROR: macOS Accessibility access is required.\n` +
          `Go to: System Settings → Privacy & Security → Accessibility\n` +
          `Add and enable the terminal app (Terminal, iTerm2, or VS Code) running StackOwl.\n` +
          `Original error: ${msg}`
        );
      }

      // Linux: missing xdotool
      if (msg.includes("xdotool") && msg.includes("not found")) {
        return (
          `TOOL NOT FOUND: xdotool is required for Linux automation.\n` +
          `Install: sudo apt install xdotool scrot\n` +
          `Wayland: sudo apt install ydotool\n` +
          `Original error: ${msg}`
        );
      }

      // Windows: PowerShell error
      if (msg.includes("powershell") || msg.includes("SendInput")) {
        return (
          `WINDOWS ERROR: Desktop automation failed.\n` +
          `Ensure PowerShell is available and the terminal has permission to send input.\n` +
          `Original error: ${msg}`
        );
      }

      return `Error (${action}): ${msg}`;
    }
  },
};
