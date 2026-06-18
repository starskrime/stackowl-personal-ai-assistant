/**
 * Transcript — committed turns, viewport slice.
 * Rendered into a memoized subtree so token.delta events do not cause re-renders here.
 *
 * Messaging-app layout (adaptive — reacts to terminal resize via useTerminalCols):
 *   User:  fully right-aligned within the 2-col-guttered content area
 *   Owl:   left-aligned, full content width (no 70% cap)
 *   Between turns: centered ─·─·─·─ divider at 80% terminal width (red dashes, yellow dots)
 *
 * Width model:
 *   ChatScreen wraps this in a paddingX={2} Box → 2 cols each side consumed by parent.
 *   contentWidth = cols - 4  (accounts for parent's 2+2 padding)
 */

import { memo, useMemo } from "react";
import { Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import { OwlAvatar } from "./OwlAvatar.js";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export interface TranscriptProps {
  turns: Turn[];
}

function TranscriptImpl({ turns }: TranscriptProps) {
  const { colors } = useTheme();
  const cols        = useTerminalCols();
  const { contentWidth, dividerChars } = useMemo(() => {
    const cw = Math.max(8, cols - 4);
    const dl = Math.max(4, Math.min(Math.round(cols * 0.8), cw - 2));
    const dc = ("─·".repeat(Math.ceil(dl / 2)).slice(0, dl)).split("");
    return { contentWidth: cw, dividerChars: dc };
  }, [cols]);
  const firstTurnId  = turns[0]?.turnId;

  return (
    <Box flexDirection="column">
      {turns.map((turn) => (
        <Box key={turn.turnId} flexDirection="column" marginBottom={1} width={contentWidth}>

          {/* Adaptive ─·─·─ divider between turns */}
          {turn.turnId !== firstTurnId && (
            <Box justifyContent="center" width={contentWidth} marginBottom={1}>
              {dividerChars.map((ch, i) => (
                <Text key={i} color={ch === "─" ? "red" : "yellow"}>{ch}</Text>
              ))}
            </Box>
          )}

          {turn.role === "user" ? (
            /* User message — right edge of content area (flush to gutter) */
            <Box flexDirection="row" width={contentWidth} justifyContent="flex-end">
              <Box flexDirection="column" alignItems="flex-end">
                <Text bold color={colors.user}>You ❯</Text>
                <Text wrap="wrap">{turn.text}</Text>
              </Box>
            </Box>
          ) : (
            /* Owl message — left side, full content width */
            <Box flexDirection="column" width={contentWidth}>
              <OwlAvatar
                emoji={turn.owlEmoji ?? "🦉"}
                name={turn.owlName ?? "Owl"}
                role={turn.owlRole}
              />
              <Box paddingLeft={2}>
                <Text wrap="wrap">{turn.text}</Text>
              </Box>
              {(turn.memoryCount ?? 0) > 0 && (
                <Box paddingLeft={2}>
                  <Text color="cyan" dimColor>[+{turn.memoryCount}m]</Text>
                </Box>
              )}
              {turn.cancelled && (
                <Box paddingLeft={2}>
                  <Text dimColor>↩ Cancelled</Text>
                </Box>
              )}
            </Box>
          )}

        </Box>
      ))}
    </Box>
  );
}

export const Transcript = memo(TranscriptImpl);
