/** <Static> committed turns — no re-diff after turn.committed. Phase 1. */

import { Static, Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import { Header } from "./Header.js";

export interface TranscriptProps {
  turns: Turn[];
}

export function Transcript({ turns }: TranscriptProps) {
  return (
    <Static items={turns}>
      {(turn) => (
        <Box key={turn.turnId} flexDirection="column" marginBottom={1}>
          {turn.role === "user" ? (
            <Text color="green">▸ You</Text>
          ) : (
            <Header
              emoji={turn.owlEmoji ?? "🦉"}
              name={turn.owlName ?? "Owl"}
              role={turn.owlRole}
            />
          )}
          <Text>{turn.text}</Text>
        </Box>
      )}
    </Static>
  );
}
