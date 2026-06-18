/**
 * StackOwl — Chrome Discovery
 *
 * Shared utility to find a Chrome/Chromium executable on the system.
 * Extracted from compat/tools/browser.ts so BrowserPool can reuse it.
 */

import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

export function findChrome(): string | undefined {
  const candidates = [
    ...(() => {
      try {
        const base = join(
          process.env.HOME || "",
          ".cache",
          "puppeteer",
          "chrome",
        );
        if (!existsSync(base)) return [];
        return readdirSync(base)
          .sort()
          .reverse()
          .flatMap((ver: string) => [
            join(
              base,
              ver,
              "chrome-mac-arm64",
              "Google Chrome for Testing.app",
              "Contents",
              "MacOS",
              "Google Chrome for Testing",
            ),
            join(
              base,
              ver,
              "chrome-mac-x64",
              "Google Chrome for Testing.app",
              "Contents",
              "MacOS",
              "Google Chrome for Testing",
            ),
            join(base, ver, "chrome-linux64", "chrome"),
          ]);
      } catch {
        return [];
      }
    })(),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
  ];
  return candidates.find((p) => existsSync(p));
}
