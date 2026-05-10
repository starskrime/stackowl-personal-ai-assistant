/**
 * Composer — multi-line input editor + generation state indicator.
 *
 * Idle layout:
 *   ─────────────────────────────────────────────
 *     ❯ your message here▋
 *     /help /owls /sessions /skills /mcp
 *
 * Generating layout:
 *   ─────────────────────────────────────────────
 *     ⠙ generating...
 */

import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp, useStdout } from "ink";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

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
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);
  const mode = useUiStore((s) => s.mode);

  useEffect(() => {
    const handler = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", handler);
    return () => { stdout?.off("resize", handler); };
  }, [stdout]);

  // Spinner when generating
  useEffect(() => {
    if (!disabled) return;
    const t = setInterval(() => setGenFrame((f) => (f + 1) % SPINNER.length), 80);
    return () => clearInterval(t);
  }, [disabled]);

  useInput(
    (input, key) => {
      if (key.ctrl && input === "c") {
        exit();
        return;
      }

      if (key.ctrl && input === "p") {
        if (mode === "parliament") {
          globalBridge.dismissParliamentView();
        } else {
          globalBridge.requestParliamentView();
        }
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

        if (trimmed) {
          historyRef.current.push(trimmed);
          onSubmit(trimmed);
        }
        setValue("");
        return;
      }

      if (key.backspace || key.delete) {
        setValue((v) => v.slice(0, -1));
        return;
      }

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

      if (isPasteChunk(input)) {
        setValue((v) => v + stripPasteMarkers(input));
        return;
      }

      if (!key.ctrl && !key.meta && input.length === 1) {
        setValue((v) => v + input);
        return;
      }
    },
    { isActive: !disabled },
  );

  // Slash command autocomplete hint
  const slashHint = (() => {
    if (!value.startsWith("/") || value.includes(" ")) return null;
    const match = SLASH_COMMANDS.find((cmd) => cmd.startsWith(value) && cmd !== value);
    return match ? match.slice(value.length) : null;
  })();

  const sep = "─".repeat(Math.max(0, cols));

  return (
    <Box flexDirection="column">
      <Text dimColor>{sep}</Text>
      {disabled ? (
        <Box paddingLeft={2}>
          <Text color="cyan">{SPINNER[genFrame]} </Text>
          <Text dimColor>generating...</Text>
        </Box>
      ) : (
        <>
          <Box paddingLeft={2}>
            <Text bold color="green">❯ </Text>
            <Text>{value}</Text>
            {slashHint ? <Text dimColor>{slashHint}</Text> : null}
            <Text color="cyan">▋</Text>
          </Box>
          {value === "" && (
            <Box paddingLeft={2}>
              <Text dimColor>/help · /owls · /sessions · /skills · /mcp</Text>
            </Box>
          )}
        </>
      )}
    </Box>
  );
}
