/**
 * StackOwl — Element 7 T18 — Frontmost browser detector
 *
 * Asks macOS which application is currently in the foreground, then maps
 * the process name onto a normalized browser tag. The unified `live_browser`
 * tool uses this to auto-target whichever browser the user is actually
 * looking at — Safari (JXA driver) or Chrome-family (CDP driver).
 *
 * Returns null when:
 *   - the platform is not darwin
 *   - osascript fails or returns nothing
 *   - the frontmost app is not a known browser
 *
 * The detector deliberately does *not* throw on failure: in the live_browser
 * tool we want a clean "no browser available" branch, not an exception.
 */
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

export type FrontmostBrowser = "safari" | "chrome";

export interface DetectOptions {
  /** Override the osascript runner (used in tests). */
  runner?: () => Promise<string>;
  /** Override the platform check (used in tests). Defaults to process.platform. */
  platform?: NodeJS.Platform | string;
}

const FRONTMOST_OSASCRIPT =
  'tell application "System Events" to get name of first application process whose frontmost is true';

/** Names commonly returned by macOS for Chromium-family browsers. */
const CHROME_FAMILY = new Set([
  "Google Chrome",
  "Google Chrome Canary",
  "Chromium",
  "Brave Browser",
  "Arc",
  "Microsoft Edge",
  "Opera",
  "Vivaldi",
]);

const SAFARI_FAMILY = new Set([
  "Safari",
  "Safari Technology Preview",
]);

async function defaultRunner(): Promise<string> {
  const { stdout } = await execAsync(`osascript -e '${FRONTMOST_OSASCRIPT}'`);
  return stdout;
}

export async function detectFrontmostBrowser(
  opts: DetectOptions = {},
): Promise<FrontmostBrowser | null> {
  const platform = opts.platform ?? process.platform;
  if (platform !== "darwin") return null;

  let raw: string;
  try {
    raw = await (opts.runner ?? defaultRunner)();
  } catch {
    return null;
  }
  const name = raw.trim();
  if (!name) return null;

  if (SAFARI_FAMILY.has(name)) return "safari";
  if (CHROME_FAMILY.has(name)) return "chrome";
  return null;
}
