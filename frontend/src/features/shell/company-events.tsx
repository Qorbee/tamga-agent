"use client";

import { Cancel01Icon } from "@hugeicons/core-free-icons";
import { useAui, useAuiState } from "@assistant-ui/react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useState } from "react";

import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import { openCompanyEvents, type ChatPage, type CompanyEvent } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { CHATS_KEY, chatTitle } from "@/lib/use-chats";

import { notifyAbout, onNotificationClick } from "./notify";
import { CHAT_PATH } from "./pages";

/** What each kind of note makes stale: every list and record that shows it. */
const READ_AGAIN: Record<CompanyEvent["kind"], QueryKey[]> = {
  chat: [CHATS_KEY],
  projects: [
    ["placements"],
    ["projects"],
    ["project"],
    ["project-links"],
    ["checklist-items"],
    ["shipments"],
    ["plates"],
    ["field-proposals"],
    // The firms code suggests on a project.
    ["project-parties"],
  ],
  // A file read names plates a job's board lists.
  documents: [["documents"], ["document"], ["plates"]],
  threads: [["threads"], ["thread"], ["placements"]],
  // A source's sync, the owner's own account or category, or a movement matched to a bill.
  money: [["money-accounts"], ["money-holdings"], ["money-movements"]],
  // A bill synced again may name other plates, or another day.
  invoices: [["invoice"], ["invoices"], ["invoice-totals"], ["parties"], ["plates"]],
  decisions: [["decisions"], ["decision"], ["payments"], ["decision-events"], ["decision-chain"]],
};

const CHAT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** How long the note that Tamga wrote stays on screen. */
const SHOWN_FOR = 8_000;
/** How long before a stream the browser gave up on is opened again: twice as long each time. */
const REOPEN_FIRST = 2_000;
const REOPEN_LAST = 60_000;

/**
 * The company event stream (`GET /events`): the server says what changed, never its content, and
 * what shows it is read again. When Tamga writes to the owner on its own, a note says so here,
 * and the browser's own notification too while this tab is out of sight, once the owner turned
 * that on in Settings. A stream that drops is opened again, and what changed in between is then
 * read again whole.
 */
export function CompanyEvents() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const aui = useAui();
  const router = useRouter();
  const pathname = usePathname();
  const onScreen = useAuiState((s) => s.threads.mainThreadId);
  const [wrote, setWrote] = useState<{ id: string; title: string } | null>(null);

  const open = (id: string) => {
    setWrote(null);
    aui.threads.switchToThread(id);
    router.push(CHAT_PATH);
  };

  const tell = useEffectEvent((id: string) => {
    const looking = document.visibilityState === "visible";
    // The chat is already open in front of the owner: they are reading it.
    if (looking && pathname === CHAT_PATH && onScreen === id) return;
    const chat = queryClient.getQueryData<ChatPage>(CHATS_KEY)?.items.find((item) => item.id === id);
    const title = (chat && chatTitle(chat, t, locale)) || t.chat.newChat;
    setWrote({ id, title });
    if (!looking) void notifyAbout(id, t.events.tamgaWrote, title).catch(() => undefined);
  });

  const clicked = useEffectEvent((id: string) => open(id));
  useEffect(() => onNotificationClick(clicked), []);
  // A notification tapped once no tab of the app was left opens one at `/app?chat=<id>`: the chat
  // is opened, and the address loses the id.
  const arrived = useEffectEvent((id: string) => {
    aui.threads.switchToThread(id);
    router.replace(CHAT_PATH);
  });
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("chat");
    if (id && CHAT_ID.test(id)) arrived(id);
  }, []);

  useEffect(() => {
    let source: EventSource | undefined;
    let reopen: ReturnType<typeof setTimeout> | undefined;
    let wait = REOPEN_FIRST;
    let dropped = false;
    const connect = () => {
      source = openCompanyEvents();
      source.onopen = () => {
        wait = REOPEN_FIRST;
        if (dropped) void queryClient.invalidateQueries();
        dropped = false;
      };
      source.onerror = () => {
        dropped = true;
        // The browser opens a stream that dropped again by itself, but not one refused with an
        // error status (a 502 while the API restarts): that one is opened here, later each time.
        if (source?.readyState !== EventSource.CLOSED) return;
        reopen = setTimeout(connect, wait);
        wait = Math.min(wait * 2, REOPEN_LAST);
      };
      source.onmessage = (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as CompanyEvent;
        const read = Promise.all(
          (READ_AGAIN[event.kind] ?? []).map((queryKey) => queryClient.invalidateQueries({ queryKey })),
        );
        const id = event.kind === "chat" && event.by_tamga ? event.id : null;
        // Told once the list is read again, which holds the chat's title.
        if (id) void read.then(() => tell(id));
      };
    };
    connect();
    return () => {
      clearTimeout(reopen);
      source?.close();
    };
  }, [queryClient]);

  useEffect(() => {
    if (!wrote) return;
    const timer = setTimeout(() => setWrote(null), SHOWN_FOR);
    return () => clearTimeout(timer);
  }, [wrote]);

  if (!wrote) return null;
  return (
    <div
      role="status"
      className="bg-popover text-popover-foreground border-border fixed inset-x-4 bottom-4 z-50 flex items-center gap-3 rounded-xl border p-3 shadow-lg sm:inset-x-auto sm:right-4 sm:w-96"
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{t.events.tamgaWrote}</p>
        <p className="text-muted-foreground truncate text-sm">{wrote.title}</p>
      </div>
      <Button size="sm" onClick={() => open(wrote.id)}>
        {t.events.open}
      </Button>
      <IconButton icon={Cancel01Icon} label={t.events.dismiss} onClick={() => setWrote(null)} />
    </div>
  );
}
