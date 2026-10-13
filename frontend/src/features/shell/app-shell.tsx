"use client";

import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { useAppHeight } from "@/lib/use-app-height";

import { AppSidebar } from "./app-sidebar";
import { CompanyEvents } from "./company-events";
import { SearchDialog } from "./search-dialog";
import { SettingsDialog } from "./settings-dialog";
import { ShellActionsContext } from "./shell-actions";

/**
 * The owner app's frame: sidebar, the page, and the canvas, plus the two dialogs any part of it
 * opens (Search, Settings), and the company event stream that keeps them current. The sizes come from the layout, which reads them from cookies, so the
 * first render already has the owner's widths. `canvas` is the canvas, sized by the layout too.
 * `bar` sits above the page and `beside` next to it, inside what the canvas leaves the page: the
 * playground's bar and World panel, in a sandbox.
 */
export function AppShell({
  sidebarOpen,
  sidebarWidth,
  canvas,
  bar,
  beside,
  children,
}: {
  sidebarOpen: boolean;
  sidebarWidth: number;
  canvas: ReactNode;
  bar?: ReactNode;
  beside?: ReactNode;
  children: ReactNode;
}) {
  const [width, setWidth] = useState(sidebarWidth);
  useAppHeight();
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
        className="h-(--app-height,100dvh) min-h-0 overflow-hidden"
      >
        <AppSidebar width={width} onWidthChange={setWidth} />
        <div className="flex min-w-0 flex-1">
          <SidebarInset className="min-w-0 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
            {bar}
            {/* The page fills what the bar leaves, so a page as tall as the screen stays so. */}
            <div className="flex min-h-0 flex-1 flex-col">{children}</div>
          </SidebarInset>
          {beside}
        </div>
        {canvas}
        {/* Inside the provider: on a phone Settings goes back to the menu with its own button. */}
        <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
        <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      </SidebarProvider>
      <CompanyEvents />
    </ShellActionsContext.Provider>
  );
}
