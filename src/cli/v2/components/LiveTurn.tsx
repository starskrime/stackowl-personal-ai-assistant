/**
 * LiveTurn — token-streaming live region.
 * Matches Transcript layout: owl header + 2-space indent for content.
 * Tool cards render before text (they execute before the text references them).
 * Cursor ▋ appears at the live streaming edge.
 */

import { Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import type { ToolCall } from "../state/slices/tools.js";
import { OwlAvatar } from "./OwlAvatar.js";
import { ToolCallCard } from "./ToolCallCard.js";

export interface LiveTurnProps {
  turn: Turn | null;
  toolCalls: ToolCall[];
}

export function LiveTurn({ turn, toolCalls }: LiveTurnProps) {
  if (!turn) return null;

  const myTools = toolCalls.filter((tc) => tc.turnId === turn.turnId);

  return (
    <Box flexDirection="column" marginBottom={1}>
      <OwlAvatar
        emoji={turn.owlEmoji ?? "🦉"}
        name={turn.owlName ?? "Owl"}
        role={turn.owlRole}
      />
      {myTools.map((tc) => (
        <ToolCallCard key={tc.toolCallId} tool={tc} />
      ))}
      <Box paddingLeft={2}>
        <Text wrap="wrap">
          {turn.text}
          <Text color="cyan">▋</Text>
        </Text>
      </Box>
    </Box>
  );
}
