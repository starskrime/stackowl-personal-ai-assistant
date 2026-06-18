import { Box, Text } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";

export function ShortcutsBar() {
  const generating  = useUiStore((s) => s.generating);
  const panelFocus  = useUiStore((s) => s.panelFocus);
  const activePanel = useUiStore((s) => s.activePanel);

  let hint: string;

  if (generating) {
    hint = "Esc stop generation";
  } else if (panelFocus === "panel") {
    const props = activePanel?.props as { actions?: Array<{ key: string; label: string }> } | undefined;
    const panelActions = props?.actions ?? [];
    hint = [
      "↑↓ nav",
      ...panelActions.map((a) => `${a.key === "return" ? "Enter" : a.key} ${a.label}`),
      "Esc close",
    ].join("  ·  ");
  } else {
    hint = "Shift+Tab owl  ·  ^P parliament  ·  ^L clear  ·  ^C quit";
  }

  return (
    <Box paddingLeft={1}>
      <Text dimColor>{hint}</Text>
    </Box>
  );
}
