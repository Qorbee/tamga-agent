"use client";

import { ThreadListPrimitive, useAuiState } from "@assistant-ui/react";
import {
  Folder01Icon,
  InboxIcon,
  PlusSignIcon,
  TransactionHistoryIcon,
  Wallet01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type MouseEvent } from "react";

import {
  ThreadListItems,
  ThreadListRoot,
  ThreadListSearch,
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
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { SIDEBAR_SIZE, saveSize } from "@/lib/panel-sizes";
import { cn } from "@/lib/utils";

import { AccountMenu } from "./account-menu";

const CHAT_PATH = "/app";

// Every row, pages and past chats alike, is 36px with an 18px icon; collapsed, a 40px square.
const ROW =
  "h-9 [&_svg]:size-4.5 group-data-[collapsible=icon]:size-10! group-data-[collapsible=icon]:p-[11px]!";

type Page = { href: string; label: keyof Dictionary["nav"]; icon: IconSvgElement };

const pages: readonly Page[] = [
  { href: "/app/projects", label: "projects", icon: Folder01Icon },
  { href: "/app/payment-run", label: "paymentRun", icon: Wallet01Icon },
  { href: "/app/inbox", label: "inbox", icon: InboxIcon },
  { href: "/app/log", label: "log", icon: TransactionHistoryIcon },
];

/**
 * The owner app's sidebar: a new chat, the pages, past chats, and the account at its foot. It
 * collapses to icons (the button at the top, or Ctrl/⌘ B), its right edge drags to resize, and on a
 * phone it is a drawer.
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
  const [search, setSearch] = useState("");
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

        {hasThreads ? (
          <SidebarGroup
            data-chat-open={chatOpen || undefined}
            className="group-data-[collapsible=icon]:hidden"
          >
            <SidebarGroupLabel>{t.shell.recents}</SidebarGroupLabel>
            <ThreadListSearch value={search} onValueChange={setSearch} />
            <ThreadListRoot onClick={onThreadClick}>
              <ThreadListItems searchQuery={search} />
            </ThreadListRoot>
          </SidebarGroup>
        ) : null}
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
