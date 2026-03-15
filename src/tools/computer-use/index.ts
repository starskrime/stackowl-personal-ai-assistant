/**
 * StackOwl — Computer Use Tool
 *
 * Full desktop automation: mouse, keyboard, screenshots, app control,
 * and UI element discovery. Zero external dependencies — uses native
 * macOS JXA (CoreGraphics + System Events) under the hood.
 *
 * Workflow:
 *   1. screenshot → see what's on screen
 *   2. find_elements / click / type → interact
 *   3. screenshot → verify result
 *
 * Requires macOS Accessibility permissions.
 */

import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";
import * as mac from "./macos.js";
import { readScreen, formatScreenAsText, formatScreenMinimal } from "./screen-reader.js";

export { readScreen, formatScreenAsText, formatScreenMinimal } from "./screen-reader.js";
export type { ScreenState, ScreenElement } from "./screen-reader.js";

export const ComputerUseTool: ToolImplementation = {
  definition: {
    name: "computer_use",
    description:
      "Control the computer like a human — move mouse, click, type, press keys, scroll, " +
      "open apps/URLs, drag, and read what's on screen. " +
      "BEST FOR: any desktop interaction, filling forms, web browsing (bypasses ALL bot detection), " +
      "automating repetitive tasks. " +
      "WORKFLOW: analyze_screen → read text output with [ref:N] click coordinates → click/type → analyze_screen to verify. " +
      "analyze_screen returns a TEXT description of everything visible (buttons, links, text fields, content) " +
      "with coordinates — NO vision model needed, works with any text model. " +
      "Use this as FALLBACK when web_crawl or browser get blocked by bot detection. " +
      "Requires macOS Accessibility permissions.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            "Action to perform. One of: " +
            "screenshot, analyze_screen, " +
            "click, double_click, right_click, " +
            "move, move_smooth, drag, scroll, " +
            "type, key, hotkey, " +
            "open_app, open_url, front_app, " +
            "find_elements, cursor_position, screen_size, wait. " +
            "PREFER analyze_screen over screenshot — it returns a text description of everything " +
            "on screen (buttons, text fields, links with click coordinates) without needing vision. " +
            "Use screenshot only when you need the actual image file to send to the user.",
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
            "Text to type (for type action), app name (for open_app), URL (for open_url), or search text (for find_elements)",
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
          description: "Scroll amount (default 3) or wait duration in ms",
        },
        region: {
          type: "string",
          description:
            'Screenshot region as JSON: \'{"x":0,"y":0,"width":500,"height":300}\'. Omit for full screen.',
        },
        app_name: {
          type: "string",
          description:
            "Application name for find_elements (searches UI of that app)",
        },
        role: {
          type: "string",
          description:
            "UI element role filter for find_elements (e.g., AXButton, AXTextField, AXLink)",
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
          await mac.screenshot(outPath, region);

          if (!existsSync(outPath)) {
            return "Screenshot failed — file not created. Ensure macOS screencapture is available.";
          }

          // Auto-include text analysis of the screen so the model can
          // understand what's visible even without vision capabilities
          let textAnalysis = "";
          try {
            const state = await readScreen();
            textAnalysis = "\n\n--- SCREEN CONTENT (text) ---\n" + formatScreenAsText(state);
          } catch {
            // Accessibility might not be available — still return the screenshot
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
          const minimal = args.human_like as boolean; // reuse param for "minimal" mode
          const state = await readScreen(appName);

          const text = minimal
            ? formatScreenMinimal(state)
            : formatScreenAsText(state);

          return text;
        }

        // ── Mouse Click ─────────────────────────────────────────────
        case "click": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: click requires x and y coordinates.";
          await mac.mouseClick(x, y, "left", 1);
          return `Clicked at (${x}, ${y})`;
        }

        case "double_click": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: double_click requires x and y coordinates.";
          await mac.mouseClick(x, y, "left", 2);
          return `Double-clicked at (${x}, ${y})`;
        }

        case "right_click": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: right_click requires x and y coordinates.";
          await mac.mouseClick(x, y, "right", 1);
          return `Right-clicked at (${x}, ${y})`;
        }

        // ── Mouse Move ──────────────────────────────────────────────
        case "move": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: move requires x and y coordinates.";
          await mac.mouseMove(x, y);
          return `Moved cursor to (${x}, ${y})`;
        }

        case "move_smooth": {
          const x = args.x as number;
          const y = args.y as number;
          if (x == null || y == null)
            return "Error: move_smooth requires x and y coordinates.";
          const steps = (args.amount as number) || 25;
          await mac.mouseMoveSmooth(x, y, steps, steps * 12);
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
          await mac.mouseDrag(x, y, toX, toY);
          return `Dragged from (${x}, ${y}) to (${toX}, ${toY})`;
        }

        // ── Scroll ──────────────────────────────────────────────────
        case "scroll": {
          const dir = (args.direction as string) || "down";
          const amt = (args.amount as number) || 3;
          if (!["up", "down", "left", "right"].includes(dir)) {
            return `Error: scroll direction must be up, down, left, or right. Got "${dir}".`;
          }
          await mac.scroll(dir as "up" | "down" | "left" | "right", amt);
          return `Scrolled ${dir} by ${amt}`;
        }

        // ── Keyboard: Type Text ─────────────────────────────────────
        case "type": {
          const text = args.text as string;
          if (!text) return "Error: type requires text parameter.";
          const humanLike = args.human_like as boolean;
          await mac.typeText(text, humanLike ? 50 + Math.random() * 80 : 0);
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
          await mac.pressKey(key, modifiers);
          const modStr = modifiers.length > 0 ? modifiers.join("+") + "+" : "";
          return `Pressed: ${modStr}${key}`;
        }

        // ── Keyboard: Hotkey Combo ──────────────────────────────────
        case "hotkey": {
          const combo = args.key as string;
          if (!combo)
            return "Error: hotkey requires key parameter (e.g., 'cmd+c', 'cmd+shift+s').";
          await mac.hotkey(combo);
          return `Pressed hotkey: ${combo}`;
        }

        // ── App Control ─────────────────────────────────────────────
        case "open_app": {
          const appName = args.text as string;
          if (!appName)
            return "Error: open_app requires text parameter with app name.";
          await mac.openApp(appName);
          return `Opened/activated: ${appName}`;
        }

        case "open_url": {
          const url = args.text as string;
          if (!url) return "Error: open_url requires text parameter with URL.";
          await mac.openUrl(url);
          return `Opened URL in default browser: ${url}`;
        }

        case "front_app": {
          const name = await mac.getFrontApp();
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
          const pos = await mac.getCursorPosition();
          return `Cursor position: (${Math.round(pos.x)}, ${Math.round(pos.y)})`;
        }

        case "screen_size": {
          const dims = await mac.getScreenSize();
          return `Screen: ${dims.width}x${dims.height} pixels (scale factor: ${dims.scaleFactor}x, effective resolution: ${dims.width * dims.scaleFactor}x${dims.height * dims.scaleFactor})`;
        }

        // ── Wait ────────────────────────────────────────────────────
        case "wait": {
          const ms = (args.amount as number) || 1000;
          await mac.wait(ms);
          return `Waited ${ms}ms`;
        }

        default:
          return (
            `Unknown action: "${action}". Available actions:\n` +
            `  Mouse: click, double_click, right_click, move, move_smooth, drag, scroll\n` +
            `  Keyboard: type, key, hotkey\n` +
            `  Apps: open_app, open_url, front_app\n` +
            `  Vision: screenshot, find_elements, cursor_position, screen_size\n` +
            `  Utility: wait`
          );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);

      // Detect accessibility permission errors
      if (
        msg.includes("not allowed assistive access") ||
        msg.includes("accessibility")
      ) {
        return (
          `PERMISSION ERROR: macOS Accessibility access is required.\n` +
          `Go to: System Settings → Privacy & Security → Accessibility\n` +
          `Add and enable the terminal app (Terminal, iTerm2, or VS Code) that is running StackOwl.\n` +
          `Original error: ${msg}`
        );
      }

      return `Error (${action}): ${msg}`;
    }
  },
};
