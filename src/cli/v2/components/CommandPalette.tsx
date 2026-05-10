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
import { useTheme } from "../providers/ThemeProvider.js";

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

function Row({ label, desc, color, keyCol }: { label: string; desc: string; color: string; keyCol: number }) {
  return (
    <Box paddingLeft={2}>
      <Text color={color}>{label.padEnd(keyCol)}</Text>
      <Text dimColor>{desc}</Text>
    </Box>
  );
}

export function CommandPalette({ onClose: _onClose }: { onClose: () => void }) {
  const { colors, layout, glyphs } = useTheme();

  useInput((_input, key) => {
    if (key.escape) globalBridge.dismissHelpView();
  });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.accent}
      paddingX={1}
      paddingY={0}
      marginBottom={1}
    >
      {/* Header */}
      <Box justifyContent="space-between" marginBottom={1}>
        <Text bold color={colors.accent}>Keybindings & Commands</Text>
        <Text dimColor>  Esc to close</Text>
      </Box>

      {/* Keyboard section */}
      <Box paddingLeft={2} marginBottom={0}>
        <Text bold>Keyboard</Text>
      </Box>
      <Box paddingLeft={2} marginBottom={0}>
        <Text dimColor>{glyphs.divider.repeat(layout.dividerWidth)}</Text>
      </Box>
      {KEYBINDINGS.map((r) => <Row key={r.key} label={r.key} desc={r.desc} color={colors.success} keyCol={layout.keyCol} />)}

      <Box marginTop={1} />

      {/* Slash commands section */}
      <Box paddingLeft={2}>
        <Text bold>Slash Commands</Text>
      </Box>
      <Box paddingLeft={2} marginBottom={0}>
        <Text dimColor>{glyphs.divider.repeat(layout.dividerWidth)}</Text>
      </Box>
      {SLASH_COMMANDS.map((r) => <Row key={r.key} label={r.key} desc={r.desc} color={colors.warning} keyCol={layout.keyCol} />)}
    </Box>
  );
}
