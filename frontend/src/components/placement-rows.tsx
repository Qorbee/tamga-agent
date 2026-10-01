"use client";

import {
  ArrowRight01Icon,
  Attachment01Icon,
  Building03Icon,
  Cancel01Icon,
  Mail01Icon,
  PackageIcon,
  Route01Icon,
  Tick02Icon,
  TruckIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { IconButton } from "@/components/icon-button";
import { PersonAvatar } from "@/components/person-avatar";
import { T } from "@/components/translated";
import { decidePlacement, type Placement } from "@/lib/api";
import { formatListDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";
import { cn } from "@/lib/utils";

/** Everything a decision on a placement changes: the lists it is in and the counts beside them. */
export function useInvalidatePlacements(): () => void {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["placements"] });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    void queryClient.invalidateQueries({ queryKey: ["project"] });
    void queryClient.invalidateQueries({ queryKey: ["project-links"] });
  };
}

/** A number beside its icon, named in full for a screen reader. */
export function Count({ icon, count, label }: { icon: IconSvgElement; count: number; label: string }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 tabular-nums" aria-label={label} title={label}>
      <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
      {count}
    </span>
  );
}

type Reading = NonNullable<Placement["reading"]>;

/** Names as they were read, one of each however it was capitalised: mail writes a firm in
 * capitals as often as not, and a Turkish İ or ı is its own letter. */
function distinct(names: (string | null | undefined)[]): string[] {
  const seen = new Map<string, string>();
  for (const name of names) {
    if (!name) continue;
    const folded = name.replace(/[İIı]/g, "i").toLowerCase();
    if (!seen.has(folded)) seen.set(folded, name);
  }
  return [...seen.values()];
}

function Fact({ icon, label, children }: { icon: IconSvgElement; label: string; children: ReactNode }) {
  return (
    <span className="flex max-w-full min-w-0 items-start gap-1.5" title={label}>
      <HugeiconsIcon icon={icon} strokeWidth={1.75} className="mt-0.5 size-4 shrink-0" aria-label={label} />
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </span>
  );
}

/**
 * What was read of a project's job across its conversations: who gave it and who it was given on
 * to, the route, the load. A long value wraps; nothing is cut at the edge.
 */
export function JobFacts({ placements, className }: { placements: Placement[]; className?: string }) {
  const { t } = useI18n();
  const readings = placements.flatMap((placement) => (placement.reading ? [placement.reading] : []));
  const first = (key: "client" | "origin" | "destination" | "cargo") =>
    readings.find((reading) => reading[key])?.[key] ?? undefined;
  const dealtWith = (role: Reading["party_role"]) =>
    readings.filter((reading) => reading.party_role === role).map((reading) => reading.party);
  const client = first("client") ?? distinct(dealtWith("client"))[0];
  const subcontractors = distinct([...readings.map((reading) => reading.subcontractor), ...dealtWith("subcontractor")]);
  const route = [first("origin"), first("destination")].filter(Boolean).join(" → ");
  const cargo = first("cargo");
  if (!client && subcontractors.length === 0 && !route && !cargo) return null;
  return (
    <ul className={cn("text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-sm", className)}>
      {client || subcontractors.length > 0 ? (
        <li className="flex max-w-full min-w-0 flex-wrap items-start gap-x-2 gap-y-1">
          {client ? (
            <Fact icon={Building03Icon} label={t.project.client}>
              {client}
            </Fact>
          ) : null}
          {client && subcontractors.length > 0 ? (
            <HugeiconsIcon icon={ArrowRight01Icon} strokeWidth={1.75} className="mt-1 size-3.5 shrink-0" aria-hidden />
          ) : null}
          {subcontractors.length > 0 ? (
            <Fact icon={TruckIcon} label={t.project.subcontractor}>
              {subcontractors.join(", ")}
            </Fact>
          ) : null}
        </li>
      ) : null}
      {route ? (
        <li className="flex max-w-full min-w-0">
          <Fact icon={Route01Icon} label={t.project.route}>
            <T>{route}</T>
          </Fact>
        </li>
      ) : null}
      {cargo ? (
        <li className="flex max-w-full min-w-0">
          <Fact icon={PackageIcon} label={t.project.cargo}>
            <T>{cargo}</T>
          </Fact>
        </li>
      ) : null}
    </ul>
  );
}

/** What the firm the company deals with in a conversation is to it in this job. */
const ROLE_ICONS: Partial<Record<NonNullable<Reading["party_role"]>, IconSvgElement>> = {
  client: Building03Icon,
  subcontractor: TruckIcon,
};

/**
 * Conversations placed in a project, one row each however many mails it holds: what it is, who
 * wrote, why it was placed there, and the owner's answer while its links wait. A row opens the
 * conversation in the canvas.
 */
