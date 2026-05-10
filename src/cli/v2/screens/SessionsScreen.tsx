/**
 * SessionsScreen — recent-session resume picker.
 *
 *   🗂  Recent Sessions   ↑↓ navigate · Enter resume · Esc cancel
 *   ─────────────────────────────────────────────────────────────
 *   ❯  My last conversation                     2m ago
 *      Another session                          1h ago  [current]
 *      Old topic                                3d ago
 *   ─────────────────────────────────────────────────────────────
 *   3 sessions
 */

import { Box, Text, useInput, useStdout } from "ink";
import { useState, useEffect } from "react";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";

interface SessionsScreenProps {
  onResume: (sessionId: string, title: string) => void;
}

function formatRelativeTime(ts: number): string {
  const diffMs  = Date.now() - ts;
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1)  return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24)   return `${diffH}h ago`;
  return `${Math.floor(diffH / 24)}d ago`;
}

export function SessionsScreen({ onResume }: SessionsScreenProps) {
  const sessions       = useUiStore((s) => s.recentSessions);
  const activeSession  = useUiStore((s) => s.activeSessionId);
  const { stdout }     = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    const h = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", h);
    return () => { stdout?.off("resize", h); };
  }, [stdout]);

  useEffect(() => { setCursor(0); }, [sessions.length]);

  useInput((input, key) => {
    if (key.escape || (!key.ctrl && !key.meta && input === "q")) {
      globalBridge.dismissSessionsView();
      return;
    }
    if (key.upArrow)   { setCursor((c) => Math.max(0, c - 1)); return; }
    if (key.downArrow) { setCursor((c) => Math.min(sessions.length - 1, c + 1)); return; }
    if (key.return) {
      const sel = sessions[cursor];
      if (sel) onResume(sel.sessionId, sel.title);
      else     globalBridge.dismissSessionsView();
    }
  });

  const divider = "─".repeat(Math.max(0, cols));

  return (
    <Box flexDirection="column" width={cols}>
      {/* Header */}
      <Box paddingX={1}>
        <Text bold color="cyan">🗂  Recent Sessions</Text>
        <Text dimColor>   ↑↓ navigate · Enter resume · Esc cancel</Text>
      </Box>

      <Text dimColor>{divider}</Text>

      {/* List */}
      {sessions.length === 0 ? (
        <Box paddingLeft={3} paddingY={1}>
          <Text dimColor>No previous sessions found.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" paddingX={1}>
          {sessions.map((s, i) => {
            const isSelected = i === cursor;
            const isCurrent  = s.sessionId === activeSession;
            return (
              <Box key={s.sessionId}>
                <Text bold={isSelected} color={isSelected ? "cyan" : undefined}>
                  {isSelected ? "❯  " : "   "}
                </Text>
                <Text bold={isSelected} color={isSelected ? "cyan" : undefined}>
                  {s.title}
                </Text>
                <Text dimColor>{"  "}{formatRelativeTime(s.lastActiveAt)}</Text>
                {isCurrent && <Text dimColor color="green">{"  [current]"}</Text>}
              </Box>
            );
          })}
        </Box>
      )}

      <Text dimColor>{divider}</Text>

      {/* Footer */}
      <Box paddingX={1}>
        <Text dimColor>
          {sessions.length > 0
            ? `${sessions.length} session${sessions.length === 1 ? "" : "s"}`
            : "Start a conversation to create your first session"}
        </Text>
      </Box>
    </Box>
  );
}
