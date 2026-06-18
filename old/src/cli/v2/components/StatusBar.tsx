import { Box, Text } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { useUiStore } from "../providers/UiStoreProvider.js";

export function StatusBar() {
  const { colors } = useTheme();
  const owlEmoji    = useUiStore((s) => s.activeOwlEmoji);
  const owlName     = useUiStore((s) => s.activeOwlName);
  const model       = useUiStore((s) => s.activeModel);
  const mode        = useUiStore((s) => s.mode);
  const generating  = useUiStore((s) => s.generating);
  const totalTokens = useUiStore((s) => s.totalTokens);
  const totalCostUsd = useUiStore((s) => s.totalCostUsd);
  const ctxPct      = useUiStore((s) => s.contextWindowPct);

  const parts: string[] = [];
  if (owlName)          parts.push(`${owlEmoji} ${owlName}`);
  if (model)            parts.push(model);
  if (ctxPct > 0)       parts.push(`ctx ${ctxPct}%`);
  if (totalTokens > 0)  parts.push(`↑${(totalTokens / 1000).toFixed(1)}k tok`);
  if (totalCostUsd > 0) parts.push(`$${totalCostUsd.toFixed(4)}`);

  const baseText = parts.join(" · ");

  return (
    <Box paddingLeft={1}>
      <Text dimColor>{baseText}</Text>
      <Text dimColor> · </Text>
      {generating ? (
        <Text color={colors.warning}>esc esc to stop</Text>
      ) : (
        <Text dimColor>? for help</Text>
      )}
      {mode !== "chat" && (
        <Text color={colors.accent}> [{mode}]</Text>
      )}
    </Box>
  );
}
