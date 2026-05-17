/**
 * TUI entrypoint — loaded when STACKOWL_TUI is not set to v1.
 *
 * Phase 1: wires gateway adapter, bridge, and full ChatScreen.
 * Phase 3-B: onboarding is handled in chatCommand() (src/index.ts) BEFORE
 *            bootstrap() runs, so the config is already on disk by the time
 *            startV2() is called.
 */

import React from "react";
import { render } from "ink";
import { App } from "./app.js";
import { uiStore } from "./state/store.js";
import { installLoggerRedirect, uninstallLoggerRedirect } from "./io/logger.js";
import { enableBracketedPaste, disableBracketedPaste } from "./input/paste.js";
import { detectCapabilities } from "./io/capabilities.js";
import { CliAdapter } from "../../gateway/adapters/cli.js";
import type { OwlGateway } from "../../gateway/core.js";

export async function startV2(gateway: OwlGateway): Promise<void> {
  const caps = detectCapabilities();
  if (!caps.isTTY) {
    throw new Error("TUI v2 requires a TTY. For non-TTY use, set STACKOWL_JSON=true.");
  }

  const adapter = new CliAdapter(gateway);
  gateway.register(adapter);

  installLoggerRedirect();
  enableBracketedPaste();

  // Enter the alternate screen buffer so the whole UI re-renders on resize.
  // Clear the alt-screen and home the cursor so Ink always starts at (0,0).
  // On exit (signal or normal), we restore the main screen so the user lands
  // back in their shell with their prior scrollback intact.
  process.stdout.write("\x1B[?1049h\x1B[2J\x1B[H");

  const restoreScreen = () => { process.stdout.write("\x1B[?1049l"); };

  const { unmount, waitUntilExit } = render(
    React.createElement(App, {
      onSubmit: (text: string) => adapter.submitMessage(text),
      onResume: (sessionId: string, title: string) => adapter.resumeSession(sessionId, title),
      commandDispatcher: adapter.getCommandDispatcher(),
    }),
    // Disable Ink's built-in Ctrl+C → exit so our ExitConfirmDialog intercepts it first.
    { exitOnCtrlC: false }
  );

  const cleanup = () => {
    disableBracketedPaste();
    uninstallLoggerRedirect();
    restoreScreen();
    adapter.stop();
  };

  // Signal handlers: unmount Ink first so escape sequences don't corrupt its
  // render buffer, then run cleanup to restore console and disable paste mode.
  // SIGINT is blocked while the exit-confirm dialog is open so the user must
  // explicitly choose Yes/No rather than getting force-killed by a stray signal.
  process.on("SIGINT", () => {
    if (uiStore.getState().exitConfirmOpen) return;
    unmount(); cleanup(); process.exit(0);
  });
  process.once("SIGTERM", () => { unmount(); cleanup(); process.exit(0); });
  process.once("uncaughtException", (err) => { unmount(); restoreScreen(); throw err; });

  // Run adapter and Ink in parallel. When Ink's exit() is called (e.g. dialog
  // "Yes"), waitUntilExit() resolves. We immediately call adapter.stop() so its
  // internal _quitPromise resolves too — otherwise Promise.all deadlocks waiting
  // for adapter.start() while adapter.start() waits for cleanup() in the finally.
  // process.exit(0) in finally guarantees termination: the gateway has long-lived
  // components (Telegram, heartbeat) that survive startV2 returning on their own.
  try {
    await Promise.all([
      adapter.start(),
      waitUntilExit().then(() => adapter.stop()),
    ]);
  } finally {
    unmount();
    cleanup();
    process.exit(0);
  }
}
