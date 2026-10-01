"use client";

import { Attachment01Icon, Cancel01Icon, Search01Icon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { IconButton } from "@/components/icon-button";
import { PersonAvatar } from "@/components/person-avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { decideProposal, listProposals, listThreads, type FieldProposal } from "@/lib/api";
import { formatDays, formatListDate } from "@/lib/format";
import { conversationName, ownSnippet } from "@/lib/mail-text";
import type { Locale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";

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
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [limit, setLimit] = useState(30);
  const threads = useQuery({
    queryKey: ["threads", { q, limit }],
    queryFn: () => listThreads({ q, limit }),
    placeholderData: keepPreviousData,
  });

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
      {threads.isPending ? (
        <Skeleton className="h-64 rounded-2xl" />
      ) : threads.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : threads.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.inbox.mailEmpty}</p>
      ) : (
        <>
          <ul className="divide-border border-border divide-y overflow-hidden rounded-2xl border">
            {threads.data.items.map((thread) => {
              const who = thread.participants.slice(0, 3).join(", ") || thread.latest.sender || "—";
              return (
                <li key={thread.thread_key}>
                  <CanvasLink
                    view={{ kind: "mail", id: thread.latest.id }}
                    className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex items-start gap-3 px-4 py-3"
                  >
                    <PersonAvatar name={thread.participants[0] ?? who} className="mt-0.5" />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline gap-2">
                        <span className="truncate text-sm font-medium">{who}</span>
                        {thread.message_count > 1 ? (
                          <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
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
                        <span className="min-w-0 flex-1 truncate text-sm">
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
                      <span className="text-muted-foreground block truncate text-xs">
                        {ownSnippet(thread.latest.snippet)}
                      </span>
                    </span>
                  </CanvasLink>
                </li>
              );
            })}
          </ul>
          {threads.data.total > threads.data.items.length ? (
            <Button variant="ghost" size="sm" className="justify-self-center" onClick={() => setLimit(limit + 30)}>
              {t.inbox.showMore}
            </Button>
          ) : null}
        </>
      )}
    </Section>
  );
}
