"use client";

import {
  Cancel01Icon,
  CheckmarkBadge01Icon,
  Invoice01Icon,
  MoreHorizontalIcon,
  Tick02Icon,
  TruckIcon,
  Upload01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useRef, useState, type ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { fileIcon } from "@/components/file-icon";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { chargesWord, useInvoiceLines } from "@/components/invoice-lines";
import { MoveConversationDialog } from "@/components/move-conversation-dialog";
import { PersonAvatar } from "@/components/person-avatar";
import { RowAnswer } from "@/components/placement-rows";
import { ProjectPicker } from "@/components/project-picker";
import { T } from "@/components/translated";
import { Segmented } from "@/components/segmented";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createLink,
  decidePlacement,
  listAllLinks,
  listAllPlacements,
  listDocumentTypes,
  updateLink,
  uploadDocument,
  type ChecklistItem,
  type InvoiceLine,
  type LinkItem,
  type MessageSummary,
  type Placement,
  type ProjectLink,
  type Shipment,
} from "@/lib/api";
import { formatDate, formatListDate, formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";
import { useInvalidatePlacements } from "@/lib/use-invalidate-placements";
import { useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

import { plateOf, useChecklist, useTrucks } from "./truck-board";

/** What was just rejected here, asked why once (ADR-0032): what the owner says reaches the
 * weekly learner, and a signature is code's to act on. */
type Rejected = { links: ProjectLink[]; kind: "mail" | "file" | "invoice" };
const AskWhy = createContext<(rejected: Rejected) => void>(() => {});

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
// The papers a truck's proof of delivery can be, once the owner says so (ADR-0021).
const PROOF_PAPERS = new Set(["cmr", "pod_photo"]);
// What a missing item is uploaded as; the others come from the books.
const UPLOADED_AS: Record<string, string> = {
  loading_photo: "loading_photo",
  cmr: "cmr",
  proof_of_delivery: "pod_photo",
  client_order: "transport_order",
};
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
 * at a time, newest first, and what is still missing above them. What still waits for the owner
 * is answered on its row. With a truck chosen on the board, only what is on that truck.
 */
export function ProjectTimeline({
  id,
  truck,
  onTruck,
}: {
  id: string;
  truck: string | null;
  onTruck: (truckId: string | null) => void;
}) {
  const { t, locale } = useI18n();
  const [show, setShow] = useState<Show>("all");
  const [rejected, setRejected] = useState<Rejected | null>(null);
  const links = useQuery({ queryKey: linksKey(id), queryFn: () => readLinks(id) });
  const placements = useQuery({ queryKey: placementsKey(id), queryFn: () => readPlacements(id) });
  const trucks = (useTrucks(id).data ?? []).filter((each) => each.state !== "rejected");
  const checklist = useChecklist(id).data ?? [];
  const chosen = trucks.find((each) => each.id === truck);

  const onChosen = (row: Row) =>
    !chosen ||
    (row.kind === "mail" ? row.links.some((link) => link.shipment_id === chosen.id) : row.link.shipment_id === chosen.id);
  const rows = rowsOf(links.data ?? [], placements.data ?? []).filter(onChosen);
  const lines = useInvoiceLines(rows.flatMap((row) => (row.kind === "invoice" ? [row.item.invoice.id] : [])));
  const linesOf = (billId: string) => (lines.data ?? []).filter((line) => line.invoice_id === billId);
  const counts = { all: rows.length, mail: 0, files: 0, invoices: 0 } satisfies Record<Show, number>;
  for (const row of rows) counts[row.kind === "mail" ? "mail" : row.kind === "file" ? "files" : "invoices"] += 1;
  const shown = rows.filter((row) => SHOWN[show] === null || row.kind === SHOWN[show]);
  const missing =
    show === "all" || show === "files"
      ? checklist.filter((item) => item.state === "missing" && (!chosen || item.shipment_id === chosen.id))
      : [];
  const days: { day: string; rows: Row[] }[] = [];
  for (const row of shown) {
    const day = dayOf(row.at);
    const last = days.at(-1);
    if (last?.day === day) last.rows.push(row);
    else days.push({ day, rows: [row] });
  }

  return (
    <AskWhy.Provider value={setRejected}>
    <section className="grid grid-cols-1 gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h4 className="text-sm font-medium">{t.project.timeline}</h4>
        {rows.length > 0 || chosen ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t.project.timeline}>
            {chosen ? (
              <Button
                size="xs"
                variant="secondary"
                className="rounded-full font-mono"
                aria-label={t.project.showAll}
                onClick={() => onTruck(null)}
              >
                <HugeiconsIcon icon={TruckIcon} strokeWidth={1.75} aria-hidden />
                {plateOf(chosen)}
                <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} aria-hidden />
              </Button>
            ) : null}
            <Segmented
              label={t.project.showLabel}
              value={show}
              onChange={setShow}
              options={(Object.keys(SHOWN) as Show[])
                .filter((each) => each === "all" || counts[each] > 0)
                .map((each) => ({
                  value: each,
                  className: "gap-1.5",
                  label: (
                    <>
                      {t.project.show[each]}
                      <span className="text-muted-foreground tabular-nums">{counts[each]}</span>
                    </>
                  ),
                }))}
            />
          </div>
        ) : null}
      </div>
      {rejected ? (
        <WhyRejected
          key={rejected.links.map((link) => link.id).join()}
          rejected={rejected}
          onDone={() => setRejected(null)}
        />
      ) : null}
      {links.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : links.isPending ? (
        <Skeleton className="h-32 rounded-2xl" />
      ) : null}
      {missing.length > 0 ? (
        <div className="grid gap-1.5">
          <h5 className="text-muted-foreground px-1 text-xs font-medium">{t.project.missing}</h5>
          <ul className="divide-border border-border divide-y overflow-hidden rounded-2xl border">
            {missing.map((item) => (
              <GapRow key={`${item.shipment_id}:${item.key}`} item={item} trucks={trucks} />
            ))}
          </ul>
        </div>
      ) : null}
      {links.isError || links.isPending ? null : rows.length === 0 ? (
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
                    <FileRow key={row.key} link={row.link} item={row.item} trucks={trucks} />
                  ) : (
                    <InvoiceRow key={row.key} link={row.link} item={row.item} trucks={trucks} charged={linesOf(row.item.invoice.id)} />
                  ),
                )}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </section>
    </AskWhy.Provider>
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
  wrapTitle = false,
  detail,
  note,
  answer,
  menu,
}: {
  view: Parameters<typeof CanvasLink>[0]["view"];
  icon: ReactNode;
  title: ReactNode;
  /** A title that is a sentence (what a model said a file is) wraps to a second line. */
  wrapTitle?: boolean;
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
          <span className={cn("text-sm font-medium", wrapTitle ? "line-clamp-2 break-words" : "block truncate")}>
            {title}
          </span>
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
  const askWhy = useContext(AskWhy);
  const waiting = row.links.filter((link) => link.status === "suggested");
  const decide = useMutation({
    mutationFn: async (answer: "confirmed" | "rejected") => {
      // The conversation is answered as a whole where the linker placed it; else mail by mail.
      if (row.placement) await decidePlacement(row.placement.id, { links: answer });
      else for (const link of waiting) await updateLink(link.id, { status: answer });
    },
    onSuccess: (_, answer) => {
      invalidate();
      if (answer === "rejected" && waiting.length > 0) askWhy({ links: waiting, kind: "mail" });
    },
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
          <span className="text-muted-foreground mt-1 line-clamp-2 text-sm text-pretty">
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

/**
 * A file's row says what the file is: its type, else the vision model's line on it, else its
 * name. The line stays under a typed file's row, and the name moves to the details.
 */
function FileRow({ link, item, trucks }: { link: ProjectLink; item: FileItem; trucks: Shipment[] }) {
  const { t, locale } = useI18n();
  const types = useQuery({ queryKey: ["document-types"], queryFn: listDocumentTypes, staleTime: Infinity });
  const file = item.document;
  const typeKey = file.doc_type;
  const typeLabel = typeKey
    ? (t.files.types[typeKey] ?? types.data?.find((type) => type.key === typeKey)?.label ?? typeKey)
    : null;
  const shows = file.description.trim();
  return (
    <ItemRow
      link={link}
      trucks={trucks}
      proofPaper={typeKey !== null && PROOF_PAPERS.has(typeKey)}
      isFile
      view={{ kind: "document", id: file.id }}
      icon={<RowIcon icon={fileIcon(file.preview)} />}
      title={typeLabel ?? (shows ? <T>{shows}</T> : file.filename)}
      wrapTitle={!typeLabel && Boolean(shows)}
      about={
        typeLabel && shows ? (
          <span className="text-muted-foreground mt-0.5 line-clamp-2 text-sm text-pretty">
            <T>{shows}</T>
          </span>
        ) : null
      }
      detail={
        <>
          {typeLabel || shows ? <span className="max-w-full truncate">{file.filename}</span> : null}
          <span>{item.message_id ? t.project.cameByMail : t.project.uploaded}</span>
          <time dateTime={item.at} className="tabular-nums">
            {formatListDate(new Date(item.at), locale)}
          </time>
        </>
      }
    />
  );
}

function InvoiceRow({
  link,
  item,
  trucks,
  charged,
}: {
  link: ProjectLink;
  item: BillItem;
  trucks: Shipment[];
  charged: InvoiceLine[];
}) {
  const { t, locale } = useI18n();
  const bill = item.invoice;
  const charges = chargesWord(charged, t);
  const total =
    bill.total_amount !== null && bill.currency ? formatMoney(Number(bill.total_amount), bill.currency, locale) : null;
  return (
    <ItemRow
      link={link}
      trucks={trucks}
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
          {bill.returns ? <span className="text-warning-foreground">{t.books.returned}</span> : null}
          {charges ? <span>{charges}</span> : null}
          {total ? <span className={cn("tabular-nums", bill.cancelled && "line-through")}>{total}</span> : null}
          {bill.cancelled ? <span className="text-danger-foreground">{t.books.cancelled}</span> : null}
        </>
      }
    />
  );
}

/**
 * A file's or an invoice's row: answered on its own link, put on a truck, moved by tying it
 * elsewhere. A CMR or delivery photo on a truck asks whether it is that truck's proof of delivery:
 * only the owner's yes makes it one (ADR-0021).
 */
function ItemRow({
  link,
  trucks,
  proofPaper = false,
  isFile = false,
  view,
  icon,
  title,
  wrapTitle,
  about,
  detail,
}: {
  link: ProjectLink;
  trucks: Shipment[];
  proofPaper?: boolean;
  isFile?: boolean;
  view: Parameters<typeof CanvasLink>[0]["view"];
  icon: ReactNode;
  title: ReactNode;
  wrapTitle?: boolean;
  /** What the item is, in a line under its title. */
  about?: ReactNode;
  detail: ReactNode;
}) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const invalidate = useInvalidatePlacements();
  const [moving, setMoving] = useState(false);
  const askWhy = useContext(AskWhy);
  const decide = useMutation({
    mutationFn: (answer: "confirmed" | "rejected") => updateLink(link.id, { status: answer }),
    onSuccess: (_, answer) => {
      invalidate();
      if (answer === "rejected") askWhy({ links: [link], kind: isFile ? "file" : "invoice" });
    },
  });
  const place = useMutation({
    mutationFn: (shipmentId: string | null) => updateLink(link.id, { shipment_id: shipmentId }),
    onSuccess: invalidate,
  });
  const prove = useMutation({
    mutationFn: () => updateLink(link.id, { status: "confirmed", proof_of_delivery: true }),
    onSuccess: invalidate,
  });
  const truck = trucks.find((each) => each.id === link.shipment_id);
  const proven = link.proof_of_delivery && link.status === "confirmed" && link.method === "owner" && truck;
  const failed = decide.error ?? place.error ?? prove.error;
  return (
    <TimelineRow
      view={view}
      icon={icon}
      title={title}
      wrapTitle={wrapTitle}
      detail={
        <>
          {detail}
          {truck ? (
            <span className="inline-flex items-center gap-1 font-mono">
              <HugeiconsIcon icon={TruckIcon} strokeWidth={1.75} className="size-3.5" aria-hidden />
              {plateOf(truck)}
            </span>
          ) : null}
          {proven ? (
            <span className="text-success-foreground inline-flex items-center gap-1">
              <HugeiconsIcon icon={CheckmarkBadge01Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
              {t.project.proof}
            </span>
          ) : null}
        </>
      }
      note={
        <>
          {about}
          {proofPaper && truck && !proven && isOwner ? (
            <span className="mt-1.5 flex flex-wrap items-center gap-2 text-sm">
              <span>{t.project.proofQuestion(plateOf(truck))}</span>
              <Button
                size="xs"
                variant="outline"
                disabled={prove.isPending}
                onClick={(event) => {
                  // The row opens the file; the answer is the button's own.
                  event.preventDefault();
                  event.stopPropagation();
                  prove.mutate();
                }}
              >
                {t.project.proofYes}
              </Button>
            </span>
          ) : null}
          {failed ? (
            <span className="text-danger-foreground mt-1 block text-xs" role="alert">
              {failed instanceof Error && failed.message ? failed.message : t.errors.generic}
            </span>
          ) : null}
        </>
      }
      answer={
        <Answer
          waiting={link.status === "suggested"}
          pending={decide.isPending}
          onAnswer={isOwner ? (answer) => decide.mutate(answer) : undefined}
        />
      }
      menu={
        isOwner ? (
          <>
            <RowMenu
              onMove={() => setMoving(true)}
              trucks={trucks}
              current={link.shipment_id}
              onTruck={(shipmentId) => place.mutate(shipmentId)}
              // Any file on a truck may be its proof, typed or not: the question asks only of a CMR
              // or a delivery photo, the menu of the rest.
              onProve={isFile && truck && !proven ? () => prove.mutate() : undefined}
            />
            <MoveItemDialog link={link} open={moving} onOpenChange={setMoving} />
          </>
        ) : null
      }
    />
  );
}

/** What the job still lacks: uploaded here and put on its truck, or, for the books' records, said
 * to come from there. */
function GapRow({ item, trucks }: { item: ChecklistItem; trucks: Shipment[] }) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const invalidate = useInvalidatePlacements();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const docType = UPLOADED_AS[item.key];
  const truck = trucks.find((each) => each.id === item.shipment_id);
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const uploaded = await uploadDocument(file, { docType });
      await createLink({
        item_kind: "document",
        item_id: uploaded.document.id,
        project_id: item.project_id,
        shipment_id: item.shipment_id,
        proof_of_delivery: false,
      });
    },
    onSuccess: () => {
      invalidate();
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const name = t.project.checklist[item.key] ?? item.key;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
      <span className="text-danger-foreground min-w-0 flex-1 text-sm">
        {name}
        {truck ? <span className="text-muted-foreground font-mono"> · {plateOf(truck)}</span> : null}
      </span>
      {docType && isOwner ? (
        <>
          <input
            ref={input}
            type="file"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload.mutate(file);
              event.target.value = "";
            }}
          />
          <Button size="xs" variant="outline" disabled={upload.isPending} onClick={() => input.current?.click()}>
            <HugeiconsIcon icon={Upload01Icon} strokeWidth={1.75} aria-hidden />
            {t.project.upload}
          </Button>
        </>
      ) : !docType ? (
        <span className="text-muted-foreground text-xs">{t.project.fromBooks}</span>
      ) : null}
      {upload.isError ? (
        <span className="text-danger-foreground w-full text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </li>
  );
}

