"use client";

import { HelpCircleIcon, Logout01Icon, Settings01Icon, UnfoldMoreIcon, UserIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar";
import { ApiError, getMe, signOut } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { startTour } from "@/lib/tour";
import { cn } from "@/lib/utils";

import { useShellActions } from "./shell-actions";

/**
 * The foot of the sidebar: who is signed in and to which company, with Settings and Sign out. When
 * no one is signed in, or the API cannot be reached, it says so in the same place.
 */
export function AccountMenu({ floating = false }: { floating?: boolean }) {
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { openSettings } = useShellActions();
  const { setOpenMobile } = useSidebar();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const leave = useMutation({
    mutationFn: signOut,
    onSuccess: () => {
      // Nothing of the signed-out user's may stay cached for the next one.
      queryClient.clear();
      router.push("/");
    },
  });

  const user = me.data?.user;
  const name = user ? (user.name ?? user.email ?? t.account.guest) : undefined;
  const signedOut = me.error instanceof ApiError && me.error.status === 401;
  const label = name ?? (me.isPending ? "" : signedOut ? t.account.notSignedIn : t.account.offline);

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            {floating ? (
              // A phone's: the initials alone, floating at the drawer's foot.
              <button
                type="button"
                aria-label={t.account.menu}
                className={cn(
                  "pointer-events-auto flex h-12 min-w-12 items-center justify-center gap-2 rounded-full text-sm font-medium shadow-lg",
                  name ? "bg-primary text-primary-foreground" : "bg-card text-foreground px-4",
                )}
              >
                {name ? (
                  initials(name, locale)
                ) : (
                  // Signed out or offline is said, not shown as the same blank icon.
                  <>
                    <HugeiconsIcon icon={UserIcon} strokeWidth={1.75} />
                    {label ? <span className="max-w-40 truncate">{label}</span> : null}
                  </>
                )}
              </button>
            ) : (
              <SidebarMenuButton
                data-tour="account"
                tooltip={label || t.account.menu}
                aria-label={t.account.menu}
                className="data-[state=open]:bg-sidebar-accent h-12 gap-2.5 px-2 group-data-[collapsible=icon]:size-10! group-data-[collapsible=icon]:p-1!"
              >
                <Avatar className="size-8">
                  <AvatarFallback className="bg-secondary text-xs font-medium">
                    {name ? initials(name, locale) : <HugeiconsIcon icon={UserIcon} strokeWidth={1.75} />}
                  </AvatarFallback>
                </Avatar>
                <span className="flex min-w-0 flex-1 flex-col text-start">
                  <span className="truncate-fade text-sm font-medium">{label}</span>
                  {me.data ? (
                    <span className="text-muted-foreground truncate-fade text-xs">{me.data.company.name}</span>
                  ) : null}
                </span>
                <HugeiconsIcon icon={UnfoldMoreIcon} strokeWidth={1.75} className="text-muted-foreground" />
              </SidebarMenuButton>
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="min-w-56">
            {user?.email ? (
              <DropdownMenuLabel className="text-muted-foreground truncate font-normal">
                {user.email}
              </DropdownMenuLabel>
            ) : null}
            <DropdownMenuItem onSelect={openSettings}>
              <HugeiconsIcon icon={Settings01Icon} strokeWidth={1.75} />
              {t.account.settings}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                // On a phone the menu is a drawer over the page: it gives way to the tour.
                setOpenMobile(false);
                startTour();
              }}
            >
              <HugeiconsIcon icon={HelpCircleIcon} strokeWidth={1.75} />
              {t.account.tour}
            </DropdownMenuItem>
            {me.data ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem disabled={leave.isPending} onSelect={() => leave.mutate()}>
                  <HugeiconsIcon icon={Logout01Icon} strokeWidth={1.75} />
                  {t.account.signOut}
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

/** The first letters of the first two words, upper-cased by the reader's language (i → İ in Turkish). */
function initials(name: string, locale: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toLocaleUpperCase(locale);
}
