/**
 * StackOwl — Linux Driver
 *
 * Desktop automation via xdotool (X11) with ydotool fallback (Wayland).
 * Screenshots via scrot or gnome-screenshot fallback.
 *
 * Install (Ubuntu/Debian): sudo apt install xdotool scrot
 * Wayland: sudo apt install ydotool  (requires ydotoold service)
 *
 * Performance: ~20-60ms per action (tool spawn, no persistent process needed
 * because xdotool starts in ~5ms vs osascript's 700ms).
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type {
  IOSDriver,
  Point,
  Region,
  ScreenDimensions,
  MouseButton,
  ScrollDirection,
} from "./interface.js";

const execFileAsync = promisify(execFile);
const TIMEOUT = 10_000;

// ─── Tool detection ───────────────────────────────────────────────

type MouseTool = "xdotool" | "ydotool";
type ScreenshotTool = "scrot" | "gnome-screenshot" | "import";

async function detectTool(tool: string): Promise<boolean> {
  try {
    await execFileAsync("which", [tool], { timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}

// ─── Key name mapping (xdotool uses X11 keysym names) ────────────

const XKEY: Record<string, string> = {
  enter: "Return",
  return: "Return",
  tab: "Tab",
  backspace: "BackSpace",
  delete: "Delete",
  escape: "Escape",
  esc: "Escape",
  space: "space",
  up: "Up",
  down: "Down",
  left: "Left",
  right: "Right",
  home: "Home",
  end: "End",
  pageup: "Page_Up",
  pagedown: "Page_Down",
  f1: "F1", f2: "F2", f3: "F3", f4: "F4",
  f5: "F5", f6: "F6", f7: "F7", f8: "F8",
  f9: "F9", f10: "F10", f11: "F11", f12: "F12",
  cmd: "super", command: "super", meta: "super",
  ctrl: "ctrl", control: "ctrl",
  shift: "shift",
  alt: "alt",
};

function xkey(key: string): string {
  return XKEY[key.toLowerCase()] ?? key;
}

function xmods(modifiers: string[]): string[] {
  return modifiers.map((m) => {
    const l = m.toLowerCase();
    if (l === "cmd" || l === "command" || l === "meta") return "super";
    if (l === "ctrl" || l === "control") return "ctrl";
    return l;
  });
}

// ─── Linux Driver ─────────────────────────────────────────────────

export class LinuxDriver implements IOSDriver {
  readonly platform = "linux" as const;

  private mouseTool: MouseTool = "xdotool";
  private screenshotTool: ScreenshotTool = "scrot";
  private ready = false;

  isReady(): boolean {
    return this.ready;
  }

  async init(): Promise<void> {
    if (this.ready) return;

    // Detect available tools
    const [hasXdo, hasYdo, hasScrot, hasGnome, hasImport] = await Promise.all([
      detectTool("xdotool"),
      detectTool("ydotool"),
      detectTool("scrot"),
      detectTool("gnome-screenshot"),
      detectTool("import"),
    ]);

    if (hasXdo) {
      this.mouseTool = "xdotool";
    } else if (hasYdo) {
      this.mouseTool = "ydotool";
    } else {
      throw new Error(
        "No automation tool found. Install xdotool: sudo apt install xdotool\n" +
          "For Wayland: sudo apt install ydotool",
      );
    }

    if (hasScrot) {
      this.screenshotTool = "scrot";
    } else if (hasGnome) {
      this.screenshotTool = "gnome-screenshot";
    } else if (hasImport) {
      this.screenshotTool = "import";
    } else {
      // Non-fatal — screenshots just won't work
    }

    this.ready = true;
  }

  async dispose(): Promise<void> {
    this.ready = false;
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private async xdo(...args: string[]): Promise<string> {
    const tool = this.mouseTool === "ydotool" ? "ydotool" : "xdotool";
    const { stdout } = await execFileAsync(tool, args, { timeout: TIMEOUT });
    return stdout.trim();
  }

  private async run(cmd: string, args: string[]): Promise<string> {
    const { stdout } = await execFileAsync(cmd, args, { timeout: TIMEOUT });
    return stdout.trim();
  }

  // ─── IOSDriver ────────────────────────────────────────────────

  async getScreenSize(): Promise<ScreenDimensions> {
    // Use xdpyinfo or xrandr
    try {
      const { stdout } = await execFileAsync("xdpyinfo", [], { timeout: 5000 });
      const match = stdout.match(/dimensions:\s+(\d+)x(\d+)/);
      if (match) {
        return { width: Number(match[1]), height: Number(match[2]), scaleFactor: 1 };
      }
    } catch {}

    // Fallback: xrandr
    try {
      const { stdout } = await execFileAsync("xrandr", [], { timeout: 5000 });
      const match = stdout.match(/current (\d+) x (\d+)/);
      if (match) {
        return { width: Number(match[1]), height: Number(match[2]), scaleFactor: 1 };
      }
    } catch {}

    return { width: 1920, height: 1080, scaleFactor: 1 };
  }

  async getCursorPosition(): Promise<Point> {
    if (this.mouseTool === "xdotool") {
      const out = await this.xdo("getmouselocation", "--shell");
      const x = Number(out.match(/X=(\d+)/)?.[1] ?? 0);
      const y = Number(out.match(/Y=(\d+)/)?.[1] ?? 0);
      return { x, y };
    }
    // ydotool doesn't report cursor position; fall back to X11 query
    return { x: 0, y: 0 };
  }

  async screenshot(outputPath: string, region?: Region): Promise<void> {
    switch (this.screenshotTool) {
      case "scrot": {
        const args = region
          ? ["-a", `${region.x},${region.y},${region.width},${region.height}`, outputPath]
          : [outputPath];
        await execFileAsync("scrot", args, { timeout: TIMEOUT });
        break;
      }
      case "gnome-screenshot": {
        const args = region
          ? ["--area", `${region.x},${region.y},${region.width}x${region.height}`, "-f", outputPath]
          : ["-f", outputPath];
        await execFileAsync("gnome-screenshot", args, { timeout: TIMEOUT });
        break;
      }
      case "import": {
        // ImageMagick import
        const args = region
          ? ["-window", "root", "-crop", `${region.width}x${region.height}+${region.x}+${region.y}`, outputPath]
          : ["-window", "root", outputPath];
        await execFileAsync("import", args, { timeout: TIMEOUT });
        break;
      }
      default:
        throw new Error("No screenshot tool available. Install scrot: sudo apt install scrot");
    }
  }

  async mouseMove(x: number, y: number): Promise<void> {
    if (this.mouseTool === "xdotool") {
      await this.xdo("mousemove", "--sync", String(x), String(y));
    } else {
      await this.xdo("mousemove", `${x}`, `${y}`);
    }
  }

  async mouseClick(x: number, y: number, button: MouseButton, count: number): Promise<void> {
    const btn = button === "right" ? "3" : button === "middle" ? "2" : "1";
    if (this.mouseTool === "xdotool") {
      await this.xdo("mousemove", "--sync", String(x), String(y));
      for (let i = 0; i < count; i++) {
        await this.xdo("click", btn);
      }
    } else {
      await this.xdo("mousemove", `${x}`, `${y}`);
      await this.xdo("click", btn);
    }
  }

  async mouseDrag(fromX: number, fromY: number, toX: number, toY: number): Promise<void> {
    if (this.mouseTool === "xdotool") {
      await this.xdo("mousemove", String(fromX), String(fromY));
      await this.xdo("mousedown", "1");
      // Smooth drag via intermediate points
      const steps = 15;
      for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        const x = Math.round(fromX + (toX - fromX) * t);
        const y = Math.round(fromY + (toY - fromY) * t);
        await this.xdo("mousemove", String(x), String(y));
      }
      await this.xdo("mouseup", "1");
    } else {
      // ydotool: basic drag
      await this.xdo("mousemove", `${fromX}`, `${fromY}`);
      await this.xdo("mousedown", "1");
      await this.xdo("mousemove", `${toX}`, `${toY}`);
      await this.xdo("mouseup", "1");
    }
  }

  async scroll(direction: ScrollDirection, amount: number): Promise<void> {
    if (this.mouseTool === "xdotool") {
      // xdotool: button 4=up, 5=down, 6=left, 7=right
      const btn =
        direction === "up" ? "4" : direction === "down" ? "5" : direction === "left" ? "6" : "7";
      for (let i = 0; i < amount; i++) {
        await this.xdo("click", btn);
      }
    } else {
      const btn =
        direction === "up" ? "4" : direction === "down" ? "5" : direction === "left" ? "6" : "7";
      await this.xdo("click", btn);
    }
  }

  async typeText(text: string): Promise<void> {
    if (this.mouseTool === "xdotool") {
      await this.xdo("type", "--clearmodifiers", "--", text);
    } else {
      await this.xdo("type", text);
    }
  }

  async typeChar(char: string): Promise<void> {
    if (char === "\x08" || char === "\u0008") {
      await this.pressKey("backspace");
      return;
    }
    if (this.mouseTool === "xdotool") {
      await this.xdo("type", "--clearmodifiers", "--", char);
    } else {
      await this.xdo("type", char);
    }
  }

  async pressKey(key: string, modifiers: string[] = []): Promise<void> {
    const k = xkey(key);
    if (modifiers.length > 0) {
      const mods = xmods(modifiers);
      const combo = [...mods, k].join("+");
      if (this.mouseTool === "xdotool") {
        await this.xdo("key", "--clearmodifiers", combo);
      } else {
        await this.xdo("key", combo);
      }
    } else {
      if (this.mouseTool === "xdotool") {
        await this.xdo("key", "--clearmodifiers", k);
      } else {
        await this.xdo("key", k);
      }
    }
  }

  async openApp(name: string): Promise<void> {
    // Try wmctrl first (raise existing), then xdg-open, then direct launch
    try {
      await this.run("wmctrl", ["-a", name]);
      return;
    } catch {}
    try {
      await this.run("xdg-open", [name]);
    } catch {
      await execFileAsync(name, [], { timeout: 5000 }).catch(() => {});
    }
  }

  async openUrl(url: string): Promise<void> {
    await this.run("xdg-open", [url]);
  }

  async getFrontApp(): Promise<string> {
    try {
      // Get active window name via xdotool
      const wid = await this.xdo("getactivewindow");
      const name = await this.xdo("getwindowname", wid.trim());
      return name;
    } catch {
      return "unknown";
    }
  }

  async sleep(ms: number): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, ms));
  }
}
