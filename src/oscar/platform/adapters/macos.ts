import type {
  CanonicalAction,
  CanonicalTarget,
  AccessibilityState,
  AccessibilityElement,
  WindowInfo,
  BoundingBox,
  VerificationResult,
  VerificationCondition,
} from "../../types.js";
import { exec } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

export class MacOSAdapter {
  private focusedApp: string | null = null;

  async getFocusedApp(): Promise<string | null> {
    try {
      const { stdout } = await execAsync(
        'osascript -e \'tell application "System Events" to get name of first process whose frontmost is true\'',
        { timeout: 1000 }
      );
      this.focusedApp = stdout.trim();
      return this.focusedApp;
    } catch {
      return null;
    }
  }

  async getWindowsForApp(bundleId: string): Promise<WindowInfo[]> {
    try {
      const script = `
        tell application "System Events"
          set appRef to application process "${bundleId}"
          set winList to {}
          repeat with w in windows of appRef
            set end of winList to {title:name of w, id:id of w as string, bounds:bounds of w}
          end repeat
          return winList
        end tell
      `;
      const { stdout } = await execAsync(`osascript -e '${script}'`, { timeout: 2000 });
      return this.parseWindowsList(stdout, bundleId);
    } catch (error) {
      console.error("[MacOSAdapter] Failed to get windows:", error);
      return [];
    }
  }

