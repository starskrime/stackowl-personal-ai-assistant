/** Multi-line input, history, paste. Phase 1. */

import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp, useStdout } from "ink";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";

export interface ComposerProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSubmit, disabled }: ComposerProps) {
  const [value, setValue] = useState("");
  const historyRef = useRef<InputHistory>(new InputHistory());
  const { exit } = useApp();
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);
  const mode = useUiStore((s) => s.mode);

  useEffect(() => {
    const handler = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", handler);
    return () => {
      stdout?.off("resize", handler);
    };
  }, [stdout]);

  useInput(
    (input, key) => {
      // Quit
      if (key.ctrl && input === "c") {
        exit();
        return;
      }

      // Ctrl+P — toggle Parliament theater view
      if (key.ctrl && input === "p") {
        if (mode === "parliament") {
          globalBridge.dismissParliamentView();
        } else {
          globalBridge.requestParliamentView();
        }
        return;
      }

      // Submit on Enter (not Shift+Enter)
      if (key.return && !key.shift) {
        const trimmed = value.trim();

        // ─── Slash command interceptions ─────────────────────────────────
        if (trimmed === "/sessions") {
          globalBridge.requestSessionsView();
          setValue("");
          return;
        }
        if (trimmed === "/help") {
          globalBridge.requestHelpView();
          setValue("");
          return;
        }
        if (trimmed === "/owls") {
          globalBridge.requestOwlsView();
          setValue("");
          return;
        }
        if (trimmed === "/skills") {
          globalBridge.requestSkillsView();
          setValue("");
          return;
        }
        if (trimmed === "/mcp") {
          globalBridge.requestMcpView();
          setValue("");
          return;
        }
        if (trimmed === "/quit" || trimmed === "/exit") {
          exit();
          return;
        }
        // ─────────────────────────────────────────────────────────────────

        if (trimmed) {
          historyRef.current.push(trimmed);
          onSubmit(trimmed);
        }
        setValue("");
        return;
      }

      // Backspace / Delete
      if (key.backspace || key.delete) {
        setValue((v) => v.slice(0, -1));
        return;
      }

      // History navigation
      if (key.upArrow) {
        const prev = historyRef.current.prev(value);
        if (prev !== null) setValue(prev);
        return;
      }

      if (key.downArrow) {
        const next = historyRef.current.next();
        setValue(next !== null ? next : "");
        return;
      }

      // Paste chunk — strip markers and append
      if (isPasteChunk(input)) {
        const cleaned = stripPasteMarkers(input);
        setValue((v) => v + cleaned);
        return;
      }

      // Regular printable character
      if (!key.ctrl && !key.meta && input.length === 1) {
        setValue((v) => v + input);
        return;
      }
    },
    { isActive: !disabled },
  );

  // Autocomplete hint: show matching slash commands when input starts with "/"
  const SLASH_COMMANDS = ["/help", "/owls", "/skills", "/mcp", "/sessions", "/quit", "/exit"];
  const slashHint = (() => {
    if (!value.startsWith("/") || value.includes(" ")) return null;
    const matches = SLASH_COMMANDS.filter((cmd) => cmd.startsWith(value) && cmd !== value);
    return matches.length > 0 ? matches[0]!.slice(value.length) : null;
  })();

  return (
    <Box flexDirection="column">
      <Text dimColor>{"─".repeat(Math.max(0, cols))}</Text>
      <Box>
        <Text color="green">{"› "}</Text>
        <Text>
          {value}
          {slashHint && <Text dimColor>{slashHint}</Text>}
          <Text color="cyan">▋</Text>
        </Text>
      </Box>
    </Box>
  );
}
