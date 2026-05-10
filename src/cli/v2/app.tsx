/**
 * app.tsx — Ink root component for TUI v2.
 *
 * Renders the active screen based on ui.mode from the Zustand store.
 */

import { UiStoreProvider, useUiStore } from "./providers/UiStoreProvider.js";
import { EventBusProvider } from "./providers/EventBusProvider.js";
import { ThemeProvider } from "./providers/ThemeProvider.js";
import { CommandDispatcherProvider } from "./providers/CommandDispatcherProvider.js";
import { ChatScreen } from "./screens/ChatScreen.js";
import { ParliamentScreen } from "./screens/ParliamentScreen.js";
import { OnboardingScreen } from "./screens/OnboardingScreen.js";
import { SkillWizardScreen } from "./screens/SkillWizardScreen.js";
import { SessionsScreen } from "./screens/SessionsScreen.js";
import { OwlsScreen } from "./screens/OwlsScreen.js";
import type { CommandDispatcher } from "./commands/dispatcher.js";

interface ActiveScreenProps {
  onSubmit: (text: string) => void;
  onResume: (sessionId: string, title: string) => void;
}

function ActiveScreen({ onSubmit, onResume }: ActiveScreenProps) {
  const mode = useUiStore((s) => s.mode);
  switch (mode) {
    case "parliament":   return <ParliamentScreen />;
    case "onboarding":  return <OnboardingScreen />;
    case "skill-wizard": return <SkillWizardScreen />;
    case "sessions":    return <SessionsScreen onResume={onResume} />;
    case "owls":        return <OwlsScreen />;
    default:            return <ChatScreen onSubmit={onSubmit} />;
  }
}

export interface AppProps {
  onSubmit: (text: string) => void;
  onResume: (sessionId: string, title: string) => void;
  commandDispatcher: CommandDispatcher;
}

export function App({ onSubmit, onResume, commandDispatcher }: AppProps) {
  return (
    <ThemeProvider>
      <UiStoreProvider>
        <EventBusProvider>
          <CommandDispatcherProvider dispatcher={commandDispatcher}>
            <ActiveScreen onSubmit={onSubmit} onResume={onResume} />
          </CommandDispatcherProvider>
        </EventBusProvider>
      </UiStoreProvider>
    </ThemeProvider>
  );
}
