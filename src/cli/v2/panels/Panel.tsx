import { useState } from "react";
import { Box, Text, useInput, useStdout } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";

export interface PanelItem {
  id: string;
  label: string;
  meta?: string;
  data?: unknown;
}

export interface PanelAction {
  key: string;          // single char or "return"
  label: string;
  handler: (item: PanelItem) => void | Promise<void>;
  confirm?: string;     // if set, show "Type 'yes' to confirm:" before firing
  destructive?: boolean;
}

export interface PanelProps {
  title: string;
  color?: string;
  items: PanelItem[];
  actions?: PanelAction[];
  onDismiss: () => void;
  emptyText?: string;
}

export function Panel({ title, color, items, actions = [], onDismiss, emptyText = "No items." }: PanelProps) {
  const { colors } = useTheme();
  const { stdout } = useStdout();
  const [scrollTop, setScrollTop] = useState(0);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [confirming, setConfirming] = useState<PanelAction | null>(null);
  const [confirmInput, setConfirmInput] = useState("");
  const [working, setWorking] = useState(false);

  const rows = stdout?.rows ?? 24;
  // Reserve: TopBar(2) + Composer(4) + StatusBar(1) + ShortcutsBar(1) + panel header(2) + footer(2) + padding(2)
  const maxVisible = Math.max(3, rows - 14);

  const visibleItems = items.slice(scrollTop, scrollTop + maxVisible);
  const hasAbove = scrollTop > 0;
  const hasBelow = scrollTop + maxVisible < items.length;
  const borderColor = color ?? colors.accent;

  // Clamp selectedIdx to valid range when items change
  const clampedIdx = items.length > 0 ? Math.min(selectedIdx, items.length - 1) : 0;

  useInput((_input, key) => {
    if (confirming) {
      if (key.escape) { setConfirming(null); setConfirmInput(""); return; }
      if (key.return) {
        if (confirmInput.toLowerCase() === "yes") {
          setWorking(true);
          Promise.resolve(confirming.handler(items[clampedIdx]!)).finally(() => {
            setWorking(false);
            setConfirming(null);
            setConfirmInput("");
          });
        } else {
          setConfirming(null);
          setConfirmInput("");
        }
        return;
      }
      if (key.backspace || key.delete) { setConfirmInput((v) => v.slice(0, -1)); return; }
      if (!key.ctrl && !key.meta && _input.length === 1) { setConfirmInput((v) => v + _input); return; }
      return;
    }

    if (key.escape) { onDismiss(); return; }

    if (key.upArrow) {
      const newIdx = Math.max(0, clampedIdx - 1);
      setSelectedIdx(newIdx);
      if (newIdx < scrollTop) setScrollTop(newIdx);
      return;
    }
    if (key.downArrow) {
      const newIdx = Math.min(items.length - 1, clampedIdx + 1);
      setSelectedIdx(newIdx);
      if (newIdx >= scrollTop + maxVisible) setScrollTop(newIdx - maxVisible + 1);
      return;
    }

    // Action key dispatch
    const selectedItem = items[clampedIdx];
    if (!selectedItem) return;
    for (const action of actions) {
      const matches =
        action.key === "return" ? key.return :
        (_input === action.key && !key.ctrl && !key.meta);
      if (matches) {
        if (action.confirm) {
          setConfirming(action);
          setConfirmInput("");
        } else {
          setWorking(true);
          Promise.resolve(action.handler(selectedItem)).finally(() => setWorking(false));
        }
        return;
      }
    }
  });

  const footerActions = confirming
    ? `Type 'yes' to confirm ${confirming.label} (Enter/Esc):`
    : [
        "↑↓ nav",
        ...actions.map((a) => `${a.key === "return" ? "Enter" : a.key} ${a.label}`),
        "Esc close",
      ].join("  ·  ");

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={borderColor} paddingX={1}>
      <Box>
        <Text bold color={borderColor}>{title}</Text>
      </Box>

      {hasAbove && (
        <Box paddingLeft={1}>
          <Text dimColor>▲ {scrollTop} above</Text>
        </Box>
      )}

      {items.length === 0 ? (
        <Box paddingLeft={1}>
          <Text dimColor>{emptyText}</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {visibleItems.map((item, i) => {
            const absIdx = scrollTop + i;
            const isSelected = absIdx === clampedIdx;
            const isWorking = working && isSelected;
            return (
              <Box key={item.id}>
                <Text color={isSelected ? borderColor : undefined} bold={isSelected}>
                  {isSelected ? "❯ " : "  "}
                </Text>
                <Text bold={isSelected} color={isSelected ? borderColor : undefined}>{isWorking ? "⟳ " : ""}{item.label}</Text>
                {item.meta && <Text dimColor>{"  " + item.meta}</Text>}
              </Box>
            );
          })}
        </Box>
      )}

      {hasBelow && (
        <Box paddingLeft={1}>
          <Text dimColor>▼ {items.length - scrollTop - maxVisible} more</Text>
        </Box>
      )}

      <Box marginTop={1}>
        {confirming ? (
          <Box>
            <Text color={colors.warning}>{footerActions} </Text>
            <Text>{confirmInput}</Text>
            <Text color={colors.accent}>▋</Text>
          </Box>
        ) : (
          <Text dimColor>{footerActions}</Text>
        )}
      </Box>
    </Box>
  );
}
