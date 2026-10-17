"use client";

import type { ReactNode } from "react";

import { SidebarIcon } from "@/components/sidebar-icon";
import { Button } from "@/components/ui/button";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * A phone's bar over a page or a chat, as a chat app keeps it: no solid band, only the page's own
 * colour fading out beneath the buttons, so what scrolls under it stays in view and the screen
 * keeps its height for the content. The sidebar button is the icon alone. Shared by every page, the chat and the settings, so the way back to the menu is the
 * same button everywhere.
 */
export function TopBar({ title, children, className }: { title?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "from-background pointer-events-none flex h-16 shrink-0 items-center gap-2 bg-linear-to-b from-45% to-transparent px-3 pb-2",
        className,
      )}
    >
      <FloatingTrigger />
      {title ? <div className="pointer-events-auto min-w-0 flex-1 truncate text-base font-medium">{title}</div> : <span className="flex-1" />}
      {children ? <div className="pointer-events-auto flex shrink-0 items-center gap-1">{children}</div> : null}
    </div>
  );
}

// Only the icon: the page's fade beneath it is all the ground it needs.
const FLOATING = "pointer-events-auto size-9 hover:bg-transparent aria-expanded:bg-transparent dark:hover:bg-transparent";

/** The sidebar button as it floats over a page on a phone. */
export function FloatingTrigger() {
  return <SidebarTrigger className={FLOATING} />;
}

/** The same button where it must open the menu, never close it: Settings, opened from the menu
 * itself, which is then still open behind it. */
export function BackToMenu({ onLeave }: { onLeave: () => void }) {
  const { t } = useI18n();
  const { setOpenMobile } = useSidebar();
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className={FLOATING}
      onClick={() => {
        onLeave();
        setOpenMobile(true);
      }}
    >
      <SidebarIcon open={false} />
      <span className="sr-only">{t.shell.openSidebar}</span>
    </Button>
  );
}
