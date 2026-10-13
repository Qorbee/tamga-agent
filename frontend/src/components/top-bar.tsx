"use client";

import { SidebarLeftIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * A phone's bar over a page or a chat, as a chat app keeps it: no solid band, only the page's own
 * colour fading out beneath the buttons, so what scrolls under it stays in view and the screen
 * keeps its height for the content. The sidebar button floats on its own, edged as the sidebar's
 * rows are. Shared by every page, the chat and the settings, so the way back to the menu is the
 * same button everywhere.
 */
export function TopBar({ title, children, className }: { title?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "from-background pointer-events-none flex h-16 shrink-0 items-center gap-2 bg-linear-to-b from-60% to-transparent px-3 pb-2",
        className,
      )}
    >
      <FloatingTrigger />
      {title ? <div className="pointer-events-auto min-w-0 flex-1 truncate text-base font-medium">{title}</div> : <span className="flex-1" />}
      {children ? <div className="pointer-events-auto flex shrink-0 items-center gap-1">{children}</div> : null}
    </div>
  );
}

const FLOATING = "bg-background/90 pointer-events-auto size-9 rounded-full shadow-[0_0_0_1px_var(--sidebar-border)] shadow-xs";

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
      <HugeiconsIcon icon={SidebarLeftIcon} strokeWidth={1.75} />
      <span className="sr-only">{t.shell.toggleSidebar}</span>
    </Button>
  );
}
