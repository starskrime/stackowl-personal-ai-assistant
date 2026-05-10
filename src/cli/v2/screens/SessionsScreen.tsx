/**
 * SessionsScreen — recent-session resume picker.
 *
 * Opened when the user types /sessions and presses Enter.
 * Displays up to 20 recent sessions sorted by most-recent-first.
 * Arrow keys navigate the list; Enter resumes the selected session;
 * Escape returns to chat without changing session.
 *
 * Architecture:
 *   - globalBridge.dismissSessionsView()  → sessions.view.dismissed → mode: "chat"
 *   - adapter.resumeSession(id)           → session.changed + sessions.view.dismissed
 */

import { Box, Text, useInput, useStdout } from "ink";
import { useState, useEffect } from "react";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";

interface SessionsScreenProps {
  /** Called when the user picks a session to resume. */
  onResume: (sessionId: string, title: string) => void;
}

function formatRelativeTime(ts: number): string {
  const diffMs = Date.now() - ts;
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  return `${diffD}d ago`;
}

export function SessionsScreen({ onResume }: SessionsScreenProps) {
  const sessions = useUiStore((s) => s.recentSessions);
  const activeSessionId = useUiStore((s) => s.activeSessionId);
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    const handler = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", handler);
    return () => { stdout?.off("resize", handler); };
  }, [stdout]);

  // Reset cursor when sessions list changes.
  useEffect(() => {
    setCursor(0);
  }, [sessions.length]);

  useInput((input, key) => {
    if (key.escape) {
      globalBridge.dismissSessionsView();
      return;
    }

    if (key.upArrow) {
      setCursor((c) => Math.max(0, c - 1));
      return;
    }

    if (key.downArrow) {
      setCursor((c) => Math.min(sessions.length - 1, c + 1));
      return;
    }

    if (key.return) {
      const selected = sessions[cursor];
      if (selected) {
        onResume(selected.sessionId, selected.title);
      } else {
        globalBridge.dismissSessionsView();
      }
      return;
    }

    // 'q' also dismisses
    if (!key.ctrl && !key.meta && input === "q") {
      globalBridge.dismissSessionsView();
    }
  });

  return (
    <Box flexDirection="column" width={cols}>
      {/* Header */}
      <Box paddingX={1} paddingY={0}>
        <Text bold color="cyan">{"🗂  Recent Sessions"}</Text>
        <Text dimColor>{"  (↑↓ navigate · Enter resume · Esc cancel)"}</Text>
      </Box>

      <Text>{"─".repeat(Math.max(0, cols))}</Text>

      {/* Session list */}
      {sessions.length === 0 ? (
        <Box paddingX={2} paddingY={1}>
          <Text dimColor>No previous sessions found.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" paddingX={1}>
          {sessions.map((s, i) => {
            const isActive = s.sessionId === activeSessionId;
            const isSelected = i === cursor;
            const prefix = isSelected ? "> " : "  ";
            const timeStr = formatRelativeTime(s.lastActiveAt);

            return (
              <Box key={s.sessionId}>
                <Text
                  bold={isSelected}
                  color={isSelected ? "cyan" : isActive ? "green" : undefined}
                >
                  {prefix}
                </Text>
                <Text
                  bold={isSelected}
                  color={isSelected ? "cyan" : undefined}
                >
                  {s.title}
                </Text>
                <Text dimColor>{"  "}{timeStr}</Text>
                {isActive && (
                  <Text color="green" dimColor>{"  [current]"}</Text>
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
          {sessions.length > 0
            ? `${sessions.length} session${sessions.length === 1 ? "" : "s"} · /sessions to reopen`
            : "Start a conversation to create your first session"}
        </Text>
      </Box>
    </Box>
  );
}
