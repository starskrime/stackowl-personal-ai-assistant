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

  const useAltScreen = process.env.STACKOWL_KEEP_INLINE !== "1";
  if (useAltScreen) {
    process.stdout.write("\x1B[?1049h");
  }

  const { unmount, waitUntilExit } = render(
    React.createElement(App, {
      onSubmit: (text: string) => adapter.submitMessage(text),
      onResume: (sessionId: string, title: string) => adapter.resumeSession(sessionId, title),
    })
  );

  const cleanup = () => {
    if (useAltScreen) {
      process.stdout.write("\x1B[?1049l");
    }
    disableBracketedPaste();
    uninstallLoggerRedirect();
    adapter.stop();
  };

  // Signal handlers: unmount Ink first so escape sequences don't corrupt its
  // render buffer, then run cleanup to restore console and disable paste mode.
  process.once("SIGINT", () => { unmount(); cleanup(); process.exit(0); });
  process.once("SIGTERM", () => { unmount(); cleanup(); process.exit(0); });

  // Run adapter and Ink in parallel — both must resolve for a clean exit.
  // The finally block guarantees terminal restoration even if either rejects.
  try {
    await Promise.all([adapter.start(), waitUntilExit()]);
  } finally {
    unmount();
    cleanup();
  }
}
