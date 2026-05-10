/**
 * CommandPalette — help overlay, shown when the user types /help.
 *
 *   ╭─ Keybindings & Commands ─────── Esc to close ─╮
 *   │                                               │
 *   │  Keyboard                                     │
 *   │  ──────────────────────────────────────────   │
 *   │  Enter               Send message             │
 *   │  Shift+Enter         New line                 │
 *   │  ...                                          │
 *   │                                               │
 *   │  Slash Commands                               │
 *   │  ──────────────────────────────────────────   │
 *   │  /sessions           Resume a session         │
 *   │  ...                                          │
 *   ╰───────────────────────────────────────────────╯
 */

import { Box, Text, useInput } from "ink";
import { globalBridge } from "../events/bridge.js";

interface KeyRow {
  key:  string;
  desc: string;
}

const KEYBINDINGS: KeyRow[] = [
  { key: "Enter",       desc: "Send message"              },
  { key: "Shift+Enter", desc: "New line"                  },
  { key: "↑ / ↓",       desc: "Command history"           },
  { key: "Ctrl+P",      desc: "Toggle Parliament theater" },
  { key: "Ctrl+C",      desc: "Quit"                      },
];

const SLASH_COMMANDS: KeyRow[] = [
  { key: "/sessions", desc: "Resume a previous conversation" },
  { key: "/owls",     desc: "Switch active owl persona"      },
  { key: "/skills",   desc: "List installed skills"          },
  { key: "/mcp",      desc: "MCP server status"              },
  { key: "/help",     desc: "This help overlay"              },
  { key: "/quit",     desc: "Exit StackOwl"                  },
];

const KEY_COL = 16;

function Row({ label, desc, color }: { label: string; desc: string; color: string }) {
  return (
    <Box paddingLeft={2}>
      <Text color={color}>{label.padEnd(KEY_COL)}</Text>
      <Text dimColor>{desc}</Text>
    </Box>
  );
}

export function CommandPalette({ onClose: _onClose }: { onClose: () => void }) {
  useInput((_input, key) => {
    if (key.escape) globalBridge.dismissHelpView();
  });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="cyan"
      paddingX={1}
      paddingY={0}
      marginBottom={1}
    >
      {/* Header */}
      <Box justifyContent="space-between" marginBottom={1}>
        <Text bold color="cyan">Keybindings & Commands</Text>
        <Text dimColor>  Esc to close</Text>
      </Box>

      {/* Keyboard section */}
      <Box paddingLeft={2} marginBottom={0}>
        <Text bold>Keyboard</Text>
      </Box>
      <Box paddingLeft={2} marginBottom={0}>
        <Text dimColor>{"─".repeat(38)}</Text>
      </Box>
      {KEYBINDINGS.map((r) => <Row key={r.key} label={r.key} desc={r.desc} color="green" />)}

      <Box marginTop={1} />

      {/* Slash commands section */}
      <Box paddingLeft={2}>
        <Text bold>Slash Commands</Text>
      </Box>
      <Box paddingLeft={2} marginBottom={0}>
        <Text dimColor>{"─".repeat(38)}</Text>
      </Box>
      {SLASH_COMMANDS.map((r) => <Row key={r.key} label={r.key} desc={r.desc} color="yellow" />)}
    </Box>
  );
}
