/**
 * SkillWizardScreen — @clack/prompts palette for /skills install.
 * Phase 0 stub. Wired in Phase 3.
 */

import { Box, Text } from "ink";
import { TopBar } from "../components/TopBar.js";
import { Frame } from "../components/Frame.js";

export function SkillWizardScreen() {
  return (
    <Box flexDirection="column">
      <TopBar />
      <Frame>
        <Text dimColor>Skills Wizard — coming soon. Use /skills from chat for now.</Text>
      </Frame>
    </Box>
  );
}
