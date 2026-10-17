import type { ReactNode } from "react";

import { TabTitle } from "@/components/tab-title";
import { TopBar } from "@/components/top-bar";

/**
 * A page beside the chat (Inbox, Projects, Upload, …): its title and content in one column centred
 * in the room the sidebar and canvas leave, as the chat's own column is. On a phone the title sits
 * in the see-through `TopBar` with the sidebar button, which stays in reach while the page scrolls
 * under it. The chat keeps `PageHeader`.
 */
export function Page({
  title,
  actions,
  tour,
  children,
}: {
  // A string also names the browser tab.
  title: ReactNode;
  actions?: ReactNode;
  // What the tour calls the page, to light it.
  tour?: string;
  children: ReactNode;
}) {
  return (
    // Not shrunk to the scrolling area's height: the bar sticks only for as long as this is tall.
    <div className="flex min-h-full shrink-0 flex-col">
      {typeof title === "string" ? <TabTitle subject={title} /> : null}
      <TopBar title={<h1 className="truncate">{title}</h1>} className="sticky top-0 z-20 md:hidden">
        {actions}
      </TopBar>
      <div data-tour={tour} className="mx-auto flex w-full max-w-3xl min-w-0 flex-1 flex-col px-4 pt-2 pb-12 md:px-8 md:pt-12">
        <header className="mb-8 flex min-h-9 items-center gap-3 max-md:hidden">
          <h1 className="min-w-0 flex-1 truncate text-2xl font-medium tracking-tight">{title}</h1>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
        {children}
      </div>
    </div>
  );
}