/** Why something just left the project, in one optional tap: a file can be a signature or a
 * logo, which code then keeps out of every project until the owner undoes it here; anything can
 * be another job, not work, or something else said in a line. */
function WhyRejected({ rejected, onDone }: { rejected: Rejected; onDone: () => void }) {
  const { t } = useI18n();
  const words = t.project.why;
  const invalidate = useInvalidatePlacements();
  const [other, setOther] = useState(false);
  const [note, setNote] = useState("");
  const [signed, setSigned] = useState(false);
  const tell = useMutation({
    mutationFn: async ({ because, said }: { because: WhyReason | null; said?: string }) => {
      for (const link of rejected.links) await updateLink(link.id, { because, note: said || null });
    },
    onSuccess: (_, { because }) => {
      invalidate();
      // A signature reaches past this project: it stays undoable until the bar is closed.
      if (because === "signature" || because === null) setSigned(because !== null);
      else onDone();
    },
  });
  const reasons: WhyReason[] =
    rejected.kind === "file" ? ["signature", "another_job", "not_work", "other"] : ["another_job", "not_work", "other"];
  return (
    <div className="border-border bg-card flex flex-wrap items-center gap-2 rounded-2xl border px-3 py-2 text-sm" role="status">
      <span className="text-muted-foreground">{signed ? words.signed : words.ask}</span>
      {signed ? (
        <Button size="xs" variant="outline" className="rounded-full" disabled={tell.isPending} onClick={() => tell.mutate({ because: null })}>
          {words.undo}
        </Button>
      ) : other ? (
        <form
          className="flex min-w-0 flex-1 items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) tell.mutate({ because: "other", said: note.trim() });
          }}
        >
          <Input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={200}
            placeholder={words.placeholder}
            aria-label={words.other}
            className="h-8 min-w-0 flex-1"
            autoFocus
          />
          <Button size="xs" type="submit" disabled={tell.isPending || !note.trim()}>
            {words.send}
          </Button>
        </form>
      ) : (
        reasons.map((reason) => (
          <Button
            key={reason}
            size="xs"
            variant="outline"
            className="rounded-full"
            disabled={tell.isPending}
            onClick={() => (reason === "other" ? setOther(true) : tell.mutate({ because: reason }))}
          >
            {words[reason]}
          </Button>
        ))
      )}
      <IconButton
        icon={Cancel01Icon}
        label={signed ? words.done : words.skip}
        size="icon-xs"
        className="text-muted-foreground ms-auto"
        // Closed mid-answer, a signature would have no Undo left to show.
        disabled={tell.isPending}
        onClick={onDone}
      />
      {tell.isError ? (
        <span className="text-danger-foreground w-full text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </div>
  );
}

