/** <Static> committed turns — no re-diff after turn.committed. Phase 1. */

import { Static, Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
export function Transcript({ turns }: { turns: Turn[] }) {
  return (
    <Static items={turns}>
      {(turn) => (
        <Box key={turn.turnId} flexDirection="column" marginBottom={1}>
          <Text>{turn.role === "user" ? "▸ You" : `${turn.owlEmoji ?? "🦉"} ${turn.owlName ?? "Owl"}`}</Text>
          <Text>{turn.text}</Text>
        </Box>
      )}
    </Static>
  );
}
