import { cookies } from "next/headers";
import { Suspense } from "react";

import { TranslateProvider } from "@/components/translated";
import { ChatRuntime } from "@/features/assistant/chat-runtime";
import { PlaygroundBar, PlaygroundProvider, WorldPanel } from "@/features/playground/playground";
import { RequireSession } from "@/features/session/require-session";
import { AppShell } from "@/features/shell/app-shell";
import { CANVAS_SIZE, SIDEBAR_OPEN_COOKIE, SIDEBAR_SIZE, readSize } from "@/lib/panel-sizes";
import { TRANSLATE_COOKIE } from "@/lib/translate";

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
        <TranslateProvider initial={jar.get(TRANSLATE_COOKIE)?.value === "on"}>
        <PlaygroundProvider>
        <AppShell
          sidebarOpen={jar.get(SIDEBAR_OPEN_COOKIE)?.value !== "false"}
          sidebarWidth={readSize(SIDEBAR_SIZE, jar.get(SIDEBAR_SIZE.cookie)?.value)}
          canvas={
            <Suspense fallback={null}>
              <CanvasOutlet initialWidth={readSize(CANVAS_SIZE, jar.get(CANVAS_SIZE.cookie)?.value)} />
            </Suspense>
          }
          bar={<PlaygroundBar />}
          beside={
            <Suspense fallback={null}>
              <WorldPanel />
            </Suspense>
          }
        >
          {children}
        </AppShell>
        </PlaygroundProvider>
        </TranslateProvider>
      </ChatRuntime>
    </RequireSession>
  );
}
