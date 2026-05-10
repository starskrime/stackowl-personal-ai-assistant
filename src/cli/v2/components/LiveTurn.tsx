/** Token-streaming live region. Only subscribes to token.delta. Phase 1. */

import { Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import type { ToolCall } from "../state/slices/tools.js";
import { Header } from "./Header.js";
import { ToolCallCard } from "./ToolCallCard.js";

export interface LiveTurnProps {
  turn: Turn | null;
  toolCalls: ToolCall[];
}

export function LiveTurn({ turn, toolCalls }: LiveTurnProps) {
  if (!turn) return null;

  // Filter tool calls belonging to this turn
  const myTools = toolCalls.filter((tc) => tc.turnId === turn.turnId);

  return (
    <Box flexDirection="column">
      <Header
        emoji={turn.owlEmoji ?? "🦉"}
        name={turn.owlName ?? "Owl"}
        role={turn.owlRole}
      />
      {myTools.map((tc) => (
        <ToolCallCard key={tc.toolCallId} tool={tc} />
      ))}
      <Text>
        {turn.text}
        <Text color="cyan">▋</Text>
      </Text>
    </Box>
  );
}