  private parseWindowsList(stdout: string, bundleId: string): WindowInfo[] {
    const windows: WindowInfo[] = [];
    const lines = stdout.trim().split("\n").filter(Boolean);

    for (const line of lines) {
      const match = line.match(/\{title:([^,]*), id:([^,]*), bounds:\{([^}]*)\}\}/);
      if (match) {
        const [, title, id, boundsStr] = match;
        const bounds = this.parseBounds(boundsStr);
        windows.push({
          id: id.trim(),
          title: title.trim(),
          bundleId,
          bounds,
          isFocused: true,
        });
      }
    }
    return windows;
  }

  private parseBounds(boundsStr: string): BoundingBox {
    const parts = boundsStr.split(",").map((s) => parseInt(s.trim()));
    if (parts.length === 4) {
      return {
        x: parts[0],
        y: parts[1],
        width: parts[2] - parts[0],
        height: parts[3] - parts[1],
      };
    }
    return { x: 0, y: 0, width: 0, height: 0 };
  }

  async getAccessibilityTree(maxDepth = 10): Promise<AccessibilityState> {
    const focusedApp = await this.getFocusedApp();
    if (!focusedApp) {
      return { windows: [], elements: new Map() };
    }

    try {
      const bundleId = await this.getBundleIdForApp(focusedApp);
      const windows = await this.getWindowsForApp(bundleId);

      const rootElement = await this.getRootElement(focusedApp);
      const elements = new Map<string, AccessibilityElement>();

      if (rootElement) {
        await this.walkTree(rootElement, elements, 0, maxDepth);
      }

      return {
        focusedElement: this.findFocusedElement(elements),
        windows,
        elements,
      };
    } catch (error) {
      console.error("[MacOSAdapter] Failed to get accessibility tree:", error);
      return { windows: [], elements: new Map() };
    }
  }

  private async getBundleIdForApp(appName: string): Promise<string> {
    try {
      const { stdout } = await execAsync(
        `osascript -e 'tell application "${appName}" to return id'`,
        { timeout: 1000 }
      );
      return stdout.trim();
    } catch {
      return appName;
    }
  }

  private async getRootElement(appName: string): Promise<AccessibilityElement | null> {
    try {
      const script = `
        tell application "System Events"
          tell application process "${appName}"
            return entire contents of window 1
          end tell
        end tell
      `;
      const { stdout } = await execAsync(`osascript -e '${script}'`, { timeout: 3000 });

      if (stdout.trim()) {
        return {
          id: "root",
          path: "root",
          role: "AXWindow",
          label: appName,
          bounds: { x: 0, y: 0, width: 1920, height: 1080 },
          state: {},
          children: [],
        };
      }
    } catch {}
    return null;
  }

  private async walkTree(
    element: AccessibilityElement,
    elements: Map<string, AccessibilityElement>,
    depth: number,
    maxDepth: number
  ): Promise<void> {
    if (depth > maxDepth) return;

    elements.set(element.id, element);

    try {
      const children = await this.getChildren(element);
      for (const child of children) {
        child.parent = element.id;
        element.children.push(child.id);
        await this.walkTree(child, elements, depth + 1, maxDepth);
      }
    } catch {}
  }

  private async getChildren(element: AccessibilityElement): Promise<AccessibilityElement[]> {
    if (!this.focusedApp) return [];

    try {
      const script = `
        tell application "System Events"
          tell application process "${this.focusedApp}"
            set elemRef to UI element "${element.path}"
            set childList to {}
            repeat with child in every UI element of elemRef
              set end of childList to {name:name of child, role:role of child, value:value of child as string}
            end repeat
            return childList
          end tell
        end tell
      `;
      const { stdout } = await execAsync(`osascript -e '${script}'`, { timeout: 2000 });

      const children: AccessibilityElement[] = [];
      const lines = stdout.trim().split("\n").filter(Boolean);

      let idx = 0;
      for (const line of lines) {
        const match = line.match(/\{name:([^,]*), role:([^,]*), value:([^}]*)\}/);
        if (match) {
          const [, name, role, value] = match;
          children.push({
            id: `${element.id}_${idx++}`,
            path: `${element.path}/${name}`,
            role: role.trim(),
            label: name.trim(),
            value: value.trim(),
            bounds: { x: 0, y: 0, width: 0, height: 0 },
            state: {},
            children: [],
          });
        }
      }
      return children;
    } catch {
      return [];
    }
  }

  private findFocusedElement(elements: Map<string, AccessibilityElement>): string | undefined {
    for (const [id, elem] of elements) {
      if (elem.state && elem.state.focused) {
        return id;
      }
    }
    return undefined;
  }

  async executeAction(action: CanonicalAction): Promise<{ success: boolean; error?: string }> {
    switch (action.type) {
      case "click":
        return this.executeClick(action.target, action.params);
      case "type":
        return this.executeType(action.target, action.params);
      case "hotkey":
        return this.executeHotkey(action.params);
      case "launch":
        return this.executeLaunch(action.params);
      case "close":
        return this.executeClose(action.target);
      case "drag":
        return this.executeDrag(action.params);
      case "scroll":
        return this.executeScroll(action.params);
      default:
        return { success: false, error: `Unknown action type: ${action.type}` };
    }
  }

  private async executeClick(
    target: CanonicalTarget,
    params: Record<string, unknown>
  ): Promise<{ success: boolean; error?: string }> {
    try {
      let x: number, y: number;

      if (target.visualRegion) {
        x = target.visualRegion.x + target.visualRegion.width / 2;
        y = target.visualRegion.y + target.visualRegion.height / 2;
      } else if (target.accessibilityPath) {
        const elem = await this.findElementByPath(target.accessibilityPath);
        if (!elem) {
          return { success: false, error: "Element not found" };
        }
        x = elem.bounds.x + elem.bounds.width / 2;
        y = elem.bounds.y + elem.bounds.height / 2;
      } else if (target.semanticSelector) {
        const elem = await this.findElementBySemantic(target.semanticSelector);
        if (!elem) {
          return { success: false, error: "Element not found by semantic selector" };
        }
        x = elem.bounds.x + elem.bounds.width / 2;
        y = elem.bounds.y + elem.bounds.height / 2;
      } else {
        return { success: false, error: "No target specified for click" };
      }

      const button = (params.button as string) || "left";
      const clickCount = (params.clickCount as number) || 1;

      await this.simulateMouseClick(x, y, button, clickCount);
      return { success: true };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async simulateMouseClick(
    x: number,
    y: number,
    button: string,
    clickCount: number
  ): Promise<void> {
    for (let i = 0; i < clickCount; i++) {
      const script = `
        tell application "System Events"
          set mousePos to {${x}, ${y}}
          set mouseData to {12186, 0, 0, 0}
          set mouseData2 to {12192, 0, 0, 0}
          
          ${button === "right" ? `
          do shell script "/usr/bin/python3 -c 'from Quartz import *; k = CGEventCreateMouseEvent(0, kCGEventRightMouseDown, mousePos, 0); CGEventPost(0, k); k = CGEventCreateMouseEvent(0, kCGEventRightMouseUp, mousePos, 0); CGEventPost(0, k)'"
          ` : `
          do shell script "/usr/bin/python3 -c 'from Quartz import *; k = CGEventCreateMouseEvent(0, kCGEventLeftMouseDown, mousePos, 0); CGEventPost(0, k); k = CGEventCreateMouseEvent(0, kCGEventLeftMouseUp, mousePos, 0); CGEventPost(0, k)'"
          `}
        end tell
      `;

      await execAsync(`osascript -e '${script.replace(/'/g, '"')}'`, { timeout: 1000 });
    }
  }

  private async executeType(
    target: CanonicalTarget,
    params: Record<string, unknown>
  ): Promise<{ success: boolean; error?: string }> {
    const text = params.text as string;
    if (!text) {
      return { success: false, error: "No text specified" };
    }

    try {
      if (target.semanticSelector) {
        const elem = await this.findElementBySemantic(target.semanticSelector);
        if (elem && elem.state?.focused) {
          await this.simulateTyping(text);
          return { success: true };
        }
      }

      await this.simulateTyping(text);
      return { success: true };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async simulateTyping(text: string): Promise<void> {
    const escaped = text.replace(/"/g, '\\"');
    const script = `
      tell application "System Events"
        keystroke "${escaped}"
      end tell
    `;
    await execAsync(`osascript -e '${script}'`, { timeout: 1000 });
  }

  private async executeHotkey(params: Record<string, unknown>): Promise<{ success: boolean; error?: string }> {
    const key = params.key as string;
    const modifiers = (params.modifiers as string[]) || [];

    if (!key) {
      return { success: false, error: "No key specified" };
    }

    try {
      const modStr = modifiers
        .map((m) => {
          switch (m.toLowerCase()) {
            case "cmd":
            case "command":
              return "command down";
            case "ctrl":
            case "control":
              return "control down";
            case "opt":
            case "option":
            case "alt":
              return "option down";
            case "shift":
              return "shift down";
            default:
              return "";
          }
        })
        .filter(Boolean)
        .join(", ");

      const script = `
        tell application "System Events"
          keystroke "${key}" ${modStr ? `using {${modStr}}` : ""}
        end tell
      `;
      await execAsync(`osascript -e '${script}'`, { timeout: 1000 });
      return { success: true };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async executeLaunch(params: Record<string, unknown>): Promise<{ success: boolean; error?: string }> {
    const app = params.application as string;
    if (!app) {
      return { success: false, error: "No application specified" };
    }

    try {
      const script = `osascript -e 'tell application "${app}" to activate'`;
      await execAsync(script, { timeout: 5000 });
      return { success: true };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async executeClose(_target?: CanonicalTarget): Promise<{ success: boolean; error?: string }> {
    try {
      const focusedApp = await this.getFocusedApp();
      if (focusedApp) {
        await execAsync(
          `osascript -e 'tell application "${focusedApp}" to close window 1'`,
          { timeout: 1000 }
        );
        return { success: true };
      }
      return { success: false, error: "No focused app" };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async executeDrag(params: Record<string, unknown>): Promise<{ success: boolean; error?: string }> {
    const fromX = params.fromX as number;
    const fromY = params.fromY as number;
    const toX = params.toX as number;
    const toY = params.toY as number;

    if (fromX === undefined || fromY === undefined || toX === undefined || toY === undefined) {
      return { success: false, error: "Missing drag coordinates" };
    }

    try {
      const script = `
        tell application "System Events"
          set dragStart to {${fromX}, ${fromY}}
          set dragEnd to {${toX}, ${toY}}
          
          do shell script "/usr/bin/python3 -c '
import Quartz
start = Quartz.CGPoint(${fromX}, ${fromY})
end = Quartz.CGPoint(${toX}, ${toY})
evDown = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, start, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, evDown)
for i in range(10):
    t = (i + 1) / 10.0
    p = Quartz.CGPoint(start.x + (end.x - start.x) * t, start.y + (end.y - start.y) * t)
    evDrag = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, p, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, evDrag)
    import time; time.sleep(0.01)
evUp = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, end, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, evUp)
'"
        end tell
      `;
      await execAsync(`osascript -e '${script.replace(/'/g, '"')}'`, { timeout: 3000 });
      return { success: true };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async executeScroll(params: Record<string, unknown>): Promise<{ success: boolean; error?: string }> {
    const amount = params.amount as number || 10;
    const direction = params.direction as string || "down";

    try {
      const delta = direction === "up" ? amount : -amount;
      const script = `
        tell application "System Events"
          do shell script "/usr/bin/python3 -c '
import Quartz
ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollWheelEventId, Quartz.kCGScrollWheelEvent2AxisType, ${delta}, 0)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
'"
        end tell
      `;
      await execAsync(`osascript -e '${script.replace(/'/g, '"')}'`, { timeout: 1000 });
      return { success: true };
    } catch (error) {
      return { success: false, error: String(error) };
    }
  }

  private async findElementByPath(path: string): Promise<AccessibilityElement | null> {
    const state = await this.getAccessibilityTree();
    for (const elem of state.elements.values()) {
      if (elem.path === path || elem.path.endsWith(path)) {
        return elem;
      }
    }
    return null;
  }

  private async findElementBySemantic(selector: {
    role?: string;
    label?: string;
    index?: number;
    app?: string;
  }): Promise<AccessibilityElement | null> {
    const state = await this.getAccessibilityTree();
    const matches: AccessibilityElement[] = [];

    for (const elem of state.elements.values()) {
      if (selector.role && elem.role !== selector.role) continue;
      if (selector.label && !elem.label?.includes(selector.label)) continue;
      matches.push(elem);
    }

    if (selector.index !== undefined && selector.index < matches.length) {
      return matches[selector.index];
    }

    return matches[0] || null;
  }

  async verify(conditions: VerificationCondition[]): Promise<VerificationResult[]> {
    const results: VerificationResult[] = [];

    for (const condition of conditions) {
      const result = await this.verifyCondition(condition);
      results.push(result);
    }

    return results;
  }

  private async verifyCondition(condition: VerificationCondition): Promise<VerificationResult> {
    const timeout = condition.timeout || 2000;
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      const state = await this.getAccessibilityTree();

      switch (condition.type) {
        case "element_exists":
          if (condition.target) {
            const elem = await this.findElementBySemantic(condition.target.semanticSelector || {});
            if (elem) {
              return { success: true, attempts: 1 };
            }
          }
          break;

        case "element_focused":
          if (state.focusedElement) {
            return { success: true, attempts: 1 };
          }
          break;

        case "window_opened":
          if (condition.expected?.title) {
            const found = state.windows.some((w) =>
              w.title.includes(condition.expected?.title as string)
            );
            if (found) {
              return { success: true, attempts: 1 };
            }
          }
          break;

        case "text_appeared":
          if (condition.expected?.text) {
            for (const elem of state.elements.values()) {
              if (elem.label?.includes(condition.expected.text as string)) {
                return { success: true, attempts: 1 };
              }
            }
          }
          break;
      }

      await new Promise((resolve) => setTimeout(resolve, 50));
    }

    return { success: false, error: "Verification timeout" };
  }

  async getFrontmostWindow(): Promise<WindowInfo | null> {
    const focusedApp = await this.getFocusedApp();
    if (!focusedApp) return null;

    const bundleId = await this.getBundleIdForApp(focusedApp);
    const windows = await this.getWindowsForApp(bundleId);
    return windows[0] || null;
  }

  async getScreenBounds(): Promise<{ width: number; height: number }> {
    try {
      const { stdout } = await execAsync(
        `osascript -e 'tell application "Finder" to get bounds of window of desktop'`,
        { timeout: 1000 }
      );
      const parts = stdout.trim().split(",").map(Number);
      if (parts.length === 4) {
        return { width: parts[2], height: parts[3] };
      }
    } catch {}
    return { width: 1920, height: 1080 };
  }

  invalidateCache(): void {
    // No-op: caching disabled in current implementation
  }
}

export const macOSAdapter = new MacOSAdapter();
