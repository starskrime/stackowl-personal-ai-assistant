/**
 * McpOverlay — inline scrollable MCP server list shown above the Composer.
 *
 * Arrow keys scroll through the list. Height is capped so the overlay
 * never pushes the Composer off-screen. Escape or a second /mcp closes it.
 */

import { useState } from "react";
import { Box, Text, useInput, useStdout } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import { useTheme } from "../providers/ThemeProvider.js";

export function McpOverlay() {
  const servers = useUiStore((s) => s.mcpServers);
  const { colors } = useTheme();
  const { stdout } = useStdout();
  const [scrollTop, setScrollTop] = useState(0);

  // Reserve rows for: TopBar(2) + divider(1) + Composer(3) + StatusBar(1) + overlay header+footer(3) + padding(2)
  const rows = stdout?.rows ?? 24;
  const maxVisible = Math.max(3, rows - 12);
  const visibleServers = servers.slice(scrollTop, scrollTop + maxVisible);
  const hasAbove = scrollTop > 0;
  const hasBelow = scrollTop + maxVisible < servers.length;

  useInput((_input, key) => {
    if (key.escape) { globalBridge.closePanel(); return; }
    if (key.upArrow)   { setScrollTop((t) => Math.max(0, t - 1)); return; }
    if (key.downArrow) {
      setScrollTop((t) => Math.min(Math.max(0, servers.length - maxVisible), t + 1));
      return;
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={colors.heartbeat} paddingX={1}>
      <Box>
        <Text bold color={colors.heartbeat}>MCP Servers</Text>
        <Text dimColor>{"  ↑↓ scroll · Esc close"}</Text>
      </Box>

      {hasAbove && (
        <Box paddingLeft={1}>
          <Text dimColor>▲ {scrollTop} above</Text>
        </Box>
      )}

      {servers.length === 0 ? (
        <Box paddingLeft={1}>
          <Text dimColor>No MCP servers configured.</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {visibleServers.map((server) => (
            <Box key={server.name}>
              <Text color={server.connected ? colors.success : colors.error}>
                {server.connected ? "  ● " : "  ○ "}
              </Text>
              <Text bold>{server.name}</Text>
              <Text dimColor>{"  " + server.transport}</Text>
              <Text dimColor>
                {"  " + server.toolCount + " tool" + (server.toolCount !== 1 ? "s" : "")}
              </Text>
            </Box>
          ))}
        </Box>
      )}

      {hasBelow && (
        <Box paddingLeft={1}>
          <Text dimColor>▼ {servers.length - scrollTop - maxVisible} more below</Text>
        </Box>
      )}

      <Box>
        <Text dimColor>
          {servers.length > 0
            ? `${servers.length} server${servers.length === 1 ? "" : "s"} · ${servers.filter((s) => s.connected).length} connected`
            : ""}
        </Text>
      </Box>
    </Box>
  );
}
