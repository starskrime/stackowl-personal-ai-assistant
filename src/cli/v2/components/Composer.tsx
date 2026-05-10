/** Multi-line input, history, paste. Phase 1. */

import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp, useStdout } from "ink";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";

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

      // Submit on Enter (not Shift+Enter)
      if (key.return && !key.shift) {
        const trimmed = value.trim();
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

  return (
    <Box flexDirection="column">
      <Text dimColor>{"─".repeat(Math.max(0, cols))}</Text>
      <Box>
        <Text color="green">{"› "}</Text>
        <Text>
          {value}
          <Text color="cyan">▋</Text>
        </Text>
      </Box>
    </Box>
  );
}
