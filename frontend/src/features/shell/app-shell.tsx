"use client";

import { useState, type CSSProperties, type ReactNode } from "react";

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";

import { AppSidebar } from "./app-sidebar";
import { CanvasFrame } from "./canvas";

/**
 * The owner app's frame: sidebar, the page, and the canvas. The sizes come from the layout, which
 * reads them from cookies, so the first render already has the owner's widths.
 */
export function AppShell({
  sidebarOpen,
  sidebarWidth,
  canvasWidth,
  canvas,
  children,
}: {
  sidebarOpen: boolean;
  sidebarWidth: number;
  canvasWidth: number;
  canvas: ReactNode;
  children: ReactNode;
}) {
  const [width, setWidth] = useState(sidebarWidth);

  return (
    <SidebarProvider
      defaultOpen={sidebarOpen}
      style={{ "--sidebar-width": `${width}px`, "--sidebar-width-icon": "3.5rem" } as CSSProperties}
      className="h-dvh min-h-0 overflow-hidden"
    >
      <AppSidebar width={width} onWidthChange={setWidth} />
      <SidebarInset className="min-w-0 overflow-y-auto">{children}</SidebarInset>
      <CanvasFrame initialWidth={canvasWidth}>{canvas}</CanvasFrame>
    </SidebarProvider>
  );
}
