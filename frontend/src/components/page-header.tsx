import type { ReactNode } from "react";

import { TopBar } from "@/components/top-bar";

/**
 * The bar above the chat: its title and actions. On a phone it is the see-through `TopBar`, laid
 * over the chat so the messages scroll under it; on a wider screen, a band of its own (the desktop
 * sidebar has its own button).
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
      <header className="flex h-14 shrink-0 items-center gap-2 px-6 max-md:hidden">
        {named ? <div className="min-w-0 flex-1">{named}</div> : null}
        {children ? <div className="ms-auto flex items-center gap-1">{children}</div> : null}
      </header>
    </>
  );
}
