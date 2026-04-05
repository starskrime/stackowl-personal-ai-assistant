/**
 * StackOwl — Driver Manager
 *
 * Singleton that owns the OS driver lifecycle.
 * Detects the current platform, lazily instantiates the correct driver,
 * and provides auto-restart on worker crash.
 */

import type { IOSDriver } from "./interface.js";

export class DriverManager {
  private static instance: DriverManager | null = null;
  private driver: IOSDriver | null = null;
  private initPromise: Promise<IOSDriver> | null = null;

  private constructor() {}

  static getInstance(): DriverManager {
    if (!DriverManager.instance) {
      DriverManager.instance = new DriverManager();
    }
    return DriverManager.instance;
  }

  /**
   * Get the platform driver, initializing it on first call.
   * Subsequent calls return the cached, ready driver.
   */
  async getDriver(): Promise<IOSDriver> {
    // Already initialized and healthy
    if (this.driver?.isReady()) return this.driver;

    // Initialization in progress — wait for it
    if (this.initPromise) return this.initPromise;

    this.initPromise = this.createAndInit().finally(() => {
      this.initPromise = null;
    });

    return this.initPromise;
  }

  private async createAndInit(): Promise<IOSDriver> {
    const os = process.platform;

    let driver: IOSDriver;

    if (os === "darwin") {
      const { MacOSDriver } = await import("./macos.js");
      driver = new MacOSDriver();
    } else if (os === "win32") {
      const { WindowsDriver } = await import("./windows.js");
      driver = new WindowsDriver();
    } else {
      // Linux, FreeBSD, etc.
      const { LinuxDriver } = await import("./linux.js");
      driver = new LinuxDriver();
    }

    await driver.init();
    this.driver = driver;
    return driver;
  }

  /**
   * Dispose the current driver (e.g. on process exit or test teardown).
   */
  async dispose(): Promise<void> {
    if (this.driver) {
      await this.driver.dispose().catch(() => {});
      this.driver = null;
    }
    DriverManager.instance = null;
  }

  /** Expose current platform without initializing a driver */
  static get platform(): "darwin" | "win32" | "linux" {
    const p = process.platform;
    if (p === "darwin") return "darwin";
    if (p === "win32") return "win32";
    return "linux";
  }
}

// Clean up on process exit
process.on("exit", () => {
  DriverManager.getInstance().dispose().catch(() => {});
});
