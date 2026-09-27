import { Rail } from "@/features/shell/rail";

/**
 * The owner app: rail, the page (the chat on the home route), and the canvas.
 *
 * `canvas` is a parallel-route slot. A card opens its full record there — through an intercepting
 * route under `@canvas` — while the chat stays mounted beside it. With nothing open, `@canvas/default`
 * renders nothing and the aside collapses (`empty:hidden`).
 */
export default function AppLayout({ children, canvas }: LayoutProps<"/app">) {
  return (
    <div className="bg-background flex h-dvh overflow-hidden">
      <Rail />
      <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">{children}</main>
      <aside className="border-border bg-card w-[min(40rem,45vw)] shrink-0 overflow-y-auto border-l empty:hidden max-lg:hidden">
        {canvas}
      </aside>
    </div>
  );
}
