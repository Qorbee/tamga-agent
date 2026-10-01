"use client";

import {
  Attachment01Icon,
  Building03Icon,
  Cancel01Icon,
  Mail01Icon,
  PackageIcon,
  Route01Icon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { IconButton } from "@/components/icon-button";
import { PersonAvatar } from "@/components/person-avatar";
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

/** What was read of the job a project was proposed for: who it is for, the route, the load. */
export function JobFacts({ reading, className }: { reading: Placement["reading"]; className?: string }) {
  const { t } = useI18n();
  if (!reading) return null;
  const route = [reading.origin, reading.destination].filter(Boolean).join(" → ");
  const facts: [IconSvgElement, string, string | undefined][] = [
    [Building03Icon, t.project.client, reading.client],
    [Route01Icon, t.project.route, route || undefined],
    [PackageIcon, t.project.cargo, reading.cargo],
  ];
  const shown = facts.filter((fact) => fact[2]);
  if (shown.length === 0) return null;
  return (
    <ul className={cn("text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-sm", className)}>
      {shown.map(([icon, label, value]) => (
        <li key={label} className="inline-flex min-w-0 items-center gap-1.5" title={label}>
          <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-4 shrink-0" aria-label={label} />
          <span className="truncate">{value}</span>
        </li>
      ))}
    </ul>
  );
}

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
  const reason =
    placement.reason ||
    (placement.placed_by === "keys" ? t.inbox.byNumbers : placement.placed_by === "thread" ? t.inbox.byThread : "");

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
            <span className="max-w-full truncate">{who}</span>
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
          {reason ? <span className="text-muted-foreground mt-1 line-clamp-2 block text-sm text-pretty">{reason}</span> : null}
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
