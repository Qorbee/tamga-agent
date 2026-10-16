"use client";

import {
  ArrowRight01Icon,
  Attachment01Icon,
  Building03Icon,
  Calendar03Icon,
  Cancel01Icon,
  Mail01Icon,
  MoreHorizontalIcon,
  PackageIcon,
  PackageReceiveIcon,
  Route01Icon,
  Tick02Icon,
  TruckIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { useIsOwner } from "@/lib/use-role";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { MoveConversationDialog } from "@/components/move-conversation-dialog";
import { PersonAvatar } from "@/components/person-avatar";
import { T } from "@/components/translated";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { decidePlacement, type Placement, type Project } from "@/lib/api";
import { formatDays, formatListDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";
import { useInvalidatePlacements } from "@/lib/use-invalidate-placements";
import { cn } from "@/lib/utils";

/** A number beside its icon, named in full for a screen reader. */
export function Count({ icon, count, label }: { icon: IconSvgElement; count: number; label: string }) {
  return (
    <Hint label={label}>
      <span className="inline-flex shrink-0 items-center gap-1 tabular-nums" aria-label={label}>
        <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
        {count}
      </span>
    </Hint>
  );
}

type Reading = NonNullable<Placement["reading"]>;

function Fact({ icon, label, children }: { icon: IconSvgElement; label: string; children: ReactNode }) {
  return (
    <span className="flex max-w-full min-w-0 items-start gap-1.5">
      <Hint label={label}>
        <span className="mt-0.5 shrink-0">
          <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-4" aria-label={label} />
        </span>
      </Hint>
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </span>
  );
}

/**
 * What is known of a project's job: who gave it and who it was given on to, where it goes and to
 * whom, the load, and its days. The server works the job out across the project's conversations
 * (what most of them say, each firm in one role); the days are the project's own. A long value
 * wraps; nothing is cut at the edge.
 */
export function JobFacts({
  project,
  className,
}: {
  project: Pick<Project, "job" | "starts_on" | "ends_on">;
  className?: string;
}) {
  const { t, locale } = useI18n();
  const job = project.job;
  const client = job?.client;
  const subcontractors = job?.subcontractors ?? [];
  const consignee = job?.consignee;
  const route = [job?.origin, job?.destination].filter(Boolean).join(" → ");
  const cargo = job?.cargo;
  const trucks = job?.trucks;
  const days = formatDays(project.starts_on, project.ends_on, locale);
  if (!client && subcontractors.length === 0 && !consignee && !route && !cargo && !trucks && !days) return null;
  return (
    <ul className={cn("text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-sm", className)}>
      {client || subcontractors.length > 0 ? (
        <li className="flex max-w-full min-w-0 flex-wrap items-start gap-x-2 gap-y-1">
          {client ? (
            <Fact icon={Building03Icon} label={t.project.roles.client}>
              {client}
            </Fact>
          ) : null}
          {client && subcontractors.length > 0 ? (
            <HugeiconsIcon icon={ArrowRight01Icon} strokeWidth={1.75} className="mt-1 size-3.5 shrink-0" aria-hidden />
          ) : null}
          {subcontractors.length > 0 ? (
            <Fact icon={TruckIcon} label={t.project.roles.subcontractor}>
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
      {consignee ? (
        <li className="flex max-w-full min-w-0">
          <Fact icon={PackageReceiveIcon} label={t.project.roles.consignee}>
            {consignee}
          </Fact>
        </li>
      ) : null}
      {cargo || trucks ? (
        <li className="flex max-w-full min-w-0">
          <Fact icon={PackageIcon} label={t.project.cargo}>
            {cargo ? <T>{cargo}</T> : null}
            {cargo && trucks ? " · " : null}
            {trucks ? t.board.trucks(trucks) : null}
          </Fact>
        </li>
      ) : null}
      {days ? (
        <li className="flex max-w-full min-w-0">
          <Fact icon={Calendar03Icon} label={t.project.days}>
            <span className="tabular-nums">{days}</span>
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
  const isOwner = useIsOwner();
  const [moving, setMoving] = useState(false);
  const conversation = placement.conversation;
  if (!conversation) return null;
  const who = conversation.participants.slice(0, 2).join(", ") || conversation.latest.sender || "—";
  const role = placement.reading?.party_role;
  const roleIcon = role ? ROLE_ICONS[role] : undefined;
  const roleLabel = role === "client" ? t.project.client : role === "subcontractor" ? t.project.subcontractor : "";
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
                <Hint label={`${roleLabel}: ${placement.reading?.party ?? ""}`}>
                  <span className="text-foreground/80 shrink-0">
                    <HugeiconsIcon icon={roleIcon} strokeWidth={1.75} className="size-3.5" aria-label={roleLabel} />
                  </span>
                </Hint>
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
            <span className="text-muted-foreground mt-1 line-clamp-2 text-sm text-pretty">
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
      <RowAnswer waiting={placement.suggested_links > 0}>
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
      {isOwner && placement.project_id ? (
        <>
          <DropdownMenu modal={false}>
            <Hint label={t.project.conversationActions}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-muted-foreground mt-1 shrink-0"
                  aria-label={t.project.conversationActions}
                >
                  <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                </Button>
              </DropdownMenuTrigger>
            </Hint>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setMoving(true)}>{t.project.move}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <MoveConversationDialog placement={placement} open={moving} onOpenChange={setMoving} />
        </>
      ) : null}
    </li>
  );
}

/**
 * The owner's answer while a conversation waits; a tick once given. A model's own confidence is
 * not shown: nothing checks it. What backs a proposal is weighed by code (`ProposalEvidence`).
 */
export function RowAnswer({ waiting, children }: { waiting: boolean; children: ReactNode }) {
  const { t } = useI18n();
  if (!waiting)
    return (
      <Hint label={t.inbox.confirmed}>
        <span className="text-success-foreground mt-3 shrink-0 px-2">
          <HugeiconsIcon icon={Tick02Icon} strokeWidth={1.75} className="size-4" aria-label={t.inbox.confirmed} />
        </span>
      </Hint>
    );
  return <span className="mt-1 flex shrink-0 items-center gap-0.5">{children}</span>;
}
