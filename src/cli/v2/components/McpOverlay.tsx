/** Inline overlay showing MCP server status. Phase 3-A. */

import { Box, Text, useInput } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";

export function McpOverlay() {
  const servers = useUiStore((s) => s.mcpServers);

  useInput((_input, key) => {
    if (key.escape) {
      globalBridge.dismissMcpView();
    }
  });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="magenta"
      paddingX={1}
      paddingY={0}
    >
      <Box marginBottom={1}>
        <Text bold color="magenta">MCP Servers</Text>
        <Text dimColor>{"  Esc to close"}</Text>
      </Box>

      {servers.length === 0 ? (
        <Box paddingX={1}>
          <Text dimColor>No MCP servers configured.</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {servers.map((server) => (
            <Box key={server.name}>
              <Text color={server.connected ? "green" : "red"}>
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

      <Box marginTop={1}>
        <Text dimColor>
          {servers.length > 0
            ? `${servers.length} server${servers.length === 1 ? "" : "s"} · ${servers.filter((s) => s.connected).length} connected`
            : ""}
        </Text>
      </Box>
    </Box>
  );
}
