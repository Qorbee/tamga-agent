"use client";

import { Attachment01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { PersonAvatar } from "@/components/person-avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  decideProposal,
  listLinks,
  listProjects,
  listProposals,
  listThreads,
  updateLink,
  type FieldProposal,
  type Project,
  type ProjectLink,
} from "@/lib/api";
import type { CanvasView } from "@/lib/canvas-view";
import { formatListDate } from "@/lib/format";
import { conversationName, ownSnippet } from "@/lib/mail-text";
import { useI18n } from "@/lib/i18n/client";

/** Projects an item can still be placed in. */
const OPEN = ["draft", "proposed", "active"] as const;

/** What a linked item opens as in the canvas. */
const ITEM_VIEWS: Partial<Record<ProjectLink["item_kind"], CanvasView["kind"]>> = {
  message: "mail",
  document: "document",
};

/**
 * What needs the owner: links to confirm and changes to accept, shown only while there are some,
 * then the mail as conversations. A conversation opens in the canvas.
 */
export function Inbox() {
  const projects = useQuery({
    queryKey: ["projects", { open: true }],
    queryFn: () => listProjects({ status: [...OPEN] }),
  });
  const byId = new Map(projects.data?.items.map((project) => [project.id, project]));

  return (
    <div className="grid gap-10">
      <SuggestedLinks projects={byId} />
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

function SuggestedLinks({ projects }: { projects: Map<string, Project> }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const links = useQuery({
    queryKey: ["project-links", { status: "suggested" }],
    queryFn: () => listLinks({ status: ["suggested"] }),
  });
  const decide = useMutation({
    mutationFn: ({ link, status }: { link: ProjectLink; status: "confirmed" | "rejected" }) =>
      updateLink(link.id, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project-links"] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  // Shown only while something waits: an empty section is noise above the mail.
  if (!links.isError && !links.data?.items.length) return null;
  return (
    <Section title={t.inbox.suggested}>
      {links.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : (
        <ul className="divide-border border-border divide-y rounded-2xl border">
          {links.data.items.map((link) => {
            const project = projects.get(link.project_id);
            return (
              <li key={link.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    {ITEM_VIEWS[link.item_kind] ? (
                      <CanvasLink
                        view={{ kind: ITEM_VIEWS[link.item_kind]!, id: link.item_id }}
                        className="underline-offset-4 hover:underline"
                      >
                        {t.inbox.kinds[link.item_kind]}
                      </CanvasLink>
                    ) : (
                      t.inbox.kinds[link.item_kind]
                    )}{" "}
                    →{" "}
                    <span className="font-mono text-xs">{project?.number ?? "…"}</span>{" "}
                    {project?.name}
                  </p>
                  <p className="text-muted-foreground text-xs tabular-nums">
                    {t.inbox.confidence(Math.round(link.confidence * 100))}
                  </p>
                </div>
                <Button
                  size="sm"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ link, status: "confirmed" })}
                >
                  {t.inbox.confirm}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ link, status: "rejected" })}
                >
                  {t.inbox.reject}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

function OpenProposals() {
  const { t } = useI18n();
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
            <li key={proposal.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-muted-foreground text-xs">
                  {t.inbox.field(t.inbox.targets[proposal.target], proposal.field)}
                </p>
                <p className="text-sm break-words">{display(proposal.value)}</p>
              </div>
              <Button
                size="sm"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ proposal, status: "accepted" })}
              >
                {t.inbox.accept}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ proposal, status: "dismissed" })}
              >
                {t.inbox.dismiss}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function display(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
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