type WhyReason = NonNullable<Parameters<typeof updateLink>[1]["because"]>;

function Answer({
  waiting,
  pending,
  onAnswer,
}: {
  waiting: boolean;
  pending: boolean;
  onAnswer?: (answer: "confirmed" | "rejected") => void;
}) {
  const { t } = useI18n();
  return (
    <RowAnswer waiting={waiting}>
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

function RowMenu({
  onMove,
  trucks = [],
  current = null,
  onTruck,
  onProve,
}: {
  onMove: () => void;
  trucks?: Shipment[];
  current?: string | null;
  onTruck?: (shipmentId: string | null) => void;
  onProve?: () => void;
}) {
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
        {onTruck && trucks.length > 0 ? (
          <>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>{t.project.putOnTruck}</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {trucks.map((truck) => (
                  <DropdownMenuItem
                    key={truck.id}
                    className="font-mono"
                    disabled={truck.id === current}
                    onSelect={() => onTruck(truck.id)}
                  >
                    {plateOf(truck)}
                  </DropdownMenuItem>
                ))}
                {current ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => onTruck(null)}>{t.project.noTruck}</DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
          </>
        ) : null}
        {onProve ? <DropdownMenuItem onSelect={onProve}>{t.project.proof}</DropdownMenuItem> : null}
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
      // Moved, it is another job's: what the weekly learner reads of it.
      await updateLink(link.id, { status: "rejected", because: "another_job" });
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
