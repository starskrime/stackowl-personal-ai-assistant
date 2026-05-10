/**
 * Puppeteer runtime configuration — architecture-aware Chrome download.
 *
 * Google's "Chrome for Testing" CDN only publishes Linux binaries for x86-64.
 * Both the `linux` and `linux_arm` platform entries download the same
 * linux64/chrome-linux64.zip (x86-64 ELF). That binary cannot run on ARM64
 * (aarch64) or ARM32 (armhf) without an x86 emulator.
 *
 * On Linux ARM we skip the bundled Chrome download entirely and rely on the
 * system Chromium installed via the package manager (apt, dnf, pacman, etc.).
 * See src/browser/puppeteer-fetcher.ts → findExecutable() for the runtime
 * fallback logic that picks up /usr/bin/chromium and similar paths.
 *
 * All other platforms (Linux x86-64, macOS x86-64, macOS ARM64, Windows)
 * receive the correct architecture binary from Google's CDN and work normally.
 */

const skipDownload =
  process.platform === "linux" &&
  (process.arch === "arm64" || process.arch === "arm");

if (skipDownload) {
  process.stderr.write(
    "[puppeteer] Linux ARM detected — skipping Chrome download " +
    "(no ARM Linux Chrome available from Google). " +
    "Install system Chromium: sudo apt install chromium\n",
  );
}

module.exports = { skipDownload };
