/**
 * TUI v2 entrypoint — loaded when STACKOWL_TUI=v2.
 *
 * Phase 1: wires gateway adapter, bridge, and full ChatScreen.
 * Phase 3-B: runs @clack/prompts onboarding wizard BEFORE Ink mounts
 *            so clack can own stdin without contention.
 */

import React from "react";
import { render } from "ink";
import { App } from "./app.js";
import { installLoggerRedirect, uninstallLoggerRedirect } from "./io/logger.js";
import { enableBracketedPaste, disableBracketedPaste } from "./input/paste.js";
import { detectCapabilities } from "./io/capabilities.js";
import { CliV2Adapter } from "../../gateway/adapters/cli-v2.js";
import type { OwlGateway } from "../../gateway/core.js";
import { needsOnboarding, runOnboardingWizard } from "./screens/onboarding-wizard.js";

export async function startV2(gateway: OwlGateway): Promise<void> {
  const caps = detectCapabilities();
  if (!caps.isTTY) {
    throw new Error("TUI v2 requires a TTY. For non-TTY use, set STACKOWL_JSON=true.");
  }

  // Run onboarding BEFORE Ink mounts. @clack/prompts takes over stdin in raw
  // mode — Ink does the same. They cannot coexist, so clack must finish first.
  if (needsOnboarding()) {
    await runOnboardingWizard();
    // Config is now on disk. Gateway will pick it up via its own loadConfig()
    // call which runs after this function returns.
  }

  const adapter = new CliV2Adapter(gateway);
  gateway.register(adapter);

  installLoggerRedirect();
  enableBracketedPaste();

  const { unmount, waitUntilExit } = render(
    React.createElement(App, {
      onSubmit: (text: string) => adapter.submitMessage(text),
      onResume: (sessionId: string, title: string) => adapter.resumeSession(sessionId, title),
    })
  );

  const cleanup = () => {
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
