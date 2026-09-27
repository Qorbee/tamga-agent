"use client";

import {
  BubbleChatIcon,
  Folder01Icon,
  InboxIcon,
  TransactionHistoryIcon,
  Wallet01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { BrandMark } from "@/components/brand-mark";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

import { ApiStatus } from "./api-status";

type Item = { href: string; label: keyof Dictionary["nav"]; icon: IconSvgElement };

const items: readonly Item[] = [
  { href: "/app", label: "assistant", icon: BubbleChatIcon },
  { href: "/app/projects", label: "projects", icon: Folder01Icon },
  { href: "/app/payment-run", label: "paymentRun", icon: Wallet01Icon },
  { href: "/app/inbox", label: "inbox", icon: InboxIcon },
  { href: "/app/log", label: "log", icon: TransactionHistoryIcon },
];

function isActive(pathname: string, href: string) {
  return href === "/app" ? pathname === href : pathname.startsWith(href);
}

/** The five-item left rail: Assistant is home; Rules and Settings will sit under the avatar menu. */
export function Rail() {
  const { t } = useI18n();
  const pathname = usePathname();

  return (
    <nav
      aria-label={t.nav.label}
      className="bg-sidebar border-sidebar-border flex w-16 shrink-0 flex-col items-center border-r py-4"
    >
      <Link href="/app" className="mb-6 rounded-[10px] focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none">
        <BrandMark className="size-9" title="Tamga" />
      </Link>

      <ul className="flex flex-col items-center gap-1.5">
        {items.map((item) => {
          const active = isActive(pathname, item.href);
          const label = t.nav[item.label];
          return (
            <li key={item.href}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Link
                    href={item.href}
                    aria-label={label}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "text-muted-foreground grid size-10 place-items-center rounded-xl transition-colors outline-none",
                      "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                      "focus-visible:ring-[3px] focus-visible:ring-ring/50",
                      active && "bg-card text-primary shadow-sm ring-1 ring-sidebar-border",
                    )}
                  >
                    <HugeiconsIcon icon={item.icon} strokeWidth={1.75} className="size-5" />
                  </Link>
                </TooltipTrigger>
                <TooltipContent side="right">{label}</TooltipContent>
              </Tooltip>
            </li>
          );
        })}
      </ul>

      <div className="mt-auto">
        <ApiStatus />
      </div>
    </nav>
  );
}
