/**
 * TUI v2 entrypoint — loaded when STACKOWL_TUI=v2.
 *
 * Phase 0: boots Ink, renders the Phase 0 scaffold (ChatScreen stub).
 * Phase 1: wires gateway adapter, bridge, and full ChatScreen.
 */

import React from "react";
import { render } from "ink";
import { App } from "./app.js";
import { installLoggerRedirect, uninstallLoggerRedirect } from "./io/logger.js";
import { enableBracketedPaste, disableBracketedPaste } from "./input/paste.js";
import { detectCapabilities } from "./io/capabilities.js";

export async function startV2(): Promise<void> {
  const caps = detectCapabilities();

  if (!caps.isTTY) {
    // Non-TTY: fall back to structured output (same as v1 --json mode).
    // Structured-output.ts is kept from v1 and handles this path.
    throw new Error(
      "TUI v2 requires a TTY. For non-TTY use, set STACKOWL_JSON=true.",
    );
  }

  installLoggerRedirect();
  enableBracketedPaste();

  const { unmount, waitUntilExit } = render(React.createElement(App));

  const cleanup = () => {
    disableBracketedPaste();
    uninstallLoggerRedirect();
  };

  process.once("SIGINT", () => { cleanup(); unmount(); process.exit(0); });
  process.once("SIGTERM", () => { cleanup(); unmount(); process.exit(0); });

  await waitUntilExit();
  cleanup();
}
