import type { ReactNode } from "react";

import { SidebarTrigger } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

/**
 * A page beside the chat (Inbox, Projects, Upload, …): its title and content in one column centred
 * in the room the sidebar and canvas leave, as the chat's own column is. `wide` is for a board that
 * needs the room. The chat keeps `PageHeader`.
 */
export function Page({
  title,
  actions,
  wide = false,
  children,
}: {
  title: ReactNode;
  actions?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-full flex-col">
      <div className="flex h-14 shrink-0 items-center px-3 md:hidden">
        <SidebarTrigger />
      </div>
      <div
        className={cn(
          "mx-auto flex w-full min-w-0 flex-1 flex-col px-4 pb-12 md:px-8 md:pt-12",
          wide ? "max-w-6xl" : "max-w-3xl",
        )}
      >
        <header className="mb-8 flex min-h-9 items-center gap-3">
          <h1 className="min-w-0 flex-1 truncate text-2xl font-medium tracking-tight">{title}</h1>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
        {children}
      </div>
    </div>
  );
}
