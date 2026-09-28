import type { ReactNode } from "react";

import { SidebarTrigger } from "@/components/ui/sidebar";

/**
 * The bar above a page: the sidebar button on phones (the desktop sidebar has its own), the page's
 * title, and its actions. It keeps a page's first line of content clear of the window's top edge.
 */
export function PageHeader({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 px-3 md:px-6">
      <SidebarTrigger className="md:hidden" />
      {title ? (
        <h1 className="flex min-w-0 flex-1 items-center text-sm font-medium">
          {typeof title === "string" ? <span className="truncate">{title}</span> : title}
        </h1>
      ) : null}
      {children ? <div className="ms-auto flex items-center gap-1">{children}</div> : null}
    </header>
  );
}
