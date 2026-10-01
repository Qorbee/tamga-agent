"use client";

import { ThreadListPrimitive, useAuiState } from "@assistant-ui/react";
import { PlusSignIcon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { MouseEvent } from "react";

import {
  ThreadListArchived,
  ThreadListItems,
  ThreadListRoot,
} from "@/components/assistant-ui/elements/thread-list.aui";
import { BrandMark } from "@/components/brand-mark";
import { ResizeHandle } from "@/components/resize-handle";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/lib/i18n/client";
import { SIDEBAR_SIZE, saveSize } from "@/lib/panel-sizes";
import { cn } from "@/lib/utils";

import { AccountMenu } from "./account-menu";
import { CHAT_PATH, pages } from "./pages";
import { useShellActions } from "./shell-actions";

// Every row, pages and past chats alike, is 36px with an 18px icon; collapsed, a 40px square.
const ROW =
  "h-9 [&_svg]:size-4.5 group-data-[collapsible=icon]:size-10! group-data-[collapsible=icon]:p-[11px]!";

/**
 * The owner app's sidebar: New chat and Search, the pages, past chats, and the account at its foot.
 * It collapses to icons (the button at the top, or Ctrl/⌘ B), its right edge drags to resize, and on
 * a phone it is a drawer.
 */
export function AppSidebar({
  width,
  onWidthChange,
}: {
  width: number;
  onWidthChange: (width: number) => void;
}) {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const { isMobile, setOpen, setOpenMobile } = useSidebar();
  const { openSearch } = useShellActions();
  const hasThreads = useAuiState((s) => s.threads.threadIds.length > 0);
  const chatOpen = pathname === CHAT_PATH;

  // A phone's drawer covers the page, so it closes once something is picked.
  const picked = () => {
    if (isMobile) setOpenMobile(false);
  };
  const openChat = () => {
    picked();
    if (!chatOpen) router.push(CHAT_PATH);
  };
  // A past chat is picked from any page; the runtime switches to it, this shows it.
  const onThreadClick = (event: MouseEvent<HTMLDivElement>) => {
    if ((event.target as Element).closest("[data-slot=aui_thread-list-item-trigger]")) openChat();
  };

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-14 flex-row items-center justify-between px-3 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-2">
        <Link
          href={CHAT_PATH}
          onClick={picked}
          className="focus-visible:ring-ring/50 flex min-w-0 items-center gap-2 rounded-lg outline-none focus-visible:ring-[3px] group-data-[collapsible=icon]:hidden"
        >
          <BrandMark className="size-7" />
          <span className="truncate text-base font-medium tracking-tight">{t.meta.title}</span>
        </Link>
        <SidebarTrigger className="text-muted-foreground max-md:hidden" />
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup className="pt-0">
          <SidebarMenu className="gap-0.5">
            <SidebarMenuItem>
              <ThreadListPrimitive.New asChild>
                {/* The primitive marks itself active on an empty chat; a button to start one is not
                    a place to be, so it is never highlighted. */}
                <SidebarMenuButton
                  tooltip={t.chat.newChat}
                  onClick={openChat}
                  className={cn(ROW, "data-active:hover:bg-sidebar-accent data-active:bg-transparent data-active:font-normal")}
                >
                  <HugeiconsIcon icon={PlusSignIcon} strokeWidth={1.75} />
                  <span>{t.chat.newChat}</span>
                </SidebarMenuButton>
              </ThreadListPrimitive.New>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                tooltip={t.search.title}
                onClick={() => {
                  picked();
                  openSearch();
                }}
                className={ROW}
              >
                <HugeiconsIcon icon={Search01Icon} strokeWidth={1.75} />
                <span>{t.search.title}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            {pages.map((page) => {
              const active = pathname.startsWith(page.href);
              const label = t.nav[page.label];
              return (
                <SidebarMenuItem key={page.href}>
                  <SidebarMenuButton asChild isActive={active} tooltip={label} className={ROW}>
                    <Link href={page.href} onClick={picked} aria-current={active ? "page" : undefined}>
                      <HugeiconsIcon icon={page.icon} strokeWidth={1.75} />
                      <span>{label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </SidebarMenu>
        </SidebarGroup>

        {/* Always there, so the sidebar keeps its shape before the first chat. */}
        <SidebarGroup
          data-chat-open={chatOpen || undefined}
          className="group-data-[collapsible=icon]:hidden"
        >
          <SidebarGroupLabel>{t.shell.recents}</SidebarGroupLabel>
          {!hasThreads && (
            <p className="text-muted-foreground px-3 py-1.5 text-sm">{t.shell.recentsEmpty}</p>
          )}
          <ThreadListRoot onClick={onThreadClick}>
            {hasThreads && <ThreadListItems />}
            <ThreadListArchived />
          </ThreadListRoot>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <AccountMenu />
      </SidebarFooter>

      <ResizeHandle
        label={t.shell.resizeSidebar}
        size={SIDEBAR_SIZE}
        width={width}
        edge="right"
        onResize={onWidthChange}
        onResizeEnd={(settled) => saveSize(SIDEBAR_SIZE, settled)}
        onCollapse={() => setOpen(false)}
        className="max-md:hidden group-data-[collapsible=icon]:hidden"
      />
    </Sidebar>
  );
}
