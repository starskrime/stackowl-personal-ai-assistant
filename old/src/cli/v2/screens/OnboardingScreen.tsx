/**
 * OnboardingScreen — single @clack/prompts wizard.
 *
 * Phase 0 stub. Replaces both onboarding.ts (969 LOC) + onboarding-flow.ts (1075 LOC).
 * Ink unmounts before clack takes stdin; remounts after completion.
 * Wired in Phase 3.
 */

import { Box, Text } from "ink";
import { TopBar } from "../components/TopBar.js";
import { Frame } from "../components/Frame.js";

export function OnboardingScreen() {
  return (
    <Box flexDirection="column">
      <TopBar />
      <Frame>
        <Text dimColor>Onboarding — coming soon. Run ./start.sh for now.</Text>
      </Frame>
    </Box>
  );
}
