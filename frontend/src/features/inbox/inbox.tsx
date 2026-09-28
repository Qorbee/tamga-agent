"use client";

import { Mail01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { NativeSelect } from "@/components/native-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createLink,
  decideProposal,
  getMessage,
  listLinks,
  listMessages,
  listProjects,
  listProposals,
  updateLink,
  type FieldProposal,
  type MessageSummary,
  type Project,
  type ProjectLink,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

/** Projects an item can still be placed in. */
const OPEN = ["draft", "proposed", "active"] as const;

/** What needs the owner: links to confirm, changes to accept, and the mail as it arrives. */
export function Inbox() {
  const projects = useQuery({
    queryKey: ["projects", { open: true }],
    queryFn: () => listProjects({ status: [...OPEN] }),
  });
  const byId = new Map(projects.data?.items.map((project) => [project.id, project]));

  return (
    <div className="grid w-full max-w-3xl gap-10 px-3 pb-10 md:px-6">
      <SuggestedLinks projects={byId} />
      <OpenProposals />
      <Mail projects={projects.data?.items ?? []} />
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

  return (
    <Section title={t.inbox.suggested}>
      {links.isPending ? (
        <Skeleton className="h-16 rounded-2xl" />
      ) : links.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : links.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.inbox.suggestedEmpty}</p>
      ) : (
        <ul className="divide-border border-border divide-y rounded-2xl border">
          {links.data.items.map((link) => {
            const project = projects.get(link.project_id);
            return (
              <li key={link.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    {t.inbox.kinds[link.item_kind]} →{" "}
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

  return (
    <Section title={t.inbox.proposals}>
      {proposals.isPending ? (
        <Skeleton className="h-16 rounded-2xl" />
      ) : proposals.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : proposals.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.inbox.proposalsEmpty}</p>
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

function Mail({ projects }: { projects: Project[] }) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [limit, setLimit] = useState(30);
  const messages = useQuery({
    queryKey: ["messages", { q, limit }],
    queryFn: () => listMessages({ q, limit }),
  });

  return (
    <Section title={t.inbox.mail}>
      <div className="relative max-w-xs">
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
      {messages.isPending ? (
        <Skeleton className="h-24 rounded-2xl" />
      ) : messages.isError ? (
        <p className="text-danger-foreground text-sm">{t.errors.load}</p>
      ) : messages.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.inbox.mailEmpty}</p>
      ) : (
        <>
          <ul className="divide-border border-border divide-y rounded-2xl border">
            {messages.data.items.map((message) => (
              <MailRow key={message.id} message={message} projects={projects} />
            ))}
          </ul>
          {messages.data.total > messages.data.items.length ? (
            <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => setLimit(limit + 30)}>
              {t.inbox.showMore}
            </Button>
          ) : null}
        </>
      )}
    </Section>
  );
}

function MailRow({ message, projects }: { message: MessageSummary; projects: Project[] }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState("");
  const detail = useQuery({
    queryKey: ["message", message.id],
    queryFn: () => getMessage(message.id),
    enabled: open,
  });
  const attach = useMutation({
    mutationFn: (project: string) =>
      createLink({ item_kind: "message", item_id: message.id, project_id: project, proof_of_delivery: false }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project-links"] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <li className="px-4 py-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-3 text-start"
      >
        <HugeiconsIcon icon={Mail01Icon} strokeWidth={1.75} className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium">{message.subject || t.inbox.noSubject}</span>
            <span className="text-muted-foreground ms-auto shrink-0 text-xs tabular-nums">
              {formatDate(new Date(message.sent_at), locale, "dateTime")}
            </span>
          </span>
          <span className="text-muted-foreground block truncate text-xs">
            {message.sender}
            {message.attachment_count > 0 ? ` · ${t.inbox.attachments(message.attachment_count)}` : ""}
          </span>
          {open ? null : <span className="text-muted-foreground line-clamp-1 text-xs">{message.snippet}</span>}
        </span>
      </button>
      {open ? (
        <div className="mt-3 grid gap-3 ps-7">
          {detail.isPending ? (
            <Skeleton className="h-20 rounded-xl" />
          ) : detail.isError ? (
            <p className="text-danger-foreground text-sm">{t.errors.load}</p>
          ) : (
            <>
              {/* The mail's text is shown as plain text: data, never markup (invariant 3). */}
              <p className="max-h-80 overflow-y-auto text-sm whitespace-pre-wrap">{detail.data.body_text}</p>
              {detail.data.attachments.length > 0 ? (
                <ul className="text-muted-foreground grid gap-1 text-xs">
                  {detail.data.attachments.map((file) => (
                    <li key={file.document_id}>{file.filename}</li>
                  ))}
                </ul>
              ) : null}
            </>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <NativeSelect
              aria-label={t.inbox.attachTo}
              className="h-8 w-64"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              <option value="">{t.inbox.attachTo}</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.number} · {project.name}
                </option>
              ))}
            </NativeSelect>
            <Button
              size="sm"
              variant="outline"
              disabled={!projectId || attach.isPending}
              onClick={() => attach.mutate(projectId)}
            >
              {t.inbox.attach}
            </Button>
            {attach.isSuccess ? <span className="text-success-foreground text-xs">{t.inbox.attached}</span> : null}
            {attach.isError ? <span className="text-danger-foreground text-xs">{t.errors.generic}</span> : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}
