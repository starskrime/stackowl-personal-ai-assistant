/** Footer: active owl · model · tokens · real cost. Phase 1. */

import { Box, Text } from "ink";

export interface ShortcutsBarProps {
  owlEmoji: string;
  owlName: string;
  model: string;
  generating: boolean;
  totalTokens: number;
  totalCostUsd: number;
}

export function ShortcutsBar({
  owlEmoji,
  owlName,
  model,
  generating,
  totalTokens,
  totalCostUsd,
}: ShortcutsBarProps) {
  return (
    <Box>
      <Text dimColor>
        {owlEmoji} {owlName} · {model} · {totalTokens.toLocaleString()} tok · $
        {totalCostUsd.toFixed(4)}
        {generating ? " · esc esc to stop" : " · ? for help"}
      </Text>
    </Box>
  );
}
