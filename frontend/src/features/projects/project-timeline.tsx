"use client";

import { Cancel01Icon, Invoice01Icon, MoreHorizontalIcon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { fileIcon } from "@/components/file-icon";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { MoveConversationDialog } from "@/components/move-conversation-dialog";
import { PersonAvatar } from "@/components/person-avatar";
import { RowAnswer } from "@/components/placement-rows";
import { ProjectPicker } from "@/components/project-picker";
import { T } from "@/components/translated";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createLink,
  decidePlacement,
  listAllLinks,
  listAllPlacements,
  listDocumentTypes,
  updateLink,
  type LinkItem,
  type MessageSummary,
  type Placement,
  type ProjectLink,
} from "@/lib/api";
import { formatDate, formatListDate, formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";
import { useInvalidatePlacements } from "@/lib/use-invalidate-placements";
import { useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

export const linksKey = (id: string) => ["project-links", { project_id: id, expand: "item" }] as const;
export const placementsKey = (id: string) => ["placements", { projects: [id] }] as const;
export const readLinks = (id: string) =>
  listAllLinks({ project_id: id, status: ["suggested", "confirmed"], expand: ["item"] });
export const readPlacements = (id: string) => listAllPlacements({ project_id: [id] });

type MailItem = Extract<LinkItem, { item_kind: "message" }>;
type FileItem = Extract<LinkItem, { item_kind: "document" }>;
type BillItem = Extract<LinkItem, { item_kind: "invoice" }>;

type Row =
  | { kind: "mail"; key: string; at: string; links: ProjectLink[]; mails: MessageSummary[]; placement?: Placement }
  | { kind: "file"; key: string; at: string; link: ProjectLink; item: FileItem }
  | { kind: "invoice"; key: string; at: string; link: ProjectLink; item: BillItem };

type Show = "all" | "mail" | "files" | "invoices";
const SHOWN: Record<Show, Row["kind"] | null> = { all: null, mail: "mail", files: "file", invoices: "invoice" };

/** The calendar day a moment falls on where the reader is, as `2026-10-04`. */
function dayOf(at: string): string {
  const date = new Date(at);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/**
 * The project's file as rows, newest first: a conversation's mails of one day are one row (it is
 * answered as a whole), each file and each invoice a row of its own.
 */
function rowsOf(links: ProjectLink[], placements: Placement[]): Row[] {
  const byThread = new Map(
    placements.flatMap((placement) =>
      placement.conversation ? [[placement.conversation.thread_key, placement] as const] : [],
    ),
  );
  const mails = new Map<string, Extract<Row, { kind: "mail" }>>();
  const rows: Row[] = [];
  for (const link of links) {
    const item = link.item;
    if (!item) continue;
    if (item.item_kind === "message") {
      const mail = (item as MailItem).message;
      const key = `${mail.thread_key}|${dayOf(item.at)}`;
      const row = mails.get(key);
      if (row) {
        row.links.push(link);
        row.mails.push(mail);
        if (item.at > row.at) row.at = item.at;
      } else {
        const created = {
          kind: "mail" as const,
          key,
          at: item.at,
          links: [link],
          mails: [mail],
          placement: byThread.get(mail.thread_key),
        };
        mails.set(key, created);
        rows.push(created);
      }
    } else if (item.item_kind === "document") {
      rows.push({ kind: "file", key: link.id, at: item.at, link, item: item as FileItem });
    } else {
      rows.push({ kind: "invoice", key: link.id, at: item.at, link, item: item as BillItem });
    }
  }
  return rows.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}

/**
 * Everything tied to the project, whatever brought it: mail, files and the books' invoices, a day
 * at a time, newest first. What still waits for the owner is answered on its row.
 */
export function ProjectTimeline({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const [show, setShow] = useState<Show>("all");
  const links = useQuery({ queryKey: linksKey(id), queryFn: () => readLinks(id) });
  const placements = useQuery({ queryKey: placementsKey(id), queryFn: () => readPlacements(id) });

  const rows = rowsOf(links.data ?? [], placements.data ?? []);
  const counts = { all: rows.length, mail: 0, files: 0, invoices: 0 } satisfies Record<Show, number>;
  for (const row of rows) counts[row.kind === "mail" ? "mail" : row.kind === "file" ? "files" : "invoices"] += 1;
  const shown = rows.filter((row) => SHOWN[show] === null || row.kind === SHOWN[show]);
  const days: { day: string; rows: Row[] }[] = [];
  for (const row of shown) {
    const day = dayOf(row.at);
    const last = days.at(-1);
    if (last?.day === day) last.rows.push(row);
    else days.push({ day, rows: [row] });
  }

  return (
    <section className="grid grid-cols-1 gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h4 className="text-sm font-medium">{t.project.timeline}</h4>
        {rows.length > 0 ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t.project.timeline}>
            {(Object.keys(SHOWN) as Show[]).map((each) =>
              each === "all" || counts[each] > 0 ? (
                <Button
                  key={each}
                  size="xs"
                  variant={show === each ? "secondary" : "ghost"}
                  aria-pressed={show === each}
                  className="rounded-full"
                  onClick={() => setShow(each)}
                >
                  {t.project.show[each]}
                  <span className="text-muted-foreground tabular-nums">{counts[each]}</span>
                </Button>
              ) : null,
            )}
          </div>
        ) : null}
      </div>
      {links.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : links.isPending ? (
        <Skeleton className="h-32 rounded-2xl" />
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.project.noItems}</p>
      ) : shown.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.project.noneShown}</p>
      ) : (
        <ol className="grid gap-4">
          {days.map(({ day, rows: dayRows }) => (
            <li key={day} className="grid gap-1.5">
              <h5 className="text-muted-foreground px-1 text-xs font-medium">
                <time dateTime={day}>{dayLabel(day, t.project, locale)}</time>
              </h5>
              <ul className="divide-border border-border divide-y overflow-hidden rounded-2xl border">
                {dayRows.map((row) =>
                  row.kind === "mail" ? (
                    <MailRow key={row.key} row={row} />
                  ) : row.kind === "file" ? (
                    <FileRow key={row.key} link={row.link} item={row.item} />
                  ) : (
                    <InvoiceRow key={row.key} link={row.link} item={row.item} />
                  ),
                )}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function dayLabel(
  day: string,
  words: { today: string; yesterday: string },
  locale: Parameters<typeof formatDate>[1],
): string {
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (day === dayOf(today.toISOString())) return words.today;
  if (day === dayOf(yesterday.toISOString())) return words.yesterday;
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return formatDate(new Date(year, month - 1, date), locale);
}

/** One row of the file: what it is, opened in the canvas, and the owner's answer beside it. */
function TimelineRow({
  view,
  icon,
  title,
  detail,
  note,
  answer,
  menu,
}: {
  view: Parameters<typeof CanvasLink>[0]["view"];
  icon: ReactNode;
  title: ReactNode;
  detail: ReactNode;
  note?: ReactNode;
  answer: ReactNode;
  menu?: ReactNode;
}) {
  return (
    <li className="flex items-start gap-1 py-1 ps-2 pe-3">
      <CanvasLink
        view={view}
        className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex min-w-0 flex-1 items-start gap-3 rounded-xl px-2 py-2 transition-colors"
      >
        {icon}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{title}</span>
          <span className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">{detail}</span>
          {note}
        </span>
      </CanvasLink>
      {answer}
      {menu}
    </li>
  );
}

function RowIcon({ icon }: { icon: Parameters<typeof HugeiconsIcon>[0]["icon"] }) {
  return (
    <span className="bg-muted text-muted-foreground mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full">
      <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-4" aria-hidden />
    </span>
  );
}

function MailRow({ row }: { row: Extract<Row, { kind: "mail" }> }) {
  const { t, locale } = useI18n();
  const isOwner = useIsOwner();
  const invalidate = useInvalidatePlacements();
  const [moving, setMoving] = useState(false);
  const waiting = row.links.filter((link) => link.status === "suggested");
  const decide = useMutation({
    mutationFn: async (answer: "confirmed" | "rejected") => {
      // The conversation is answered as a whole where the linker placed it; else mail by mail.
      if (row.placement) await decidePlacement(row.placement.id, { links: answer });
      else for (const link of waiting) await updateLink(link.id, { status: answer });
    },
    onSuccess: invalidate,
  });
  const latest = row.mails.reduce((a, b) => (a.sent_at > b.sent_at ? a : b));
  const who = latest.sender_name || latest.sender || "—";
  const subject = row.placement?.conversation?.subject ?? latest.subject;

  return (
    <TimelineRow
      view={{ kind: "mail", id: latest.id }}
      icon={<PersonAvatar name={who} className="mt-0.5" />}
      title={subject ? conversationName(subject) : t.inbox.noSubject}
      detail={
        <>
          <span className="truncate">{who}</span>
          {row.mails.length > 1 ? <span>{t.project.mailsThatDay(row.mails.length)}</span> : null}
          <time dateTime={latest.sent_at} className="tabular-nums">
            {formatListDate(new Date(latest.sent_at), locale)}
          </time>
        </>
      }
      note={
        waiting.length > 0 && row.placement?.reason ? (
          <span className="text-muted-foreground mt-1 line-clamp-2 block text-sm text-pretty">
            <T>{row.placement.reason}</T>
          </span>
        ) : decide.isError ? (
          <span className="text-danger-foreground mt-1 block text-xs" role="alert">
            {t.errors.generic}
          </span>
        ) : null
      }
      answer={
        <Answer
          waiting={waiting.length > 0}
          confidence={row.placement?.confidence ?? Math.max(...row.links.map((link) => link.confidence))}
          pending={decide.isPending}
          onAnswer={isOwner ? (answer) => decide.mutate(answer) : undefined}
        />
      }
      menu={
        isOwner && row.placement?.project_id ? (
          <>
            <RowMenu onMove={() => setMoving(true)} />
            <MoveConversationDialog placement={row.placement} open={moving} onOpenChange={setMoving} />
          </>
        ) : null
      }
    />
  );
}

function FileRow({ link, item }: { link: ProjectLink; item: FileItem }) {
  const { t, locale } = useI18n();
  const types = useQuery({ queryKey: ["document-types"], queryFn: listDocumentTypes, staleTime: Infinity });
  const file = item.document;
  const typeKey = file.doc_type;
  const typeLabel = typeKey
    ? (t.files.types[typeKey] ?? types.data?.find((type) => type.key === typeKey)?.label ?? typeKey)
    : null;
  return (
    <ItemRow
      link={link}
      view={{ kind: "document", id: file.id }}
      icon={<RowIcon icon={fileIcon(file.preview)} />}
      title={file.filename}
      detail={
        <>
          {typeLabel ? <span className="text-foreground/80">{typeLabel}</span> : null}
          <span>{item.message_id ? t.project.cameByMail : t.project.uploaded}</span>
          <time dateTime={item.at} className="tabular-nums">
            {formatListDate(new Date(item.at), locale)}
          </time>
        </>
      }
    />
  );
}

function InvoiceRow({ link, item }: { link: ProjectLink; item: BillItem }) {
  const { t, locale } = useI18n();
  const bill = item.invoice;
  const total =
    bill.total_amount !== null && bill.currency ? formatMoney(Number(bill.total_amount), bill.currency, locale) : null;
  return (
    <ItemRow
      link={link}
      view={{ kind: "invoice", id: bill.id }}
      icon={<RowIcon icon={Invoice01Icon} />}
      title={
        <span className={cn(bill.cancelled && "line-through")}>
          {[bill.number ?? t.books.noNumber, bill.party_name].filter(Boolean).join(" · ")}
        </span>
      }
      detail={
        <>
          <span className="text-foreground/80">{t.books.kinds[bill.kind]}</span>
          {total ? <span className={cn("tabular-nums", bill.cancelled && "line-through")}>{total}</span> : null}
          {bill.cancelled ? <span className="text-danger-foreground">{t.books.cancelled}</span> : null}
        </>
      }
    />
  );
}

/** A file's or an invoice's row: answered on its own link, moved by tying it elsewhere. */
function ItemRow({
  link,
  view,
  icon,
  title,
  detail,
}: {
  link: ProjectLink;
  view: Parameters<typeof CanvasLink>[0]["view"];
  icon: ReactNode;
  title: ReactNode;
  detail: ReactNode;
}) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const invalidate = useInvalidatePlacements();
  const [moving, setMoving] = useState(false);
  const decide = useMutation({
    mutationFn: (answer: "confirmed" | "rejected") => updateLink(link.id, { status: answer }),
    onSuccess: invalidate,
  });
  return (
    <TimelineRow
      view={view}
      icon={icon}
      title={title}
      detail={detail}
      note={
        decide.isError ? (
          <span className="text-danger-foreground mt-1 block text-xs" role="alert">
            {t.errors.generic}
          </span>
        ) : null
      }
      answer={
        <Answer
          waiting={link.status === "suggested"}
          confidence={link.confidence}
          pending={decide.isPending}
          onAnswer={isOwner ? (answer) => decide.mutate(answer) : undefined}
        />
      }
      menu={
        isOwner ? (
          <>
            <RowMenu onMove={() => setMoving(true)} />
            <MoveItemDialog link={link} open={moving} onOpenChange={setMoving} />
          </>
        ) : null
      }
    />
  );
}

function Answer({
  waiting,
  confidence,
  pending,
  onAnswer,
}: {
  waiting: boolean;
  confidence: number | null;
  pending: boolean;
  onAnswer?: (answer: "confirmed" | "rejected") => void;
}) {
  const { t } = useI18n();
  return (
    <RowAnswer waiting={waiting} confidence={confidence}>
      {onAnswer ? (
        <>
          <IconButton icon={Tick02Icon} label={t.inbox.confirm} disabled={pending} onClick={() => onAnswer("confirmed")} />
          <IconButton
            icon={Cancel01Icon}
            label={t.inbox.reject}
            className="text-muted-foreground"
            disabled={pending}
            onClick={() => onAnswer("rejected")}
          />
        </>
      ) : null}
    </RowAnswer>
  );
}

function RowMenu({ onMove }: { onMove: () => void }) {
  const { t } = useI18n();
  return (
    <DropdownMenu modal={false}>
      <Hint label={t.project.itemActions}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" className="text-muted-foreground mt-1 shrink-0" aria-label={t.project.itemActions}>
            <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
          </Button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onMove}>{t.project.move}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Moving a file or an invoice to another project: tied there by the owner first, then taken off
 * here, so a failure in between leaves it in both rather than in neither.
 */
function MoveItemDialog({
  link,
  open,
  onOpenChange,
}: {
  link: ProjectLink;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const invalidate = useInvalidatePlacements();
  const move = useMutation({
    mutationFn: async (projectId: string) => {
      await createLink({
        item_kind: link.item_kind as "document" | "invoice",
        item_id: link.item_id,
        project_id: projectId,
        proof_of_delivery: false,
      });
      await updateLink(link.id, { status: "rejected" });
    },
    onSuccess: () => {
      invalidate();
      onOpenChange(false);
    },
  });
  const item = link.item;
  const name =
    item?.item_kind === "document"
      ? (item as FileItem).document.filename
      : item?.item_kind === "invoice"
        ? ((item as BillItem).invoice.number ?? t.books.noNumber)
        : "";
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) move.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.project.moveTitle}</DialogTitle>
          <DialogDescription>{name}</DialogDescription>
        </DialogHeader>
        {open ? (
          <ProjectPicker exclude={[link.project_id]} disabled={move.isPending} onPick={(project) => move.mutate(project.id)} />
        ) : null}
        {move.isError ? (
          <p className="text-danger-foreground text-sm" role="alert">
            {t.errors.generic}
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
