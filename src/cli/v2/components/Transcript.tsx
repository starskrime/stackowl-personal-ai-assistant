/**
 * Transcript — <Static> committed turns.
 * No re-diff after turn.committed — native terminal scrollback owns history.
 *
 * Messaging-app layout (adaptive — reacts to terminal resize via useTerminalCols):
 *   User:  fully right-aligned within the 2-col-guttered content area
 *   Owl:   left-aligned, full content width (no 70% cap)
 *   Between turns: centered ─·─·─·─ divider at 80% terminal width (red dashes, yellow dots)
 *
 * Width model:
 *   ChatScreen wraps this in a paddingX={2} Box → 2 cols each side consumed by parent.
 *   contentWidth = cols - 4  (accounts for parent's 2+2 padding)
 *   Explicit width on Static children is required — Static items are measured
 *   in isolation and flex-end has no effect without a concrete width.
 */

import { Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import { OwlAvatar } from "./OwlAvatar.js";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export interface TranscriptProps {
  turns: Turn[];
}

export function Transcript({ turns }: TranscriptProps) {
  const { colors } = useTheme();
  const cols        = useTerminalCols();
  // Parent paddingX={2} consumes 4 cols total; clamp to minimum usable width.
  const contentWidth = Math.max(8, cols - 4);
  // Divider: 80% of full terminal width, capped to fit inside the content area.
  const dividerLen   = Math.max(4, Math.min(Math.round(cols * 0.8), contentWidth - 2));
  const dividerChars = ("─·".repeat(Math.ceil(dividerLen / 2)).slice(0, dividerLen)).split("");
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
            </Box>
          )}

        </Box>
      ))}
    </Box>
  );
}
