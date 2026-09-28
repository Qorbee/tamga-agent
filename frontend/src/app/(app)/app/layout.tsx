import { cookies } from "next/headers";

import { ChatRuntime } from "@/features/assistant/chat-runtime";
import { AppShell } from "@/features/shell/app-shell";
import { CANVAS_SIZE, SIDEBAR_OPEN_COOKIE, SIDEBAR_SIZE, readSize } from "@/lib/panel-sizes";

/**
 * The owner app: sidebar, the page (the chat on the home route), and the canvas.
 *
 * The chat runtime wraps the whole app, so the sidebar lists the same chats the chat page shows.
 * `canvas` is a parallel-route slot: a card opens its full record there — through an intercepting
 * route under `@canvas` that renders a `CanvasPanel` — while the chat stays mounted beside it.
 */
export default async function AppLayout({ children, canvas }: LayoutProps<"/app">) {
  const jar = await cookies();

  return (
    <ChatRuntime>
      <AppShell
        sidebarOpen={jar.get(SIDEBAR_OPEN_COOKIE)?.value !== "false"}
        sidebarWidth={readSize(SIDEBAR_SIZE, jar.get(SIDEBAR_SIZE.cookie)?.value)}
        canvasWidth={readSize(CANVAS_SIZE, jar.get(CANVAS_SIZE.cookie)?.value)}
        canvas={canvas}
      >
        {children}
      </AppShell>
    </ChatRuntime>
  );
}
