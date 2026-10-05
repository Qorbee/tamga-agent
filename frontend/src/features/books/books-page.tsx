"use client";

import { Cancel01Icon, Search01Icon, SortByDown02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  getParty,
  listInvoices,
  listInvoiceTotals,
  listParties,
  type Invoice,
  type InvoiceTotal,
  type Party,
} from "@/lib/api";
import { formatDays, formatMoney, isBeforeToday, todayIso } from "@/lib/format";
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
const SHOWN = ["open", "overdue", "all"] as const;
type Shown = (typeof SHOWN)[number];
const SORTS = ["-issue_date", "issue_date", "due_date", "-total_amount"] as const;
type Sort = (typeof SORTS)[number];
/** Which firms the Firms tab lists: those that owe the company, those it owes, or every one. */
const BALANCES = ["receivable", "payable", "all"] as const;
type Balances = (typeof BALANCES)[number];
// The chosen option is filled and in full ink; the others recede.
const SEGMENT = "text-muted-foreground aria-checked:bg-secondary aria-checked:text-foreground";

/**
 * The books as Tamga read them, read only: what clients owe, what the company owes, and the
 * firms with the balance the books keep for each. Invoices come newest first unless the owner
 * sorts them otherwise; a row opens the invoice in the canvas, and a firm narrows the invoices to
 * its own.
 */
export function BooksPage() {
  const { t } = useI18n();
  const [view, setView] = useState<View>("collect");
  const [shown, setShown] = useState<Shown>("open");
  const [sort, setSort] = useState<Sort>("-issue_date");
  const [balances, setBalances] = useState<Balances>("all");
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  // The firm as chosen from the list, and as the server has it now: its balance moves with
  // the books.
  const [chosen, setChosen] = useState<Party | null>(null);
  const current = useQuery({
    queryKey: ["parties", "one", chosen?.id],
    queryFn: () => getParty(chosen!.id),
    enabled: chosen !== null,
    initialData: chosen ?? undefined,
  });
  const firm = chosen ? (current.data ?? chosen) : null;

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
        {view === "firms" ? (
          <Segments value={balances} options={BALANCES} words={t.books.balances} onChange={setBalances} />
        ) : (
          <>
            <Segments value={shown} options={SHOWN} words={t.books.shown} onChange={setShown} />
            <SortMenu value={sort} onChange={setSort} />
          </>
        )}
      </div>

      {view !== "firms" && firm ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setChosen(null);
              if (view === "both") choose("collect");
            }}
            aria-label={t.books.everyFirm}
          >
            {t.books.onlyFirm(firm.name)}
            <HugeiconsIcon icon={Cancel01Icon} strokeWidth={1.75} />
          </Button>
          <Balance party={firm} />
        </div>
      ) : null}

      {view === "firms" ? (
        <Firms
          q={q}
          balances={balances}
          onChoose={(party) => {
            setChosen(party);
            choose("both");
          }}
        />
      ) : (
        <Invoices view={view} shown={shown} sort={sort} q={q} firm={firm} />
      )}
    </div>
  );
}

