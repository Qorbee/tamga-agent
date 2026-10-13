"use client";

import { useAui, useAuiState } from "@assistant-ui/react";
import {
  BubbleChatIcon,
  PlusSignIcon,
  Search01Icon,
  Settings01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

import { CHAT_PATH, pages } from "./pages";
import { useShellActions } from "./shell-actions";

type Result = { key: string; label: string; icon: IconSvgElement; run: () => void };
type Section = { title: string; results: Result[] };

const tabs = ["all", "chats", "pages"] as const;
type Tab = (typeof tabs)[number];

/** With nothing typed, the most recent chats; with a query, every chat that matches. */
const RECENT_CHATS = 6;

/**
 * Search, opened from the sidebar or with Ctrl/⌘ K: one field over chats, pages and actions,
 * driven from the keyboard. Projects, people and documents join as tabs when the API serves them.
 */
export function SearchDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        // On a phone it is a page of its own, the field at its top, so the keyboard moves nothing.
        className="top-[12vh] translate-y-0 gap-0 overflow-hidden p-0 max-sm:inset-0 max-sm:h-(--app-height,100dvh) max-sm:max-w-none max-sm:translate-x-0 max-sm:rounded-none max-sm:ring-0 sm:max-w-xl"
      >
        <DialogTitle className="sr-only">{t.search.title}</DialogTitle>
        {/* Mounted per opening, so each search starts empty. */}
        {open ? <SearchPanel onDone={() => onOpenChange(false)} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function SearchPanel({ onDone }: { onDone: () => void }) {
  const { t, locale } = useI18n();
  const aui = useAui();
  const router = useRouter();
  const { openSettings } = useShellActions();
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>("all");
  const [active, setActive] = useState(0);
  const listId = useId();
  const field = useRef<HTMLInputElement>(null);

  const needle = query.trim().toLocaleLowerCase(locale);
  const matches = (label: string) => !needle || label.toLocaleLowerCase(locale).includes(needle);
  const openChat = () => router.push(CHAT_PATH);

  const byId = new Map(threadItems.map((item) => [item.id, item]));
  const chats: Result[] = threadIds
    .flatMap((id) => {
      const item = byId.get(id);
      return item ? [{ id, label: item.title ?? t.chat.newChat }] : [];
    })
    .filter((chat) => matches(chat.label))
    .slice(0, needle ? undefined : RECENT_CHATS)
    .map((chat) => ({
      key: `chat:${chat.id}`,
      label: chat.label,
      icon: BubbleChatIcon,
      run: () => {
        aui.threads.switchToThread(chat.id);
        openChat();
      },
    }));
  const pageResults: Result[] = pages
    .map((page) => ({
      key: `page:${page.href}`,
      label: t.nav[page.label],
      icon: page.icon,
      run: () => router.push(page.href),
    }))
    .filter((page) => matches(page.label));
  const actions: Result[] = [
    {
      key: "action:new-chat",
      label: t.chat.newChat,
      icon: PlusSignIcon,
      run: () => {
        aui.threads.switchToNewThread();
        openChat();
      },
    },
    { key: "action:settings", label: t.account.settings, icon: Settings01Icon, run: openSettings },
  ].filter((action) => matches(action.label));

  const sections: Section[] = [
    tab !== "pages" ? { title: needle ? t.search.chats : t.search.recentChats, results: chats } : null,
    tab !== "chats" ? { title: t.search.pages, results: pageResults } : null,
    tab === "all" ? { title: t.search.actions, results: actions } : null,
  ].filter((section): section is Section => section !== null && section.results.length > 0);
  const flat = sections.flatMap((section) => section.results);
  const positions = new Map(flat.map((result, position) => [result.key, position]));
  const current = Math.min(active, Math.max(flat.length - 1, 0));
  const optionId = (position: number) => `${listId}-${position}`;

  // Keeps the highlighted result in view as the arrow keys move past the list's edge.
  useEffect(() => {
    document.getElementById(`${listId}-${current}`)?.scrollIntoView({ block: "nearest" });
  }, [listId, current]);

  const choose = (result: Result | undefined) => {
    if (!result) return;
    onDone();
    result.run();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (flat.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + flat.length) % flat.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(flat[current]);
    }
  };

  return (
    <div className="flex flex-col max-sm:h-full">
      <div className="border-border flex flex-col gap-3 border-b px-4 pt-4 pb-3">
        <div className="flex items-center gap-3">
          <HugeiconsIcon icon={Search01Icon} strokeWidth={1.75} className="text-muted-foreground size-5 shrink-0" />
          <input
            ref={field}
            autoFocus
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={flat.length > 0 ? optionId(current) : undefined}
            aria-label={t.search.title}
            placeholder={t.search.placeholder}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            className="placeholder:text-muted-foreground min-w-0 flex-1 bg-transparent text-base outline-none"
          />
          <button type="button" onClick={onDone} className="text-muted-foreground hover:text-foreground text-sm sm:hidden">
            {t.search.cancel}
          </button>
        </div>
        <div role="tablist" aria-label={t.search.title} className="flex gap-1 overflow-x-auto">
          {tabs.map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              onClick={() => {
                setTab(value);
                setActive(0);
                // Typing and the arrow keys are the field's, so a tab hands focus straight back.
                field.current?.focus();
              }}
              className="text-muted-foreground hover:text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground focus-visible:ring-ring/50 rounded-lg px-2.5 py-1 text-sm outline-none focus-visible:ring-[3px]"
            >
              {t.search.tabs[value]}
            </button>
          ))}
        </div>
      </div>

      <div id={listId} role="listbox" aria-label={t.search.title} className="max-h-[min(60vh,34rem)] overflow-y-auto p-2 max-sm:max-h-none max-sm:min-h-0 max-sm:flex-1">
        {sections.length === 0 ? (
          <p className="text-muted-foreground px-3 py-8 text-center text-sm">{t.search.noResults}</p>
        ) : (
          sections.map((section) => (
            <div key={section.title} role="group" aria-label={section.title} className="pb-1">
              <div className="text-muted-foreground px-3 pt-2 pb-1 text-xs font-medium">{section.title}</div>
              {section.results.map((result) => {
                const position = positions.get(result.key) ?? 0;
                return (
                  <div
                    key={result.key}
                    id={optionId(position)}
                    role="option"
                    aria-selected={position === current}
                    // The field keeps focus; the pointer only moves the highlight, as the arrows do.
                    onMouseMove={() => setActive(position)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => choose(result)}
                    className="aria-selected:bg-accent aria-selected:text-accent-foreground flex h-10 cursor-pointer items-center gap-3 rounded-lg px-3 text-sm max-sm:h-12 max-sm:text-base max-sm:aria-selected:bg-transparent max-sm:active:bg-accent"
                  >
                    <HugeiconsIcon icon={result.icon} strokeWidth={1.75} className="text-muted-foreground size-4.5 shrink-0" />
                    <span className="truncate-fade min-w-0 flex-1">{result.label}</span>
                    {position === current ? <Key className="max-sm:hidden">↵</Key> : null}
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      {/* Keys a phone has none of. */}
      <div className="border-border text-muted-foreground flex gap-4 border-t px-4 py-2.5 text-xs max-sm:hidden">
        <span className="flex items-center gap-1.5">
          <Key>Esc</Key> {t.search.close}
        </span>
        <span className="flex items-center gap-1.5">
          <Key>↑↓</Key> {t.search.move}
        </span>
        <span className="flex items-center gap-1.5">
          <Key>↵</Key> {t.search.open}
        </span>
      </div>
    </div>
  );
}

function Key({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd className={cn("border-border bg-muted text-muted-foreground rounded-md border px-1.5 font-sans text-xs leading-5", className)}>
      {children}
    </kbd>
  );
}
