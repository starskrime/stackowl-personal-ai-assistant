/**
 * StackOwl — Cross-Platform OS Driver Interface
 *
 * Abstracts mouse, keyboard, screenshot, and app control so the
 * computer_use tool works identically on macOS, Windows, and Linux.
 *
 * Implementations:
 *   MacOSDriver  → persistent JXA worker process (~5ms/action, was 700ms)
 *   WindowsDriver → PowerShell + Win32 SendInput API
 *   LinuxDriver   → xdotool + scrot (X11 / Wayland via ydotool fallback)
 */

export interface Point {
  x: number;
  y: number;
}

export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ScreenDimensions {
  width: number;
  height: number;
  scaleFactor: number;
}

export type MouseButton = "left" | "right" | "middle";
export type ScrollDirection = "up" | "down" | "left" | "right";

export interface IOSDriver {
  /** Platform this driver targets */
  readonly platform: "darwin" | "win32" | "linux";

  /** Initialize the driver (start persistent processes, warm up APIs) */
  init(): Promise<void>;

  /** Clean up resources */
  dispose(): Promise<void>;

  /** True if the driver has been initialized and is ready */
  isReady(): boolean;

  // ─── Screen ──────────────────────────────────────────────────

  getScreenSize(): Promise<ScreenDimensions>;
  getCursorPosition(): Promise<Point>;
  screenshot(outputPath: string, region?: Region): Promise<void>;

  // ─── Mouse ───────────────────────────────────────────────────

  /** Instantly move cursor to coordinate */
  mouseMove(x: number, y: number): Promise<void>;

  /** Click at position */
  mouseClick(
    x: number,
    y: number,
    button: MouseButton,
    count: number,
  ): Promise<void>;

  /** Press-drag from one point to another */
  mouseDrag(
    fromX: number,
    fromY: number,
    toX: number,
    toY: number,
  ): Promise<void>;

  /** Scroll the wheel */
  scroll(direction: ScrollDirection, amount: number): Promise<void>;

  // ─── Keyboard ────────────────────────────────────────────────

  /** Type a full string instantly */
  typeText(text: string): Promise<void>;

  /** Type a single character (used for per-char human-like typing) */
  typeChar(char: string): Promise<void>;

  /** Press a key with optional modifiers */
  pressKey(key: string, modifiers?: string[]): Promise<void>;

  // ─── Application ─────────────────────────────────────────────

  openApp(name: string): Promise<void>;
  openUrl(url: string): Promise<void>;
  getFrontApp(): Promise<string>;

  // ─── Accessibility Actions ────────────────────────────────────

  /**
   * Activate a UI element directly via the platform's accessibility API.
   * More reliable than coordinate-based clicking — no DPI/position drift,
   * works even if the window moves or scrolls between readScreen() and the click.
   *
   * Optional: not all platforms implement this (Linux AT-SPI is fragile).
   * Falls back to coordinate clicking when unavailable.
   *
   * @param appName    The process/app name (e.g. "Safari", "Finder")
   * @param elementLabel  Partial match against element title or description
   * @param role       Optional AX role filter (e.g. "AXButton", "AXLink")
   */
  axPress?(appName: string, elementLabel: string, role?: string): Promise<void>;

  // ─── Timing ──────────────────────────────────────────────────

  /** Sleep for duration ms (driver-native, avoids JS timer imprecision) */
  sleep(ms: number): Promise<void>;
}
