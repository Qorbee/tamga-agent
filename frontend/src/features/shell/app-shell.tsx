"use client";

import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";

import { AppSidebar } from "./app-sidebar";
import { SearchDialog } from "./search-dialog";
import { SettingsDialog } from "./settings-dialog";
import { ShellActionsContext } from "./shell-actions";

/**
 * The owner app's frame: sidebar, the page, and the canvas, plus the two dialogs any part of it
 * opens (Search, Settings). The sizes come from the layout, which reads them from cookies, so the
 * first render already has the owner's widths. `canvas` is the canvas, sized by the layout too.
 */
export function AppShell({
  sidebarOpen,
  sidebarWidth,
  canvas,
  children,
}: {
  sidebarOpen: boolean;
  sidebarWidth: number;
  canvas: ReactNode;
  children: ReactNode;
}) {
  const [width, setWidth] = useState(sidebarWidth);
  const [searchOpen, setSearchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const actions = useMemo(
    () => ({
      openSearch: () => {
        setSettingsOpen(false);
        setSearchOpen(true);
      },
      openSettings: () => {
        setSearchOpen(false);
        setSettingsOpen(true);
      },
    }),
    [],
  );

  // Ctrl/⌘ K opens Search from anywhere in the app, as it does in the chat apps the owner knows.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        // One dialog at a time: Search replaces Settings rather than opening on top of it.
        setSettingsOpen(false);
        setSearchOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <ShellActionsContext.Provider value={actions}>
      <SidebarProvider
        defaultOpen={sidebarOpen}
        style={{ "--sidebar-width": `${width}px`, "--sidebar-width-icon": "3.5rem" } as CSSProperties}
        className="h-dvh min-h-0 overflow-hidden"
      >
        <AppSidebar width={width} onWidthChange={setWidth} />
        <SidebarInset className="min-w-0 overflow-y-auto">{children}</SidebarInset>
        {canvas}
      </SidebarProvider>
      <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </ShellActionsContext.Provider>
  );
}
