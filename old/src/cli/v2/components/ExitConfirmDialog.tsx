/**
 * ExitConfirmDialog — full-screen modal asking "Exit StackOwl? Yes / No".
 *
 * Takes over the entire screen while open (ChatScreen renders only this).
 * Keyboard: Tab/Shift+Tab or ←/→ cycle focus; Enter activates; Esc = No.
 * Mouse: xterm SGR click events mapped to button bounding boxes.
 * Default focus: "No" — accidental Enter keeps the session alive.
 *
 * Yes path: dispatches /quit (gateway cleanup), shows "Saving…", then exits.
 * Ctrl+C while dialog is open is swallowed — the popup is the only exit gate.
 */

import { useState } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";
import { useTerminalRows } from "../input/useTerminalRows.js";
import { useMouse } from "../input/useMouse.js";
import { useCommandDispatcher } from "../providers/CommandDispatcherProvider.js";
import { uiStore } from "../state/store.js";

// Fixed box dimensions (including border).
const BOX_W = 44;
const BOX_H = 9; // top border + title + blank + subtitle + blank + hint + blank + buttons + bottom border

function dismiss() {
  uiStore.setState({ exitConfirmOpen: false });
}

export function ExitConfirmDialog() {
  const { exit } = useApp();
  const { colors } = useTheme();
  const cols = useTerminalCols();
  const rows = useTerminalRows();
  const dispatcher = useCommandDispatcher();

  const [focused, setFocused] = useState<"yes" | "no">("no");
  const [saving, setSaving] = useState(false);

  // Compute box origin (1-indexed) for mouse hit-testing.
  const boxTop  = Math.max(1, Math.floor((rows - BOX_H) / 2) + 1);
  const boxLeft = Math.max(1, Math.floor((cols - BOX_W) / 2) + 1);
  const boxMid  = boxLeft + Math.floor(BOX_W / 2);

  // Buttons row: top border(1) + title(1) + blank(1) + subtitle(1) + blank(1) + hint(1) + blank(1) = 7 rows in
  const btnRow = boxTop + 7;

  function handleYes() {
    if (saving) return;
    setSaving(true);
    // Dispatch /quit so the gateway runs its session-end cleanup, then exit.
    dispatcher.dispatch("/quit")
      .catch(() => {/* ignore dispatch errors on exit */})
      .finally(() => { dismiss(); exit(); });
  }
  function handleNo() {
    dismiss();
  }

  useMouse(({ row, col, button, type }) => {
    if (type !== "press" || button !== 0) return;
    // Vertical hit area: buttons row ±1 for tolerance
    if (row < btnRow - 1 || row > btnRow + 1) return;
    // Horizontal hit: left half = Yes, right half = No
    if (col >= boxLeft && col < boxMid) {
      handleYes();
    } else if (col >= boxMid && col <= boxLeft + BOX_W - 1) {
      handleNo();
    }
  });

  useInput((input, key) => {
    // Ctrl+C inside the dialog = swallow entirely. The popup is the safety gate;
    // the only exits are Tab+Enter (Yes), Esc/Enter on No, or mouse click.
    if (key.ctrl && input === "c") { return; }
    if (saving) return; // block all input while saving
    if (key.escape) { handleNo(); return; }
    if (key.tab || (key.shift && key.tab)) {
      setFocused((f) => (f === "yes" ? "no" : "yes"));
      return;
    }
    if (key.leftArrow)  { setFocused("yes"); return; }
    if (key.rightArrow) { setFocused("no");  return; }
    if (key.return) {
      if (focused === "yes") handleYes(); else handleNo();
      return;
    }
  });

  const yesLabel = saving
    ? <Text color={colors.warning}>Saving…</Text>
    : focused === "yes"
      ? <Text bold inverse> Yes </Text>
      : <Text>{"[ Yes ]"}</Text>;

  const noLabel = saving
    ? null
    : focused === "no"
      ? <Text bold inverse> No  </Text>
      : <Text>{"[ No  ]"}</Text>;

  return (
    <Box
      flexDirection="column"
      justifyContent="center"
      alignItems="center"
      width={cols}
      height={rows}
    >
      <Box
        flexDirection="column"
        borderStyle="round"
        borderColor={colors.warning}
        paddingX={3}
        paddingY={0}
        width={BOX_W}
      >
        <Box justifyContent="center" marginBottom={1}>
          <Text bold color={colors.warning}>Exit StackOwl?</Text>
        </Box>
        <Box justifyContent="center" marginBottom={1}>
          <Text dimColor>Your session will be saved.</Text>
        </Box>
        <Box justifyContent="center" marginBottom={1}>
          <Text dimColor>Tab · ←→ to switch   Enter to confirm   Esc to cancel</Text>
        </Box>
        <Box justifyContent="center" gap={4}>
          {yesLabel}
          {noLabel}
        </Box>
      </Box>
    </Box>
  );
}
