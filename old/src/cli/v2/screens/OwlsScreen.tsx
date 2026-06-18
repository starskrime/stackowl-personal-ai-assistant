/**
 * OwlsScreen — active owl switcher.
 *
 * Opened when the user types /owls and presses Enter.
 * Lists available owl personas. Arrow keys navigate, Enter switches,
 * Escape cancels and returns to chat.
 *
 * Architecture:
 *   - globalBridge.changeOwl()       → owl.changed → mode: "chat" + updates activeOwlName
 *   - globalBridge.dismissOwlsView() → owls.view.dismissed → mode: "chat"
 */

import { Box, Text, useInput } from "ink";
import { useState, useEffect } from "react";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export function OwlsScreen() {
  const { colors, glyphs } = useTheme();
  const owls = useUiStore((s) => s.availableOwls);
  const cols = useTerminalCols();
  const [cursor, setCursor] = useState(() => {
    // Start cursor on the currently-active owl.
    const activeIdx = owls.findIndex((o) => o.isActive);
    return activeIdx >= 0 ? activeIdx : 0;
  });

  // Reset cursor to active owl when owls list changes.
  useEffect(() => {
    const activeIdx = owls.findIndex((o) => o.isActive);
    setCursor(activeIdx >= 0 ? activeIdx : 0);
  }, [owls.length]);

  useInput((input, key) => {
    if (key.escape) {
      globalBridge.dismissOwlsView();
      return;
    }

    if (key.upArrow) {
      setCursor((c) => Math.max(0, c - 1));
      return;
    }

    if (key.downArrow) {
      setCursor((c) => Math.min(owls.length - 1, c + 1));
      return;
    }

    if (key.return) {
      const selected = owls[cursor];
      if (selected) {
        globalBridge.changeOwl(selected.name, selected.emoji);
      } else {
        globalBridge.dismissOwlsView();
      }
      return;
    }

    // 'q' also dismisses
    if (!key.ctrl && !key.meta && input === "q") {
      globalBridge.dismissOwlsView();
    }
  });

  return (
    <Box flexDirection="column" width={cols}>
      {/* Header */}
      <Box paddingX={1} paddingY={0}>
        <Text bold color={colors.accent}>{"Switch Owl"}</Text>
        <Text dimColor>{"  (↑↓ navigate · Enter select · Esc cancel)"}</Text>
      </Box>

      <Text>{"─".repeat(Math.max(0, cols))}</Text>

      {/* Owl list */}
      {owls.length === 0 ? (
        <Box paddingX={2} paddingY={1}>
          <Text dimColor>No owls loaded.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" paddingX={1}>
          {owls.map((owl, i) => {
            const isSelected = i === cursor;
            const prefix = isSelected ? glyphs.selection + " " : "  ";
            return (
              <Box key={owl.name}>
                <Text bold={isSelected} color={isSelected ? colors.accent : owl.isActive ? colors.success : undefined}>
                  {prefix}
                </Text>
                <Text bold={isSelected} color={isSelected ? colors.accent : undefined}>
                  {owl.emoji + " " + owl.name}
                </Text>
                {owl.description && (
                  <Text dimColor>{"  " + owl.description}</Text>
                )}
                {owl.isActive && (
                  <Text color={colors.success} dimColor>{"  [active]"}</Text>
                )}
              </Box>
            );
          })}
        </Box>
      )}

      <Text>{"─".repeat(Math.max(0, cols))}</Text>

      {/* Footer */}
      <Box paddingX={1}>
        <Text dimColor>
          {owls.length > 0
            ? `${owls.length} owl${owls.length === 1 ? "" : "s"} available`
            : "Check your owls directory"}
        </Text>
      </Box>
    </Box>
  );
}
