"use client";

import { Invoice01Icon, Link01Icon, Mail01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { fileIcon } from "@/components/file-icon";
import { PopSheet } from "@/components/pop-sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useObjectUrl } from "@/hooks/use-object-url";
import { getDocument, getDocumentFile, getInvoice, getMessage } from "@/lib/api";
import type { CanvasView } from "@/lib/canvas-view";
import { formatDate, formatMoney } from "@/lib/format";
import type { Locale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** An item something was read from, as far as the row naming it knows it. */
export type Evidence = {
  item_kind: string;
  item_id: string;
  label?: string | null;
  writer?: string | null;
  at?: string | null;
};

// The record each kind of item opens as; a truck's position opens none.
const VIEWS: Partial<Record<string, CanvasView["kind"]>> = {
  message: "mail",
  document: "document",
  invoice: "invoice",
};

// How long the pointer rests before the card opens, and lingers before it closes.
const OPEN_AFTER = 180;
const CLOSE_AFTER = 220;

/**
 * Where something was read, as a link's icon with a count of how many said it. Under the pointer,
 * or tapped where there are several, a card lists each source as it is: a mail with its writer
 * and first words, a file by its name and what it is (a photo as a picture), a bill with its firm
 * and amount. A tap on one opens it beside the page. With one source, a tap opens it at once. On
 * a phone the card is a sheet from the screen's foot.
 */
export function EvidenceLink({ items, label, small = false }: { items: Evidence[]; label: string; small?: boolean }) {
  const { t } = useI18n();
  const unique = items.filter((item, at) => items.findIndex((other) => other.item_id === item.item_id) === at);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [first] = unique;
  if (!first) return null;
  const later = (next: boolean, wait: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(next), wait);
  };
  const hover = {
    onPointerEnter: (event: React.PointerEvent) => event.pointerType === "mouse" && later(true, OPEN_AFTER),
    onPointerLeave: (event: React.PointerEvent) => event.pointerType === "mouse" && later(false, CLOSE_AFTER),
  };
  const firstView = VIEWS[first.item_kind];
  const look = cn(
    "text-muted-foreground inline-flex shrink-0 items-center justify-center gap-0.5 rounded-full",
    small ? "h-6 min-w-6 px-1" : "h-8 min-w-8 px-1.5",
  );
  const mark = (
    <>
      <HugeiconsIcon icon={Link01Icon} strokeWidth={1.75} className={small ? "size-3.5" : "size-4"} aria-hidden />
      {unique.length > 1 ? <span className="text-[0.7rem] tabular-nums">+{unique.length - 1}</span> : null}
    </>
  );
  const single = unique.length === 1 && Boolean(firstView);
  const trigger =
    single && firstView ? (
      <CanvasLink view={{ kind: firstView, id: first.item_id }} aria-label={label} className={cn(look, "hover:text-foreground hover:bg-accent/60")} {...hover}>
        {mark}
      </CanvasLink>
    ) : (
      <button
        type="button"
        aria-label={label}
        onClick={() => timer.current && clearTimeout(timer.current)}
        className={cn(look, "hover:text-foreground hover:bg-accent/60 aria-expanded:bg-accent/60 aria-expanded:text-foreground")}
        {...hover}
      >
        {mark}
      </button>
    );
  const heading = `${label} · ${t.project.sources(unique.length)}`;
  return (
    <PopSheet
      open={open}
      onOpenChange={setOpen}
      trigger={trigger}
      anchor={single}
      title={heading}
      align="end"
      className="w-96 p-1.5"
      contentProps={{ onOpenAutoFocus: (event) => event.preventDefault(), ...hover }}
    >
      {/* The sheet on a phone heads the list itself. */}
      <p className="text-muted-foreground px-2.5 pt-1.5 pb-1 text-xs text-pretty first-letter:uppercase max-md:hidden">{heading}</p>
      <ul className="grid gap-0.5 md:max-h-[min(60vh,28rem)] md:overflow-y-auto">
        {unique.map((item) => (
          <li key={item.item_id}>
            <Source item={item} onOpen={() => setOpen(false)} />
          </li>
        ))}
      </ul>
    </PopSheet>
  );
}

/** One source as a row: what it is, read from its record, and a tap away. */
function Source({ item, onOpen }: { item: Evidence; onOpen: () => void }) {
  const view = VIEWS[item.item_kind];
  const body =
    item.item_kind === "message" ? (
      <MailSource item={item} />
    ) : item.item_kind === "document" ? (
      <FileSource item={item} />
    ) : item.item_kind === "invoice" ? (
      <BillSource item={item} />
    ) : (
      <Row title={item.label ?? "—"} meta={null} />
    );
  return view ? (
    <CanvasLink view={{ kind: view, id: item.item_id }} onClick={onOpen} className="hover:bg-accent/60 active:bg-accent/60 block rounded-xl px-2.5 py-2 max-md:py-3">
      {body}
    </CanvasLink>
  ) : (
    <div className="px-2.5 py-2">{body}</div>
  );
}

function Row({
  icon,
  title,
  meta,
  more,
  picture,
}: {
  icon?: Parameters<typeof HugeiconsIcon>[0]["icon"];
  title: ReactNode;
  meta: ReactNode;
  more?: ReactNode;
  picture?: ReactNode;
}) {
  return (
    // As tall as a row once read, so the card keeps its height while its rows load.
    <span className="flex min-h-12 items-start gap-2.5">
      {icon ? <HugeiconsIcon icon={icon} strokeWidth={1.75} className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden /> : null}
      <span className="grid min-w-0 flex-1 gap-0.5">
        <span className="truncate text-sm font-medium">{title}</span>
        {meta ? <span className="text-muted-foreground truncate text-xs">{meta}</span> : null}
        {more ? <span className="text-muted-foreground line-clamp-2 text-xs text-pretty [overflow-wrap:anywhere]">{more}</span> : null}
      </span>
      {picture}
    </span>
  );
}

function day(at: string | null | undefined, locale: Locale): string | null {
  return at ? formatDate(new Date(at), locale) : null;
}

function MailSource({ item }: { item: Evidence }) {
  const { t, locale } = useI18n();
  const mail = useQuery({ queryKey: ["message", item.item_id], queryFn: () => getMessage(item.item_id), staleTime: 60_000 });
  const writer = mail.data ? (mail.data.sender_name ?? mail.data.sender) : item.writer;
  const when = day(mail.data?.sent_at ?? item.at, locale);
  return (
    <Row
      icon={Mail01Icon}
      title={mail.data ? (mail.data.subject ?? t.project.noSubject) : (item.label ?? t.project.noSubject)}
      meta={[writer, when].filter(Boolean).join(" · ")}
      more={mail.data ? mail.data.snippet : mail.isPending ? <Skeleton className="h-3 w-48" /> : null}
    />
  );
}

function FileSource({ item }: { item: Evidence }) {
  const { locale } = useI18n();
  const file = useQuery({ queryKey: ["document", item.item_id], queryFn: () => getDocument(item.item_id), staleTime: 60_000 });
  const image = file.data?.preview === "image";
  return (
    <Row
      icon={fileIcon(file.data?.preview)}
      title={file.data?.filename ?? item.label ?? "—"}
      meta={[file.data?.doc_type, day(item.at ?? file.data?.created_at, locale)].filter(Boolean).join(" · ")}
      more={file.data?.description || null}
      picture={image ? <Picture id={item.item_id} /> : null}
    />
  );
}

/** A photo's own picture, small: a CMR or a delivery photo reads at a glance. */
function Picture({ id }: { id: string }) {
  const bytes = useQuery({ queryKey: ["document-file", id], queryFn: () => getDocumentFile(id), staleTime: Infinity });
  // A file the backend would not vouch for as an image arrives as a download, never drawn.
  const url = useObjectUrl(bytes.data?.type.startsWith("image/") ? bytes.data : undefined);
  return (
    <span className="bg-muted border-border size-12 shrink-0 overflow-hidden rounded-lg border">
      {url ? (
        // A blob of a verified image: next/image cannot take one.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="" className="size-full object-cover" />
      ) : null}
    </span>
  );
}

function BillSource({ item }: { item: Evidence }) {
  const { t, locale } = useI18n();
  const bill = useQuery({ queryKey: ["invoice", item.item_id], queryFn: () => getInvoice(item.item_id), retry: false });
  const amount =
    bill.data?.total_amount != null && bill.data.currency ? formatMoney(Number(bill.data.total_amount), bill.data.currency, locale) : null;
  return (
    <Row
      icon={Invoice01Icon}
      title={[bill.data?.number ?? item.label ?? t.books.noNumber, bill.data?.party_name].filter(Boolean).join(" · ")}
      meta={[bill.data ? t.books.kinds[bill.data.kind] : null, amount, day(bill.data?.issue_date ? `${bill.data.issue_date}T12:00:00` : item.at, locale)]
        .filter(Boolean)
        .join(" · ")}
      more={bill.data?.about ?? null}
    />
  );
}
