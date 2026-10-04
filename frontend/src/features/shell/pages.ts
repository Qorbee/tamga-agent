import {
  Folder01Icon,
  InboxIcon,
  Invoice03Icon,
  TransactionHistoryIcon,
  Upload01Icon,
  Wallet01Icon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";

import type { Dictionary } from "@/lib/i18n";

/** The chat, which the logo, New chat and every past chat open. */
export const CHAT_PATH = "/app";

export type Page = { href: string; label: keyof Dictionary["nav"]; icon: IconSvgElement };

/** The owner app's pages besides the chat, in the sidebar's order; Search lists them too. */
export const pages: readonly Page[] = [
  { href: "/app/projects", label: "projects", icon: Folder01Icon },
  { href: "/app/payment-run", label: "paymentRun", icon: Wallet01Icon },
  { href: "/app/books", label: "books", icon: Invoice03Icon },
  { href: "/app/inbox", label: "inbox", icon: InboxIcon },
  { href: "/app/upload", label: "upload", icon: Upload01Icon },
  { href: "/app/log", label: "log", icon: TransactionHistoryIcon },
];
