import React, { createContext, useContext } from "react";
import type { CommandDispatcher } from "../commands/dispatcher.js";

const CommandDispatcherContext = createContext<CommandDispatcher | null>(null);

export function CommandDispatcherProvider({
  dispatcher,
  children,
}: {
  dispatcher: CommandDispatcher;
  children: React.ReactNode;
}) {
  return (
    <CommandDispatcherContext.Provider value={dispatcher}>
      {children}
    </CommandDispatcherContext.Provider>
  );
}

export function useCommandDispatcher(): CommandDispatcher {
  const d = useContext(CommandDispatcherContext);
  if (!d) throw new Error("useCommandDispatcher must be used within CommandDispatcherProvider");
  return d;
}
