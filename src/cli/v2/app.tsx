/**
 * app.tsx — Ink root component for TUI v2.
 *
 * Renders the active screen based on ui.mode from the Zustand store.
 */

import { UiStoreProvider, useUiStore } from "./providers/UiStoreProvider.js";
import { EventBusProvider } from "./providers/EventBusProvider.js";
import { ThemeProvider } from "./providers/ThemeProvider.js";
import { ChatScreen } from "./screens/ChatScreen.js";
import { ParliamentScreen } from "./screens/ParliamentScreen.js";
import { OnboardingScreen } from "./screens/OnboardingScreen.js";
import { SkillWizardScreen } from "./screens/SkillWizardScreen.js";

function ActiveScreen({ onSubmit }: { onSubmit: (text: string) => void }) {
  const mode = useUiStore((s) => s.mode);
  switch (mode) {
    case "parliament":  return <ParliamentScreen />;
    case "onboarding": return <OnboardingScreen />;
    case "skills":     return <SkillWizardScreen />;
    default:           return <ChatScreen onSubmit={onSubmit} />;
  }
}

export function App({ onSubmit }: { onSubmit: (text: string) => void }) {
  return (
    <ThemeProvider>
      <UiStoreProvider>
        <EventBusProvider>
          <ActiveScreen onSubmit={onSubmit} />
        </EventBusProvider>
      </UiStoreProvider>
    </ThemeProvider>
  );
}
