/**
 * LiveTurn — token-streaming live region.
 * Left-aligned (owl side). Matches Transcript's owl layout exactly.
 * Width = cols - 4 (parent paddingX={2} consumes 4 cols). Adaptive on resize.
 * Tool cards render before text (they execute before the text references them).
 * Cursor ▋ appears at the live streaming edge.
 */

import { Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import type { ToolCall } from "../state/slices/tools.js";
import { OwlAvatar } from "./OwlAvatar.js";
import { ToolCallCard } from "./ToolCallCard.js";
import { ThinkingIndicator } from "./ThinkingIndicator.js";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export interface LiveTurnProps {
  turn: Turn | null;
  toolCalls: ToolCall[];
  memoryCount?: number;
}

export function LiveTurn({ turn, toolCalls, memoryCount = 0 }: LiveTurnProps) {
  const { colors }  = useTheme();
  const cols        = useTerminalCols();
  const contentWidth = Math.max(8, cols - 4);

  if (!turn) return null;

  const myTools = toolCalls.filter((tc) => tc.turnId === turn.turnId);

  return (
    <Box flexDirection="column" width={contentWidth} marginBottom={1}>
      <OwlAvatar
        emoji={turn.owlEmoji ?? "🦉"}
        name={turn.owlName ?? "Owl"}
        role={turn.owlRole}
      />
      {myTools.map((tc) => (
        <ToolCallCard key={tc.toolCallId} tool={tc} />
      ))}
      <Box paddingLeft={2}>
        {turn.text === "" && myTools.length === 0 ? (
          <ThinkingIndicator />
        ) : (
          <Text wrap="wrap">
            {turn.text}
            <Text color={colors.accent}>▋</Text>
          </Text>
        )}
      </Box>
      {memoryCount > 0 && (
        <Box paddingLeft={2}>
          <Text color="cyan" dimColor>[+{memoryCount}m]</Text>
        </Box>
      )}
    </Box>
  );
}
