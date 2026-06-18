import { useState, useEffect } from "react";
import { Box, Text, useInput, useStdout } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";

// ─── Editable item spec ───────────────────────────────────────────────────────

export type EditableSpec =
  | { kind: "string"; currentValue: string; mask?: boolean; onSubmit: (raw: string) => Promise<void> | void }
  | { kind: "number"; currentValue: number; onSubmit: (n: number) => Promise<void> | void }
  | { kind: "boolean"; currentValue: boolean; onToggle: () => Promise<void> | void }
  | { kind: "drill"; onEnter: () => void };

// ─── Panel types ─────────────────────────────────────────────────────────────

export interface PanelItem {
  id: string;
  label: string;
  meta?: string;
  data?: unknown;
  edit?: EditableSpec;
}

export interface PanelAction {
  key: string;          // single char or "return"
  label: string;
  handler: (item: PanelItem) => void | Promise<void>;
  confirm?: string;     // if set, show "Type 'yes' to confirm:" before firing
}

export interface PanelProps {
  title: string;
  color?: string;
  items: PanelItem[];
  actions?: PanelAction[];
  onDismiss: () => void;
  emptyText?: string;
  isActive?: boolean;
}

export function Panel({ title, color, items, actions = [], onDismiss, emptyText = "No items.", isActive = true }: PanelProps) {
  const { colors } = useTheme();
  const { stdout } = useStdout();
  const [scrollTop, setScrollTop] = useState(0);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [confirming, setConfirming] = useState<PanelAction | null>(null);
  const [confirmInput, setConfirmInput] = useState("");
  const [editing, setEditing] = useState<PanelItem | null>(null);
  const [editInput, setEditInput] = useState("");
  const [editError, setEditError] = useState("");
  const [working, setWorking] = useState(false);

  const rows = stdout?.rows ?? 24;
  // Reserve: TopBar(2) + Composer(4) + StatusBar(1) + ShortcutsBar(1) + panel header(2) + footer(2) + padding(2)
  const maxVisible = Math.max(3, rows - 14);

  const visibleItems = items.slice(scrollTop, scrollTop + maxVisible);
  const hasAbove = scrollTop > 0;
  const hasBelow = scrollTop + maxVisible < items.length;
  const borderColor = color ?? colors.accent;

  // Reset selection when items list changes
  useEffect(() => {
    setSelectedIdx(0);
    setScrollTop(0);
  }, [items.length]);

  // Clamp selectedIdx to valid range when items change
  const clampedIdx = items.length > 0 ? Math.min(selectedIdx, items.length - 1) : 0;

  function exitEdit() { setEditing(null); setEditInput(""); setEditError(""); }

  useInput((_input, key) => {
    // ── Edit mode ────────────────────────────────────────────────────────────
    if (editing) {
      if (working) return;
      if (key.escape) { exitEdit(); return; }
      if (key.return) {
        const spec = editing.edit!;
        if (spec.kind === "string") {
          setWorking(true);
          setEditError("");
          Promise.resolve(spec.onSubmit(editInput)).finally(() => { setWorking(false); exitEdit(); });
        } else if (spec.kind === "number") {
          const n = Number(editInput.trim());
          if (isNaN(n)) { setEditError(`"${editInput}" is not a number`); return; }
          setWorking(true);
          setEditError("");
          Promise.resolve(spec.onSubmit(n)).finally(() => { setWorking(false); exitEdit(); });
        }
        return;
      }
      if (key.backspace || key.delete) { setEditInput((v) => v.slice(0, -1)); setEditError(""); return; }
      if (!key.ctrl && !key.meta && _input.length === 1) { setEditInput((v) => v + _input); setEditError(""); return; }
      return;
    }

    // ── Confirm mode ─────────────────────────────────────────────────────────
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

    // ── Navigation ───────────────────────────────────────────────────────────
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

    // ── Existing actions ─────────────────────────────────────────────────────
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

    // ── Edit spec fallback (fires only if no action consumed the key) ─────────
    if (!working && selectedItem.edit) {
      const spec = selectedItem.edit;
      if (spec.kind === "boolean" && key.return) {
        setWorking(true);
        Promise.resolve(spec.onToggle()).finally(() => setWorking(false));
        return;
      }
      if (spec.kind === "drill" && key.return) {
        spec.onEnter();
        return;
      }
      if ((spec.kind === "string" || spec.kind === "number") && (_input === "e" || key.return)) {
        const prefill = spec.kind === "string" && !spec.mask ? (spec.currentValue ?? "") : "";
        setEditing(selectedItem);
        setEditInput(prefill);
        setEditError("");
        return;
      }
    }
  }, { isActive });

  // ── Footer text ──────────────────────────────────────────────────────────

  const footerContent = (() => {
    if (editing) {
      const isNum = editing.edit?.kind === "number";
      const label = editing.label;
      return (
        <Box flexDirection="column">
          <Box>
            <Text color={colors.accent}>{`edit ${label}  value: `}</Text>
            <Text>{editInput}</Text>
            <Text color={colors.accent}>▋</Text>
            <Text dimColor>{"  Enter save · Esc cancel"}</Text>
          </Box>
          {editError ? <Text color={colors.warning}>{editError}</Text> : null}
          {isNum ? <Text dimColor>{"  (enter a number)"}</Text> : null}
        </Box>
      );
    }
    if (confirming) {
      const prompt = (confirming.confirm ?? `Type 'yes' to confirm ${confirming.label}`) + " (Enter/Esc):";
      return (
        <Box>
          <Text color={colors.warning}>{prompt} </Text>
          <Text>{confirmInput}</Text>
          <Text color={colors.accent}>▋</Text>
        </Box>
      );
    }
    const editHints = items[clampedIdx]?.edit
      ? (items[clampedIdx]!.edit!.kind === "boolean" ? "  Enter toggle" :
         items[clampedIdx]!.edit!.kind === "drill"   ? "  Enter open" :
         "  e edit")
      : "";
    const hint = [
      "↑↓ nav",
      ...actions.map((a) => `${a.key === "return" ? "Enter" : a.key} ${a.label}`),
      "Esc close",
    ].join("  ·  ") + editHints;
    return <Text dimColor>{hint}</Text>;
  })();

  return (
    <Box flexDirection="column" borderStyle="single" borderTop borderBottom borderLeft={false} borderRight={false} borderColor={borderColor} paddingX={1}>
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
        {footerContent}
      </Box>
    </Box>
  );
}
