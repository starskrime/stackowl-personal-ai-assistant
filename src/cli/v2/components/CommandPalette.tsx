/** Help overlay showing keybindings and slash commands. Phase 3-A. */

import { Box, Text, useInput } from "ink";
import { globalBridge } from "../events/bridge.js";

interface KeyRow {
  key: string;
  desc: string;
}

const KEYBINDINGS: KeyRow[] = [
  { key: "Enter",          desc: "Send message" },
  { key: "Shift+Enter",    desc: "New line" },
  { key: "Ctrl+C",         desc: "Quit" },
  { key: "Ctrl+P",         desc: "Toggle Parliament theater" },
  { key: "↑ / ↓",          desc: "Command history" },
  { key: "Ctrl+R",         desc: "Reverse history search (not yet implemented)" },
];

const SLASH_COMMANDS: KeyRow[] = [
  { key: "/sessions",  desc: "Recent sessions — resume a previous conversation" },
  { key: "/owls",      desc: "Switch active owl persona" },
  { key: "/skills",    desc: "List installed skills" },
  { key: "/mcp",       desc: "MCP server status" },
  { key: "/help",      desc: "This help overlay" },
  { key: "/quit",      desc: "Exit StackOwl" },
];

export function CommandPalette({ onClose: _onClose }: { onClose: () => void }) {
  useInput((_input, key) => {
    if (key.escape) {
      globalBridge.dismissHelpView();
    }
  });

  const keyColWidth = 20;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="cyan"
      paddingX={1}
      paddingY={0}
    >
      {/* Header */}
      <Box marginBottom={1}>
        <Text bold color="cyan">Keybindings & Commands</Text>
        <Text dimColor>{"  Esc to close"}</Text>
      </Box>

      {/* Keybindings section */}
      <Text bold>{"  Keyboard"}</Text>
      <Text dimColor>{"  " + "─".repeat(36)}</Text>
      {KEYBINDINGS.map((row) => (
        <Box key={row.key}>
          <Text color="green">{("  " + row.key).padEnd(keyColWidth + 2)}</Text>
          <Text>{row.desc}</Text>
        </Box>
      ))}

      <Box marginTop={1} />

      {/* Slash commands section */}
      <Text bold>{"  Slash Commands"}</Text>
      <Text dimColor>{"  " + "─".repeat(36)}</Text>
      {SLASH_COMMANDS.map((row) => (
        <Box key={row.key}>
          <Text color="yellow">{("  " + row.key).padEnd(keyColWidth + 2)}</Text>
          <Text>{row.desc}</Text>
        </Box>
      ))}
    </Box>
  );
}
