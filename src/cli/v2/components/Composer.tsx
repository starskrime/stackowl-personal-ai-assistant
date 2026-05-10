/**
 * Composer — multi-line input editor + generation state indicator.
 *
 * Idle layout (bordered box):
 *   ╭─────────────────────────────────────────────────╮
 *   │  ❯ your message here▋                           │
 *   │  /help · /owls · /sessions · /skills · /mcp    │
 *   ╰─────────────────────────────────────────────────╯
 *
 * Generating layout:
 *   ╭─────────────────────────────────────────────────╮
 *   │  ✳ generating...                               │
 *   ╰─────────────────────────────────────────────────╯
 *
 * Footer (owl, model, tokens, cost, status) is rendered by <StatusBar />
 * outside this component in ChatScreen.
 */

import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "./spinner.js";

const SLASH_COMMANDS = ["/help", "/owls", "/skills", "/mcp", "/sessions", "/quit", "/exit"];

export interface ComposerProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSubmit, disabled }: ComposerProps) {
  const [value, setValue] = useState("");
  const [genFrame, setGenFrame] = useState(0);
  const historyRef = useRef<InputHistory>(new InputHistory());
  const { exit } = useApp();
  const { colors } = useTheme();

  // Store values needed for keyboard handling
  const mode          = useUiStore((s) => s.mode);

  useEffect(() => {
    if (!disabled) return;
    const t = setInterval(() => setGenFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [disabled]);

  useInput(
    (input, key) => {
      if (key.ctrl && input === "c") { exit(); return; }

      if (key.ctrl && input === "p") {
        if (mode === "parliament") globalBridge.dismissParliamentView();
        else                       globalBridge.requestParliamentView();
        return;
      }

      if (key.return && !key.shift) {
        const trimmed = value.trim();
        if (trimmed === "/sessions") { globalBridge.requestSessionsView(); setValue(""); return; }
        if (trimmed === "/help")     { globalBridge.requestHelpView();     setValue(""); return; }
        if (trimmed === "/owls")     { globalBridge.requestOwlsView();     setValue(""); return; }
        if (trimmed === "/skills")   { globalBridge.requestSkillsView();   setValue(""); return; }
        if (trimmed === "/mcp")      { globalBridge.requestMcpView();      setValue(""); return; }
        if (trimmed === "/quit" || trimmed === "/exit") { exit(); return; }
        if (trimmed) { historyRef.current.push(trimmed); onSubmit(trimmed); }
        setValue("");
        return;
      }

      if (key.backspace || key.delete) { setValue((v) => v.slice(0, -1)); return; }
      if (key.upArrow)   { const p = historyRef.current.prev(value); if (p !== null) setValue(p); return; }
      if (key.downArrow) { const n = historyRef.current.next(); setValue(n !== null ? n : ""); return; }

      if (isPasteChunk(input)) { setValue((v) => v + stripPasteMarkers(input)); return; }
      if (!key.ctrl && !key.meta && input.length === 1) { setValue((v) => v + input); return; }
    },
    { isActive: !disabled },
  );

  const slashHint = (() => {
    if (!value.startsWith("/") || value.includes(" ")) return null;
    const match = SLASH_COMMANDS.find((cmd) => cmd.startsWith(value) && cmd !== value);
    return match ? match.slice(value.length) : null;
  })();

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.dim}
    >
      {disabled ? (
        <Box paddingLeft={1}>
          <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[genFrame]} </Text>
          <Text dimColor>generating...</Text>
        </Box>
      ) : (
        <>
          <Box paddingLeft={1}>
            <Text bold color={colors.user}>❯ </Text>
            <Text>{value}</Text>
            {slashHint ? <Text dimColor>{slashHint}</Text> : null}
            <Text color={colors.accent}>▋</Text>
          </Box>
          {value === "" && (
            <Box paddingLeft={1}>
              <Text dimColor>/help · /owls · /sessions · /skills · /mcp</Text>
            </Box>
          )}
        </>
      )}
    </Box>
  );
}
