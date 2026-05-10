/**
 * SkillsOverlay — inline scrollable skills list shown above the Composer.
 *
 * Arrow keys scroll through the list. Height is capped so the overlay
 * never pushes the Composer off-screen. Escape or a second /skills closes it.
 */

import { useState } from "react";
import { Box, Text, useInput, useStdout } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import { useTheme } from "../providers/ThemeProvider.js";

export function SkillsOverlay() {
  const skills = useUiStore((s) => s.installedSkills);
  const { colors } = useTheme();
  const { stdout } = useStdout();
  const [scrollTop, setScrollTop] = useState(0);

  // Reserve rows for: TopBar(2) + divider(1) + Composer(3) + StatusBar(1) + overlay header+footer(3) + padding(2)
  const rows = stdout?.rows ?? 24;
  const maxVisible = Math.max(3, rows - 12);
  const visibleSkills = skills.slice(scrollTop, scrollTop + maxVisible);
  const hasAbove = scrollTop > 0;
  const hasBelow = scrollTop + maxVisible < skills.length;

  useInput((_input, key) => {
    if (key.escape) { globalBridge.closePanel(); return; }
    if (key.upArrow)   { setScrollTop((t) => Math.max(0, t - 1)); return; }
    if (key.downArrow) {
      setScrollTop((t) => Math.min(Math.max(0, skills.length - maxVisible), t + 1));
      return;
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={colors.warning} paddingX={1}>
      <Box>
        <Text bold color={colors.warning}>Installed Skills</Text>
        <Text dimColor>{"  ↑↓ scroll · Esc close"}</Text>
      </Box>

      {hasAbove && (
        <Box paddingLeft={1}>
          <Text dimColor>▲ {scrollTop} above</Text>
        </Box>
      )}

      {skills.length === 0 ? (
        <Box paddingLeft={1}>
          <Text dimColor>No skills loaded. Check your skills directory.</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {visibleSkills.map((skill) => (
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

      {hasBelow && (
        <Box paddingLeft={1}>
          <Text dimColor>▼ {skills.length - scrollTop - maxVisible} more below</Text>
        </Box>
      )}

      <Box>
        <Text dimColor>
          {skills.length > 0
            ? `${skills.length} skill${skills.length === 1 ? "" : "s"} · ${skills.filter((s) => s.enabled).length} enabled`
            : ""}
        </Text>
      </Box>
    </Box>
  );
}
