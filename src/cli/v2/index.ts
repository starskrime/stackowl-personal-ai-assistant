/**
 * TUI v2 entrypoint — loaded when STACKOWL_TUI=v2.
 *
 * Phase 1: wires gateway adapter, bridge, and full ChatScreen.
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

  const { unmount, waitUntilExit } = render(
    React.createElement(App, { onSubmit: (text: string) => adapter.submitMessage(text) })
  );

  const cleanup = () => {
    disableBracketedPaste();
    uninstallLoggerRedirect();
    adapter.stop();
  };

  process.once("SIGINT", () => { cleanup(); unmount(); process.exit(0); });
  process.once("SIGTERM", () => { cleanup(); unmount(); process.exit(0); });

  // Run adapter and Ink in parallel — both must resolve for a clean exit
  await Promise.all([adapter.start(), waitUntilExit()]);
  cleanup();
}
