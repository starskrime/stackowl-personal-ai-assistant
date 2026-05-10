/**
 * Transcript — <Static> committed turns.
 * No re-diff after turn.committed — native terminal scrollback owns history.
 *
 * Visual layout per turn:
 *
 *   User:
 *     ❯ You                    ← bold green header
 *       message text here      ← 2-space indent
 *
 *   Owl:
 *     🦉 Hoots  strategist     ← emoji + bold name + dim role
 *       response text here     ← 2-space indent
 *       └ bash  ✓ 1.2s         ← tool cards (committed state)
 */

import { Static, Box, Text } from "ink";
import type { Turn } from "../state/slices/turns.js";
import { OwlAvatar } from "./OwlAvatar.js";
import { useTheme } from "../providers/ThemeProvider.js";

export interface TranscriptProps {
  turns: Turn[];
}

export function Transcript({ turns }: TranscriptProps) {
  const { colors } = useTheme();
  return (
    <Static items={turns}>
      {(turn) => (
        <Box key={turn.turnId} flexDirection="column" marginBottom={1}>
          {turn.role === "user" ? (
            <>
              <Box>
                <Text bold color={colors.user}>❯ </Text>
                <Text bold color={colors.user}>You</Text>
              </Box>
              <Box paddingLeft={2}>
                <Text wrap="wrap">{turn.text}</Text>
              </Box>
            </>
          ) : (
            <>
              <OwlAvatar
                emoji={turn.owlEmoji ?? "🦉"}
                name={turn.owlName ?? "Owl"}
                role={turn.owlRole}
              />
              <Box paddingLeft={2} marginTop={0}>
                <Text wrap="wrap">{turn.text}</Text>
              </Box>
            </>
          )}
        </Box>
      )}
    </Static>
  );
}
