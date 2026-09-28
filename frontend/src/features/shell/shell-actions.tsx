"use client";

import { createContext, useContext } from "react";

/** The shell's dialogs, opened from wherever the owner is: the sidebar, the account menu, Search. */
export type ShellActions = { openSearch: () => void; openSettings: () => void };

export const ShellActionsContext = createContext<ShellActions | null>(null);

export function useShellActions(): ShellActions {
  const actions = useContext(ShellActionsContext);
  if (!actions) throw new Error("useShellActions() outside <AppShell>");
  return actions;
}
