import type { ReactNode } from "react";

import { TopBar } from "@/components/top-bar";

/**
 * The bar above the chat: its title and actions, laid over the chat so the messages scroll under
 * it, with only a short fade beneath: on a phone the see-through `TopBar`; on a wider screen the
 * same without the sidebar's button, which the desktop sidebar has.
 */
export function PageHeader({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  const named = title ? (
    <h1 className="flex min-w-0 items-center text-sm font-medium">{typeof title === "string" ? <span className="truncate">{title}</span> : title}</h1>
  ) : undefined;
  return (
    <>
      <TopBar title={named} className="absolute inset-x-0 top-0 z-20 md:hidden">
        {children}
      </TopBar>
      <header className="from-background pointer-events-none absolute inset-x-0 top-0 z-20 flex h-16 items-start gap-2 bg-linear-to-b from-45% to-transparent px-6 pt-4 max-md:hidden">
        {named ? <div className="pointer-events-auto min-w-0 flex-1">{named}</div> : null}
        {children ? <div className="pointer-events-auto ms-auto flex items-center gap-1">{children}</div> : null}
      </header>
    </>
  );
}
