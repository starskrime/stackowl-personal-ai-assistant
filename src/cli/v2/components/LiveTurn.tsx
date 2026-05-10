/** Token-streaming live region. Only subscribes to token.delta. Phase 1. */

import { Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
export function LiveTurn({ turn }: { turn: Turn | null }) {
  if (!turn) return null;
  return (
    <Box flexDirection="column">
      <Text>{turn.owlEmoji ?? "🦉"} {turn.owlName ?? "Owl"}</Text>
      <Text>{turn.text}<Text color="cyan">▋</Text></Text>
    </Box>
  );
}
