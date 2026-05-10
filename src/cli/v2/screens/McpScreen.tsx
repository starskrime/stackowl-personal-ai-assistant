/**
 * McpScreen — full-screen MCP server status viewer.
 *
 * Replaces ChatScreen entirely when the user types /mcp.
 * Esc returns to chat, restoring terminal scrollback.
 */

import { Box, Text, useInput } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export function McpScreen() {
  const { colors, glyphs } = useTheme();
  const servers = useUiStore((s) => s.mcpServers);
  const cols = useTerminalCols();

  useInput((_input, key) => {
    if (key.escape) {
      globalBridge.dismissMcpView();
    }
  });

  const connectedCount = servers.filter((s) => s.connected).length;
  const divider = glyphs.divider.repeat(Math.max(0, cols));

  return (
    <Box flexDirection="column" width={cols}>
      {/* Header */}
      <Box paddingX={1}>
        <Text bold color={colors.heartbeat}>MCP Servers</Text>
        <Text dimColor>{"  (Esc to return)"}</Text>
      </Box>
      <Text dimColor>{divider}</Text>

      {/* List */}
      {servers.length === 0 ? (
        <Box paddingX={2} paddingY={1}>
          <Text dimColor>No MCP servers configured.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" paddingX={1}>
          {servers.map((server) => (
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

      {/* Footer */}
      <Text dimColor>{divider}</Text>
      <Box paddingX={1}>
        <Text dimColor>
          {servers.length > 0
            ? `${servers.length} server${servers.length === 1 ? "" : "s"} · ${connectedCount} connected`
            : "Configure MCP servers in stackowl.config.json"}
        </Text>
      </Box>
    </Box>
  );
}
