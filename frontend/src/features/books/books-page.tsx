"use client";

import { Search01Icon, Cancel01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { listInvoices, listParties, type Invoice, type Party } from "@/lib/api";
import { formatDays, formatMoney, isBeforeToday } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { usePagedList } from "@/lib/use-paged-list";
import { cn } from "@/lib/utils";

const PAGE = 50;
const VIEWS = ["collect", "pay", "firms"] as const;
/** A tab, or `both`: a firm's invoices on either side, shown when one is chosen from Firms. */
type View = (typeof VIEWS)[number] | "both";
type Side = Exclude<View, "firms">;
/** What each side of the books holds. An incoming e-invoice is left out: the books hold it again
 * as the purchase it was booked as, or it is not booked yet. */
const KINDS: Record<Side, Invoice["kind"][]> = {
  collect: ["sales"],
  pay: ["purchase", "expense"],
  both: ["sales", "purchase", "expense"],
};
// The chosen option is filled and in full ink; the others recede.
const SEGMENT = "text-muted-foreground aria-checked:bg-secondary aria-checked:text-foreground";

/**
 * The books as Tamga read them, read only: what clients owe, what the company owes, and the
 * firms. Open invoices come first, by the day they are due; a row opens the invoice in the
 * canvas, and a firm narrows the invoices to its own.
 */
export function BooksPage() {
  const { t } = useI18n();
  const [view, setView] = useState<View>("collect");
  const [shown, setShown] = useState<"open" | "all">("open");
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [firm, setFirm] = useState<Party | null>(null);

  const choose = (next: View) => {
    setView(next);
    setSearch("");
  };

  return (
    <div className="grid gap-4">
      <ToggleGroup
        type="single"
        value={view === "both" ? "" : view}
        spacing={1}
        className="w-full"
        aria-label={t.nav.books}
        onValueChange={(value) => {
          if (value) choose(value as View);
        }}
      >
        {VIEWS.map((value) => (
          <ToggleGroupItem key={value} value={value} className={cn(SEGMENT, "min-w-0 flex-1 px-2")}>
            <span className="truncate">{t.books.views[value]}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <HugeiconsIcon
            icon={Search01Icon}
            strokeWidth={1.75}
            className="text-muted-foreground pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2"
          />
          <Input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={view === "firms" ? t.books.searchFirms : t.books.search}
            aria-label={view === "firms" ? t.books.searchFirms : t.books.search}
            className="ps-9"
          />
        </div>
        {view === "firms" ? null : (
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={0}
            value={shown}
            onValueChange={(value) => {
              if (value === "open" || value === "all") setShown(value);
            }}
          >
            {(["open", "all"] as const).map((value) => (
              <ToggleGroupItem key={value} value={value} className={SEGMENT}>
                {t.books.shown[value]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        )}
      </div>

      {view !== "firms" && firm ? (
        <div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setFirm(null);
              if (view === "both") choose("collect");
            }}
            aria-label={t.books.everyFirm}
          >
            {t.books.onlyFirm(firm.name)}
            <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} />
          </Button>
        </div>
      ) : null}

      {view === "firms" ? (
        <Firms
          q={q}
          onChoose={(party) => {
            setFirm(party);
            choose("both");
          }}
        />
      ) : (
        <Invoices view={view} open={shown === "open"} q={q} firm={firm} />
      )}
    </div>
  );
}

function Invoices({ view, open, q, firm }: { view: Side; open: boolean; q: string; firm: Party | null }) {
  const { t } = useI18n();
  const invoices = usePagedList(
    ["invoices", { view, open, q, firm: firm?.id }],
    (page) =>
      listInvoices({
        kind: KINDS[view],
        outstanding: open ? true : undefined,
        sort: open ? "due_date" : "-issue_date",
        q: q || undefined,
        party_id: firm?.id,
        ...page,
      }),
    PAGE,
    (bill) => bill.id,
  );

  if (invoices.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!invoices.items) return <Skeleton className="h-40 rounded-2xl" />;
  if (invoices.items.length === 0)
    return <p className="text-muted-foreground py-10 text-center text-sm">{t.books.empty}</p>;
  return (
    <div className="grid gap-3">
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {invoices.items.map((bill) => (
          <InvoiceRow key={bill.id} bill={bill} bothSides={view === "both"} />
        ))}
      </ul>
      <ShowMore list={invoices} />
    </div>
  );
}

function ShowMore({ list }: { list: { more: (() => void) | null; loadingMore: boolean } }) {
  const { t } = useI18n();
  return list.more ? (
    <Button variant="ghost" className="justify-self-center" disabled={list.loadingMore} onClick={list.more}>
      {t.inbox.showMore}
    </Button>
  ) : null;
}

/** An invoice's row. With both sides of the books in one list, each row says which side it is on. */
function InvoiceRow({ bill, bothSides }: { bill: Invoice; bothSides: boolean }) {
  const { t, locale } = useI18n();
  const money = (amount: string | null) =>
    amount !== null && bill.currency ? formatMoney(Number(amount), bill.currency, locale) : null;
  const left = bill.remaining_amount !== null && !bill.cancelled ? Number(bill.remaining_amount) : null;
  const overdue = left !== null && left > 0 && bill.due_date !== null && isBeforeToday(bill.due_date);

  return (
    <li className="py-1 ps-2 pe-3">
      <CanvasLink
        view={{ kind: "invoice", id: bill.id }}
        className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex min-w-0 items-start gap-3 rounded-xl px-2 py-2.5 transition-colors"
      >
        <span className="min-w-0 flex-1">
          <span className="text-muted-foreground flex items-baseline gap-2 text-xs">
            <span className="font-mono">{bill.number ?? t.books.noNumber}</span>
            {bothSides || bill.kind === "expense" ? <span>{t.books.kinds[bill.kind]}</span> : null}
          </span>
          <span className="block text-base font-medium text-pretty break-words">{bill.party_name ?? "—"}</span>
          <span className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
            {bill.due_date ? <span>{t.books.dueOn(formatDays(bill.due_date, null, locale))}</span> : null}
            {overdue ? (
              <span className="text-warning-foreground inline-flex items-center gap-1.5">
                <span className="bg-warning size-1.5 rounded-full" aria-hidden />
                {t.books.overdue}
              </span>
            ) : null}
            {bill.cancelled ? <span className="text-danger-foreground">{t.books.cancelled}</span> : null}
          </span>
        </span>
        <span className="shrink-0 text-end tabular-nums">
          <span className={cn("block text-sm font-medium", bill.cancelled && "line-through")}>
            {money(bill.total_amount) ?? "—"}
          </span>
          {left !== null && left > 0 && bill.remaining_amount !== bill.total_amount ? (
            <span className="text-warning-foreground block text-xs">
              {t.books.left}: {money(bill.remaining_amount)}
            </span>
          ) : null}
        </span>
      </CanvasLink>
    </li>
  );
}

function Firms({ q, onChoose }: { q: string; onChoose: (party: Party) => void }) {
  const { t } = useI18n();
  const parties = usePagedList(
    ["parties", { q, list: "books" }],
    (page) => listParties({ q: q || undefined, ...page }),
    PAGE,
    (party) => party.id,
  );

  if (parties.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!parties.items) return <Skeleton className="h-40 rounded-2xl" />;
  if (parties.items.length === 0)
    return <p className="text-muted-foreground py-10 text-center text-sm">{t.books.empty}</p>;
  return (
    <div className="grid gap-3">
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {parties.items.map((party) => (
          <li key={party.id} className="py-1 ps-2 pe-3">
            <button
              type="button"
              onClick={() => onChoose(party)}
              className="hover:bg-accent/40 grid w-full min-w-0 rounded-xl px-2 py-2.5 text-start transition-colors"
            >
              <span className="text-base font-medium text-pretty break-words">{party.name}</span>
              <span className="text-muted-foreground text-xs">{t.books.firmKinds[party.kind]}</span>
            </button>
          </li>
        ))}
      </ul>
      <ShowMore list={parties} />
    </div>
  );
}
