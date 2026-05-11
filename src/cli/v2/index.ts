/**
 * TUI v2 entrypoint — loaded when STACKOWL_TUI=v2.
 *
 * Phase 1: wires gateway adapter, bridge, and full ChatScreen.
 * Phase 3-B: onboarding is handled in chatCommand() (src/index.ts) BEFORE
 *            bootstrap() runs, so the config is already on disk by the time
 *            startV2() is called.
 */

import React from "react";
import { render } from "ink";
import { App } from "./app.js";
import { installLoggerRedirect, uninstallLoggerRedirect } from "./io/logger.js";
import { enableBracketedPaste, disableBracketedPaste } from "./input/paste.js";
import { detectCapabilities } from "./io/capabilities.js";
import { CliV2Adapter } from "../../gateway/adapters/cli-v2.js";
import type { OwlGateway } from "../../gateway/core.js";

export async function startV2(gateway: OwlGateway): Promise<void> {
  const caps = detectCapabilities();
  if (!caps.isTTY) {
    throw new Error("TUI v2 requires a TTY. For non-TTY use, set STACKOWL_JSON=true.");
  }

  const adapter = new CliV2Adapter(gateway);
  gateway.register(adapter);

  installLoggerRedirect();
  enableBracketedPaste();

  // Enter the alternate screen buffer so the whole UI re-renders on resize.
  // Clear the alt-screen and home the cursor so Ink always starts at (0,0).
  // On exit (signal or normal), we restore the main screen so the user lands
  // back in their shell with their prior scrollback intact.
  process.stdout.write("\x1B[?1049h\x1B[2J\x1B[H");

  const restoreScreen = () => { process.stdout.write("\x1B[?1049l"); };

  // On every resize, clear the screen and home the cursor before Ink
  // re-renders. Without this, Ink's cursor-tracking can drift in alt-screen
  // mode, leaving a ghost copy of the old narrow header above the new one.
  const onResize = () => { process.stdout.write("\x1B[H\x1B[2J"); };
  process.stdout.on("resize", onResize);

  const { unmount, waitUntilExit } = render(
    React.createElement(App, {
      onSubmit: (text: string) => adapter.submitMessage(text),
      onResume: (sessionId: string, title: string) => adapter.resumeSession(sessionId, title),
      commandDispatcher: adapter.getCommandDispatcher(),
    })
  );

  const cleanup = () => {
    process.stdout.off("resize", onResize);
    disableBracketedPaste();
    uninstallLoggerRedirect();
    restoreScreen();
    adapter.stop();
  };

  // Signal handlers: unmount Ink first so escape sequences don't corrupt its
  // render buffer, then run cleanup to restore console and disable paste mode.
  process.once("SIGINT", () => { unmount(); cleanup(); process.exit(0); });
  process.once("SIGTERM", () => { unmount(); cleanup(); process.exit(0); });
  process.once("uncaughtException", (err) => { unmount(); restoreScreen(); throw err; });

  // Run adapter and Ink in parallel — both must resolve for a clean exit.
  // The finally block guarantees terminal restoration even if either rejects.
  try {
    await Promise.all([adapter.start(), waitUntilExit()]);
  } finally {
    unmount();
    cleanup();
  }
}
