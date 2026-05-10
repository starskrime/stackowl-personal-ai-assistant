/**
 * SkillsScreen — full-screen installed-skills viewer.
 *
 * Replaces ChatScreen entirely when the user types /skills.
 * Esc returns to chat, restoring terminal scrollback.
 */

import { Box, Text, useInput } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import { useTheme } from "../providers/ThemeProvider.js";
import { useTerminalCols } from "../input/useTerminalCols.js";

export function SkillsScreen() {
  const { colors, glyphs } = useTheme();
  const skills = useUiStore((s) => s.installedSkills);
  const cols = useTerminalCols();

  useInput((_input, key) => {
    if (key.escape) {
      globalBridge.dismissSkillsView();
    }
  });

  const enabledCount = skills.filter((s) => s.enabled).length;
  const divider = glyphs.divider.repeat(Math.max(0, cols));

  return (
    <Box flexDirection="column" width={cols}>
      {/* Header */}
      <Box paddingX={1}>
        <Text bold color={colors.warning}>Installed Skills</Text>
        <Text dimColor>{"  (Esc to return)"}</Text>
      </Box>
      <Text dimColor>{divider}</Text>

      {/* List */}
      {skills.length === 0 ? (
        <Box paddingX={2} paddingY={1}>
          <Text dimColor>No skills loaded. Check your skills directory.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" paddingX={1}>
          {skills.map((skill) => (
            <Box key={skill.name}>
              <Text color={skill.enabled ? colors.success : colors.dim}>
                {skill.enabled ? "  ✓  " : "  ✗  "}
              </Text>
              <Text bold={skill.enabled}>{skill.name}</Text>
              {skill.description && (
                <Text dimColor>{"  " + skill.description.slice(0, 60)}</Text>
              )}
            </Box>
          ))}
        </Box>
      )}

      {/* Footer */}
      <Text dimColor>{divider}</Text>
      <Box paddingX={1}>
        <Text dimColor>
          {skills.length > 0
            ? `${skills.length} skill${skills.length === 1 ? "" : "s"} · ${enabledCount} enabled`
            : "Install skills via ClawHub"}
        </Text>
      </Box>
    </Box>
  );
}
