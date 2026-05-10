/** Inline overlay listing installed skills. Phase 3-A. */

import { Box, Text, useInput } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";

export function SkillsOverlay() {
  const skills = useUiStore((s) => s.installedSkills);

  useInput((_input, key) => {
    if (key.escape) {
      globalBridge.dismissSkillsView();
    }
  });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="yellow"
      paddingX={1}
      paddingY={0}
    >
      <Box marginBottom={1}>
        <Text bold color="yellow">Installed Skills</Text>
        <Text dimColor>{"  Esc to close"}</Text>
      </Box>

      {skills.length === 0 ? (
        <Box paddingX={1}>
          <Text dimColor>No skills loaded. Check your skills directory.</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {skills.map((skill) => (
            <Box key={skill.name}>
              <Text color={skill.enabled ? "green" : "gray"}>
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

      <Box marginTop={1}>
        <Text dimColor>
          {skills.length > 0
            ? `${skills.length} skill${skills.length === 1 ? "" : "s"} · ${skills.filter((s) => s.enabled).length} enabled`
            : ""}
        </Text>
      </Box>
    </Box>
  );
}
