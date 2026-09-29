import { cookies } from "next/headers";
import { Suspense } from "react";

import { ChatRuntime } from "@/features/assistant/chat-runtime";
import { RequireSession } from "@/features/session/require-session";
import { AppShell } from "@/features/shell/app-shell";
import { CANVAS_SIZE, SIDEBAR_OPEN_COOKIE, SIDEBAR_SIZE, readSize } from "@/lib/panel-sizes";

import { CanvasOutlet } from "./_canvas/canvas-outlet";

/**
 * The owner app: sidebar, the page (the chat on the home route), and the canvas.
 *
 * The chat runtime wraps the whole app, so the sidebar lists the same chats the chat page shows.
 * The canvas shows the record the address names (`?view=`, `lib/canvas-view.ts`) beside any page, so
 * the page stays mounted under it, through a reload too.
 */
export default async function AppLayout({ children }: LayoutProps<"/app">) {
  const jar = await cookies();

  return (
    <RequireSession>
      <ChatRuntime>
        <AppShell
          sidebarOpen={jar.get(SIDEBAR_OPEN_COOKIE)?.value !== "false"}
          sidebarWidth={readSize(SIDEBAR_SIZE, jar.get(SIDEBAR_SIZE.cookie)?.value)}
          canvas={
            <Suspense fallback={null}>
              <CanvasOutlet initialWidth={readSize(CANVAS_SIZE, jar.get(CANVAS_SIZE.cookie)?.value)} />
            </Suspense>
          }
        >
          {children}
        </AppShell>
      </ChatRuntime>
    </RequireSession>
  );
}
