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
  const [popupIdx, setPopupIdx] = useState(0);
  const historyRef = useRef<InputHistory>(new InputHistory());
  const { exit } = useApp();
  const { colors } = useTheme();

  const mode      = useUiStore((s) => s.mode);
  const generating = useUiStore((s) => s.generating);

  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => setGenFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [generating]);

  // Slash popup: visible when value starts with "/" and has no space (not a sentence)
  const popupCandidates = (() => {
    if (!value.startsWith("/") || value.includes(" ")) return [];
    return SLASH_COMMANDS.filter((cmd) => cmd.startsWith(value));
  })();
  const showPopup = popupCandidates.length > 0 && value !== popupCandidates[0];

  // Reset popup selection index when candidates change
  useEffect(() => { setPopupIdx(0); }, [popupCandidates.length]);

  function dispatchSlash(cmd: string): void {
    if (cmd === "/sessions") { globalBridge.requestSessionsView(); return; }
    if (cmd === "/help")     { globalBridge.requestHelpView();     return; }
    if (cmd === "/owls")     { globalBridge.requestOwlsView();     return; }
    if (cmd === "/skills")   { globalBridge.requestSkillsView();   return; }
    if (cmd === "/mcp")      { globalBridge.requestMcpView();      return; }
    if (cmd === "/quit" || cmd === "/exit") { exit(); }
  }

  useInput(
    (input, key) => {
      if (key.ctrl && input === "c") { exit(); return; }

      if (key.ctrl && input === "p") {
        if (mode === "parliament") globalBridge.dismissParliamentView();
        else                       globalBridge.requestParliamentView();
        return;
      }

      // Arrow navigation inside slash popup
      if (showPopup) {
        if (key.upArrow)   { setPopupIdx((i) => (i - 1 + popupCandidates.length) % popupCandidates.length); return; }
        if (key.downArrow) { setPopupIdx((i) => (i + 1) % popupCandidates.length); return; }
        if (key.escape)    { setValue(""); return; }
        if (key.tab)       { setValue(popupCandidates[popupIdx] ?? value); return; }
      }

      if (key.return && !key.shift) {
        const trimmed = value.trim();
        // If popup is open and there's an exact or single match, dispatch it
        if (showPopup && popupCandidates.length > 0) {
          dispatchSlash(popupCandidates[popupIdx] ?? trimmed);
          setValue("");
          return;
        }
        if (SLASH_COMMANDS.includes(trimmed)) { dispatchSlash(trimmed); setValue(""); return; }
        if (trimmed) { historyRef.current.push(trimmed); onSubmit(trimmed); }
        setValue("");
        return;
      }

      if (key.backspace || key.delete) { setValue((v) => v.slice(0, -1)); return; }

      // Up/down arrow = history navigation when popup is NOT open
      if (!showPopup) {
        if (key.upArrow)   { const p = historyRef.current.prev(value); if (p !== null) setValue(p); return; }
        if (key.downArrow) { const n = historyRef.current.next(); setValue(n !== null ? n : ""); return; }
      }

      if (isPasteChunk(input)) { setValue((v) => v + stripPasteMarkers(input)); return; }
      if (!key.ctrl && !key.meta && input.length === 1) { setValue((v) => v + input); return; }
    },
    { isActive: !disabled },
  );

  return (
    <Box flexDirection="column">
      {/* Slash command popup — rendered above the input box */}
      {showPopup && (
        <Box flexDirection="column" borderStyle="round" borderColor={colors.accent} paddingX={1} marginBottom={0}>
          {popupCandidates.map((cmd, i) => (
            <Box key={cmd}>
              <Text color={i === popupIdx ? colors.accent : undefined} bold={i === popupIdx}>
                {i === popupIdx ? "❯ " : "  "}{cmd}
              </Text>
            </Box>
          ))}
        </Box>
      )}

      {/* Main input box */}
      <Box flexDirection="column" borderStyle="round" borderColor={colors.dim}>
        {generating ? (
          <Box paddingLeft={1}>
            <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[genFrame]} </Text>
            <Text dimColor>generating...</Text>
          </Box>
        ) : (
          <>
            <Box paddingLeft={1}>
              <Text bold color={colors.user}>❯ </Text>
              <Text>{value}</Text>
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
    </Box>
  );
}
