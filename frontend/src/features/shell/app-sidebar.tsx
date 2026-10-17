"use client";

import { ThreadListPrimitive, useAuiState } from "@assistant-ui/react";
import { Cancel01Icon, PlusSignIcon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { MouseEvent } from "react";

import {
  ThreadListArchived,
  ThreadListItems,
  ThreadListRoot,
} from "@/components/assistant-ui/elements/thread-list.aui";
import { Lockup } from "@/components/brand/logo";
import { SidebarIcon } from "@/components/sidebar-icon";
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

// Every row, pages and past chats alike, is 38px with 15px words and a 20px icon, as the chat
// apps size theirs; collapsed, a 40px square; on a phone, 44px for a thumb.
const ROW =
  "h-9.5 gap-2.5 text-[15px] [&_svg]:size-5 max-md:h-11 max-md:text-base group-data-[collapsible=icon]:size-10! group-data-[collapsible=icon]:p-2.5!";

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
  const { isMobile, setOpen, setOpenMobile, state } = useSidebar();
  const rail = state === "collapsed" && !isMobile;
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
      {/* On a phone the header floats over the list, which fades out beneath it instead of being
          cut by a band; the foot does the same below. */}
      <SidebarHeader className="h-14 flex-row items-center gap-0 px-3 max-md:from-sidebar max-md:via-sidebar/85 max-md:pointer-events-none max-md:absolute max-md:inset-x-0 max-md:top-0 max-md:z-10 max-md:h-16 max-md:items-start max-md:bg-linear-to-b max-md:to-transparent max-md:pt-3 max-md:*:pointer-events-auto">
        {/* Open, the sidebar's button first and the logo beside it. As the sidebar folds the
            button slides away with it and the word rolls into the seal, which stays in the
            rail, in line with the icons below, as the way back: under the pointer it turns into
            the sidebar's button, and a tap opens it. */}
        <span className="flex w-10 shrink-0 overflow-hidden transition-[width,opacity] duration-200 ease-linear group-data-[collapsible=icon]:w-0 group-data-[collapsible=icon]:opacity-0 max-md:hidden">
          <SidebarTrigger className="text-muted-foreground size-9 shrink-0 [&_svg]:size-5" tabIndex={rail ? -1 : undefined} />
        </span>
        <Link
          href={CHAT_PATH}
          aria-label={rail ? t.shell.openSidebar : t.meta.title}
          onClick={(event) => {
            if (!rail) return picked();
            event.preventDefault();
            setOpen(true);
          }}
          className="group/brand focus-visible:ring-ring/50 relative flex min-w-0 items-center overflow-hidden rounded-lg outline-none focus-visible:ring-[3px] max-md:ms-1"
        >
          <span className={cn("transition-opacity", rail && "group-hover/brand:opacity-0 group-focus-visible/brand:opacity-0")}>
            <Lockup open={!rail} height={32} label={t.meta.title} intro />
          </span>
          {rail ? (
            <SidebarIcon
              open={false}
              className="text-muted-foreground absolute inset-0 m-auto opacity-0 transition-opacity group-hover/brand:opacity-100 group-focus-visible/brand:opacity-100"
            />
          ) : null}
        </Link>
        {isMobile ? (
          <button
            type="button"
            aria-label={t.search.title}
            onClick={() => {
              picked();
              openSearch();
            }}
            className="text-muted-foreground ms-auto flex size-10 items-center justify-center"
          >
            <HugeiconsIcon icon={Search01Icon} strokeWidth={1.75} className="size-5" />
          </button>
        ) : null}
        {isMobile ? (
          // A plain button, not an IconButton: the drawer focuses it on opening, and a tooltip
          // would show on a phone with no pointer to move off it.
          <button
            type="button"
            aria-label={t.shell.closeSidebar}
            onClick={() => setOpenMobile(false)}
            className="text-muted-foreground flex size-10 items-center justify-center"
          >
            <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} className="size-5" />
          </button>
        ) : null}
      </SidebarHeader>

      {/* On a desktop, Search is a field under the name, as the chat apps keep it; collapsed to
          icons, it is a row like the others; a phone has its icon in the header. */}
      <div className="px-2 pb-2 max-md:hidden group-data-[collapsible=icon]:hidden">
        <button
          type="button"
          onClick={openSearch}
          className="border-sidebar-border bg-background text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex h-9 w-full items-center gap-2 rounded-lg border px-2.5 text-sm outline-none focus-visible:ring-[3px]"
        >
          <HugeiconsIcon icon={Search01Icon} strokeWidth={1.75} className="size-4" />
          <span className="flex-1 text-start">{t.search.title}</span>
        </button>
      </div>

      <SidebarContent className="max-md:pt-16 max-md:pb-28">
        <SidebarGroup className="pt-0">
          <SidebarMenu className="gap-0.5">
            <SidebarMenuItem className="max-md:hidden">
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
            <SidebarMenuItem className="hidden group-data-[collapsible=icon]:block">
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
                <SidebarMenuItem key={page.href} data-tour={`nav-${page.label}`}>
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
          data-tour="chats"
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

      {isMobile ? (
        // A phone's foot floats over the list, as the chat apps keep it: who is signed in, and a
        // new chat a thumb away.
        <div className="from-sidebar via-sidebar/80 pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between bg-linear-to-t to-transparent px-4 pt-6 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <AccountMenu floating />
          <ThreadListPrimitive.New asChild>
            <button
              type="button"
              onClick={openChat}
              className="bg-foreground text-background pointer-events-auto flex h-12 shrink-0 items-center gap-2 rounded-full px-5 text-base font-medium whitespace-nowrap active:opacity-90"
            >
              <HugeiconsIcon icon={PlusSignIcon} strokeWidth={2} className="size-5" />
              {t.chat.newChat}
            </button>
          </ThreadListPrimitive.New>
        </div>
      ) : (
        <SidebarFooter>
          <AccountMenu />
        </SidebarFooter>
      )}

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