function Segments<T extends string>({
  value,
  options,
  words,
  onChange,
}: {
  value: T;
  options: readonly T[];
  words: Record<T, string>;
  onChange: (next: T) => void;
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      spacing={0}
      value={value}
      onValueChange={(next) => {
        const option = options.find((each) => each === next);
        if (option) onChange(option);
      }}
    >
      {options.map((option) => (
        <ToggleGroupItem key={option} value={option} className={SEGMENT}>
          {words[option]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function SortMenu({ value, onChange }: { value: Sort; onChange: (next: Sort) => void }) {
  const { t } = useI18n();
  const words = t.books.sort;
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" aria-label={`${words.label}: ${words.options[value]}`}>
          <HugeiconsIcon icon={SortByDown02Icon} strokeWidth={1.75} />
          {words.options[value]}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => {
            const option = SORTS.find((each) => each === next);
            if (option) onChange(option);
          }}
        >
          {SORTS.map((option) => (
            <DropdownMenuRadioItem key={option} value={option}>
              {words.options[option]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Invoices({
  view,
  shown,
  sort,
  q,
  firm,
}: {
  view: Side;
  shown: Shown;
  sort: Sort;
  q: string;
  firm: Party | null;
}) {
  const { t } = useI18n();
  const filters = {
    kind: KINDS[view],
    outstanding: shown === "all" ? undefined : true,
    due_before: shown === "overdue" ? todayIso() : undefined,
    q: q || undefined,
    party_id: firm?.id,
  };
  const invoices = usePagedList(
    ["invoices", { ...filters, sort }],
    (page) => listInvoices({ ...filters, sort, ...page }),
    PAGE,
    (bill) => bill.id,
  );
  const totals = useQuery({
    queryKey: ["invoice-totals", filters],
    queryFn: () => listInvoiceTotals(filters),
  });

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
      {totals.isError ? (
        <p className="text-muted-foreground px-1 text-sm" role="status">
          {t.books.totals.unavailable}
        </p>
      ) : totals.data ? (
        <Totals totals={totals.data} open={shown !== "all"} />
      ) : null}
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {invoices.items.map((bill) => (
          <InvoiceRow key={bill.id} bill={bill} bothSides={view === "both"} />
        ))}
      </ul>
      <ShowMore list={invoices} />
    </div>
  );
}

/** What the list adds up to, one figure per currency: what is left of open ones, else the total. */
function Totals({ totals, open }: { totals: InvoiceTotal[]; open: boolean }) {
  const { t, locale } = useI18n();
  const words = t.books.totals;
  return (
    <ul className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 px-1 text-sm">
      {totals.map((total) => {
        const amount = open ? total.remaining_amount : total.total_amount;
        const figure =
          total.currency && amount !== null ? formatMoney(Number(amount), total.currency, locale) : null;
        return (
          <li key={total.currency ?? "none"} className="tabular-nums">
            <span className="text-foreground font-medium">
              {figure ? (open ? words.left(figure) : words.total(figure)) : words.unknown}
            </span>
            <span> · {words.invoices(total.count)}</span>
          </li>
        );
      })}
    </ul>
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
            {bill.issue_date ? <span>{t.books.issuedOn(formatDays(bill.issue_date, null, locale))}</span> : null}
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

/** A firm's balance as the books keep it: what it owes the company, or what the company owes it. */
function Balance({ party, className }: { party: Party; className?: string }) {
  const { t, locale } = useI18n();
  const words = t.books.balance;
  if (party.balance === null || party.balance_currency === null) return null;
  const amount = Number(party.balance);
  const figure = formatMoney(Math.abs(amount), party.balance_currency, locale);
  const said = amount > 0 ? words.receivable(figure) : amount < 0 ? words.payable(figure) : words.settled;
  return (
    <span className={cn("grid min-w-0 text-sm tabular-nums", className)}>
      <span className={cn("font-medium", amount === 0 && "text-muted-foreground")}>{said}</span>
      {party.balance_on ? (
        <span className="text-muted-foreground text-xs">{words.asOf(formatDays(party.balance_on, null, locale))}</span>
      ) : null}
    </span>
  );
}

function Firms({ q, balances, onChoose }: { q: string; balances: Balances; onChoose: (party: Party) => void }) {
  const { t } = useI18n();
  const filters =
    balances === "all"
      ? { sort: "source" as const }
      : { balance_side: balances, sort: balances === "receivable" ? ("-balance" as const) : ("balance" as const) };
  const parties = usePagedList(
    ["parties", { q, list: "books", ...filters }],
    (page) => listParties({ q: q || undefined, ...filters, ...page }),
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
              className="hover:bg-accent/40 flex w-full min-w-0 flex-col gap-1 rounded-xl px-2 py-2.5 text-start transition-colors sm:flex-row sm:items-start sm:gap-3"
            >
              <span className="grid min-w-0 flex-1">
                <span className="text-base font-medium text-pretty break-words">{party.name}</span>
                <span className="text-muted-foreground text-xs">{t.books.firmKinds[party.kind]}</span>
              </span>
              <Balance party={party} className="sm:max-w-56 sm:shrink-0 sm:text-end" />
            </button>
          </li>
        ))}
      </ul>
      <ShowMore list={parties} />
    </div>
  );
}
