/**
 * ShortcutsBar — compact footer line below the composer.
 *
 *   🦉 Hoots · claude-sonnet-4-5 · 1,234 tok · $0.0023 · esc esc to stop
 *   🦉 Hoots · claude-sonnet-4-5 · 1,234 tok · $0.0023 · ? for help
 *
 * Token count and cost are omitted when zero (e.g. first session start).
 */

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
    <Box paddingLeft={2} paddingBottom={0}>
      <Text dimColor>
        {owlEmoji} {owlName}
        {" · "}
        {model}
        {totalTokens > 0 ? ` · ${totalTokens.toLocaleString()} tok · $${totalCostUsd.toFixed(4)}` : ""}
        {" · "}
      </Text>
      {generating ? (
        <Text color="yellow">esc esc to stop</Text>
      ) : (
        <Text dimColor>? for help</Text>
      )}
    </Box>
  );
}