export function PlacementRows({ placements, className }: { placements: Placement[]; className?: string }) {
  return (
    <ul className={cn("divide-border divide-y", className)}>
      {placements.map((placement) => (
        <PlacementRow key={placement.id} placement={placement} />
      ))}
    </ul>
  );
}

function PlacementRow({ placement }: { placement: Placement }) {
  const { t, locale } = useI18n();
  const invalidate = useInvalidatePlacements();
  const decide = useMutation({
    mutationFn: (links: "confirmed" | "rejected") => decidePlacement(placement.id, { links }),
    onSuccess: invalidate,
  });
  const conversation = placement.conversation;
  if (!conversation) return null;
  const who = conversation.participants.slice(0, 2).join(", ") || conversation.latest.sender || "—";
  const role = placement.reading?.party_role;
  const roleIcon = role ? ROLE_ICONS[role] : undefined;
  const roleLabel =
    role === "client" ? t.project.client : role === "subcontractor" ? t.project.subcontractor : "";
  // Why code placed it is the app's own wording, in the reader's language already.
  const byCode = placement.placed_by === "keys" ? t.inbox.byNumbers : placement.placed_by === "thread" ? t.inbox.byThread : "";

  return (
    <li className="flex items-start gap-1 py-1 ps-2 pe-3">
      <CanvasLink
        view={{ kind: "mail", id: conversation.latest.id }}
        className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex min-w-0 flex-1 items-start gap-3 rounded-xl px-2 py-2 transition-colors"
      >
        <PersonAvatar name={conversation.participants[0] ?? who} className="mt-0.5" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">
            {conversation.subject ? conversationName(conversation.subject) : t.inbox.noSubject}
          </span>
          <span className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
            <span className="inline-flex max-w-full min-w-0 items-center gap-1">
              {roleIcon ? (
                <span className="text-foreground/80 shrink-0" title={`${roleLabel}: ${placement.reading?.party ?? ""}`}>
                  <HugeiconsIcon icon={roleIcon} strokeWidth={1.75} className="size-3.5" aria-label={roleLabel} />
                </span>
              ) : null}
              <span className="truncate">{who}</span>
            </span>
            <Count icon={Mail01Icon} count={conversation.message_count} label={t.mail.messages(conversation.message_count)} />
            {conversation.attachment_count > 0 ? (
              <Count
                icon={Attachment01Icon}
                count={conversation.attachment_count}
                label={t.inbox.files(conversation.attachment_count)}
              />
            ) : null}
            <time dateTime={conversation.last_sent_at} className="tabular-nums">
              {formatListDate(new Date(conversation.last_sent_at), locale)}
            </time>
          </span>
          {placement.reason || byCode ? (
            <span className="text-muted-foreground mt-1 line-clamp-2 block text-sm text-pretty">
              {placement.reason ? <T>{placement.reason}</T> : byCode}
            </span>
          ) : null}
          {decide.isError ? (
            <span className="text-danger-foreground mt-1 block text-xs" role="alert">
              {t.errors.generic}
            </span>
          ) : null}
        </span>
      </CanvasLink>
      <RowAnswer waiting={placement.suggested_links > 0} confidence={placement.confidence}>
        <IconButton
          icon={Tick02Icon}
          label={t.inbox.confirm}
          disabled={decide.isPending}
          onClick={() => decide.mutate("confirmed")}
        />
        <IconButton
          icon={Cancel01Icon}
          label={t.inbox.reject}
          className="text-muted-foreground"
          disabled={decide.isPending}
          onClick={() => decide.mutate("rejected")}
        />
      </RowAnswer>
    </li>
  );
}

/** The owner's answer while a conversation waits, with how sure the placement is; a tick once given. */
function RowAnswer({
  waiting,
  confidence,
  children,
}: {
  waiting: boolean;
  confidence: number | null;
  children: ReactNode;
}) {
  const { t } = useI18n();
  if (!waiting)
    return (
      <span className="text-success-foreground mt-3 shrink-0 px-2" title={t.inbox.confirmed}>
        <HugeiconsIcon icon={Tick02Icon} strokeWidth={1.75} className="size-4" aria-label={t.inbox.confirmed} />
      </span>
    );
  const percent = confidence === null ? null : Math.round(confidence * 100);
  return (
    <span className="mt-1 flex shrink-0 items-center gap-0.5">
      {percent !== null ? (
        <span
          className="text-muted-foreground me-1 text-xs tabular-nums max-sm:hidden"
          title={t.inbox.confidence(percent)}
          aria-label={t.inbox.confidence(percent)}
        >
          {percent}%
        </span>
      ) : null}
      {children}
    </span>
  );
}
