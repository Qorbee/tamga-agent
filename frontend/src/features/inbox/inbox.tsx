"use client";

import { Attachment01Icon, Cancel01Icon, Search01Icon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { IconButton } from "@/components/icon-button";
import { PersonAvatar } from "@/components/person-avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { decideProposal, getMe, listProposals, listThreads, type FieldProposal } from "@/lib/api";
import { formatDate, formatDays, formatListDate, todayIso } from "@/lib/format";
import { conversationName, ownSnippet } from "@/lib/mail-text";
import type { Locale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { usePagedList } from "@/lib/use-paged-list";

import { Waiting } from "./waiting";

/**
 * What needs the owner: the conversations Tamga placed in projects and the changes it proposes,
 * shown only while there are some, then the mail as conversations. A conversation opens in the
 * canvas.
 */
export function Inbox() {
  return (
    <div className="grid gap-10">
      <Waiting />
      <OpenProposals />
      <Mail />
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-3">
      <h2 className="text-sm font-medium">{title}</h2>
      {children}
    </section>
  );
}

function OpenProposals() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const proposals = useQuery({
    queryKey: ["field-proposals", { status: "open" }],
    queryFn: () => listProposals({ status: ["open"] }),
  });
  const decide = useMutation({
    mutationFn: ({ proposal, status }: { proposal: FieldProposal; status: "accepted" | "dismissed" }) =>
      decideProposal(proposal.id, status),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["field-proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["shipments"] });
      void queryClient.invalidateQueries({ queryKey: ["plates"] });
    },
  });

  if (!proposals.isError && !proposals.data?.items.length) return null;
  return (
    <Section title={t.inbox.proposals}>
      {proposals.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : (
        <ul className="divide-border border-border divide-y rounded-2xl border">
          {proposals.data.items.map((proposal) => (
            <li key={proposal.id} className="flex items-center gap-1 py-3 ps-4 pe-3">
              <div className="min-w-0 flex-1">
                <p className="text-muted-foreground text-xs">
                  {t.inbox.field(t.inbox.targets[proposal.target], t.inbox.fields[proposal.field] ?? proposal.field)}
                </p>
                <p className="text-sm break-words">{display(proposal.field, proposal.value, locale)}</p>
              </div>
              <IconButton
                icon={Tick02Icon}
                label={t.inbox.accept}
                disabled={decide.isPending}
                onClick={() => decide.mutate({ proposal, status: "accepted" })}
              />
              <IconButton
                icon={Cancel01Icon}
                label={t.inbox.dismiss}
                className="text-muted-foreground"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ proposal, status: "dismissed" })}
              />
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

const DAYS = new Set(["starts_on", "ends_on"]);

/** A proposed value as the owner reads it: a day in their own format, text as it is. */
function display(field: string, value: unknown, locale: Locale): string {
  if (typeof value !== "string") return JSON.stringify(value);
  return DAYS.has(field) ? formatDays(value, null, locale) : value;
}

function Mail() {
  const { t, locale } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const threads = usePagedList(
    ["threads", { q }],
    (page) => listThreads({ q, ...page }),
    30,
    (thread) => thread.thread_key,
  );

  return (
    <Section title={t.inbox.mail}>
      <div className="relative">
        <HugeiconsIcon
          icon={Search01Icon}
          strokeWidth={1.75}
          className="text-muted-foreground pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2"
        />
        <Input
          type="search"
          aria-label={t.inbox.search}
          placeholder={t.inbox.search}
          className="ps-9"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {threads.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : !threads.items ? (
        <Skeleton className="h-64 rounded-2xl" />
      ) : threads.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.inbox.mailEmpty}</p>
      ) : (
        <>
          {/* By day, as a mail app lists them: each day's threads on a card of their own. */}
          {byDay(threads.items, locale, me.data?.today ?? todayIso(), { today: t.chat.threadsToday, yesterday: t.chat.threadsYesterday }).map(([day, items]) => (
            <section key={day} className="grid gap-1.5">
              <h3 className="text-muted-foreground px-1 text-xs font-medium">{day}</h3>
              <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
                {items.map((thread) => {
                  const who = thread.participants.slice(0, 3).join(", ") || thread.latest.sender || "—";
                  return (
                    <li key={thread.thread_key}>
                      <CanvasLink
                        view={{ kind: "mail", id: thread.latest.id }}
                        className="hover:bg-accent/50 aria-[current=true]:bg-accent flex items-start gap-3 px-4 py-3.5 transition-colors"
                      >
                        <PersonAvatar name={thread.participants[0] ?? who} className="mt-0.5 size-9" />
                        <span className="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)] gap-0.5">
                          <span className="flex items-baseline gap-2">
                            <span className="truncate text-sm font-semibold">{who}</span>
                            {thread.message_count > 1 ? (
                              <span className="bg-secondary text-secondary-foreground shrink-0 rounded-full px-1.5 text-[11px] font-medium tabular-nums">
                                {thread.message_count}
                              </span>
                            ) : null}
                            <time
                              dateTime={thread.last_sent_at}
                              className="text-muted-foreground ms-auto shrink-0 text-xs tabular-nums"
                            >
                              {formatListDate(new Date(thread.last_sent_at), locale)}
                            </time>
                          </span>
                          <span className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate text-sm font-medium">
                              {thread.subject ? conversationName(thread.subject) : t.inbox.noSubject}
                            </span>
                            {thread.attachment_count > 0 ? (
                              <HugeiconsIcon
                                icon={Attachment01Icon}
                                strokeWidth={1.75}
                                aria-label={t.inbox.hasFiles}
                                className="text-muted-foreground size-3.5 shrink-0"
                              />
                            ) : null}
                          </span>
                          <span className="text-muted-foreground line-clamp-2 text-[13px] leading-snug">
                            {ownSnippet(thread.latest.snippet)}
                          </span>
                        </span>
                      </CanvasLink>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
          {threads.more ? (
            <Button
              variant="ghost"
              size="sm"
              className="justify-self-center"
              disabled={threads.loadingMore}
              onClick={threads.more}
            >
              {t.inbox.showMore}
            </Button>
          ) : null}
        </>
      )}
    </Section>
  );
}

/** The threads by the day of their last message, newest first: "Today", "Yesterday", then the
 * date. Keyed by the calendar day, the label only shown; "today" is the company's business day,
 * which a sandbox's skip moves on. */
function byDay<T extends { last_sent_at: string }>(
  threads: T[],
  locale: Locale,
  today: string,
  words: { today: string; yesterday: string },
): [string, T[]][] {
  const key = (date: Date) => todayIso(date);
  const [year = 1970, month = 1, day = 1] = today.split("-").map(Number);
  const yesterday = key(new Date(year, month - 1, day - 1));
  const days = new Map<string, { label: string; items: T[] }>();
  for (const thread of threads) {
    const date = new Date(thread.last_sent_at);
    const at = key(date);
    const label = at === today ? words.today : at === yesterday ? words.yesterday : formatDate(date, locale);
    const group = days.get(at) ?? { label, items: [] };
    group.items.push(thread);
    days.set(at, group);
  }
  return [...days.values()].map((group) => [group.label, group.items]);
}
