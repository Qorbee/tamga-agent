"use client";

import {
  Alert02Icon,
  ArrowDownLeft01Icon,
  ArrowUpRight01Icon,
  Cancel01Icon,
  CheckmarkCircle02Icon,
  Invoice01Icon,
  Search01Icon,
  SortByDown02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { IconButton } from "@/components/icon-button";
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
/** What a figure is to the company, and its colour from the palette's soft tones: money coming in
 * is sage, money going out ochre, what is late terracotta; anything else stays neutral. */
type Tone = "in" | "out" | "late" | "plain";
const TONES: Record<Tone, { card: string; chip: string; ink: string; pill: string; icon: IconSvgElement }> = {
  in: {
    card: "bg-success-soft",
    chip: "bg-card text-success-foreground",
    ink: "text-success-foreground",
    pill: "bg-success-soft text-success-foreground",
    icon: ArrowDownLeft01Icon,
  },
  out: {
    card: "bg-warning-soft",
    chip: "bg-card text-warning-foreground",
    ink: "text-warning-foreground",
    pill: "bg-warning-soft text-warning-foreground",
    icon: ArrowUpRight01Icon,
  },
  late: {
    card: "bg-danger-soft",
    chip: "bg-card text-danger-foreground",
    ink: "text-danger-foreground",
    pill: "bg-danger-soft text-danger-foreground",
    icon: Alert02Icon,
  },
  plain: {
    card: "bg-card border-border border",
    chip: "bg-muted text-muted-foreground",
    ink: "text-muted-foreground",
    pill: "bg-muted text-muted-foreground",
    icon: CheckmarkCircle02Icon,
  },
};
const SIDE_TONE: Record<Exclude<Side, "both">, Tone> = { collect: "in", pay: "out" };

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
        <FirmSummary
          party={firm}
          view={view}
          onClose={() => {
            setChosen(null);
            if (view === "both") choose("collect");
          }}
        />
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
      {view === "both" ? null : totals.isError ? (
        <p className="text-muted-foreground px-1 text-sm" role="status">
          {t.books.totals.unavailable}
        </p>
      ) : totals.data ? (
        <Totals totals={totals.data} side={view} shown={shown} />
      ) : null}
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {invoices.items.map((bill) => (
          <InvoiceRow key={bill.id} bill={bill} bothSides={view === "both"} ofFirm={firm !== null} />
        ))}
      </ul>
      <ShowMore list={invoices} />
    </div>
  );
}

/** What the list adds up to, a card per currency, never summed across them: what is left of the
 * open ones, coloured by the side of the books (or as late, for the overdue), else the total. */
function Totals({ totals, side, shown }: { totals: InvoiceTotal[]; side: Exclude<Side, "both">; shown: Shown }) {
  const { t, locale } = useI18n();
  const words = t.books.totals;
  const open = shown !== "all";
  const tone: Tone = shown === "overdue" ? "late" : SIDE_TONE[side];
  const label = words.labels[shown][side];
  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2">
      {totals.map((total) => {
        const amount = open ? total.remaining_amount : total.total_amount;
        const left = total.remaining_amount;
        const money = (value: string | null) =>
          value !== null && total.currency ? formatMoney(Number(value), total.currency, locale) : null;
        const detail = [
          !open && left !== null && Number(left) > 0 ? words.left(money(left) ?? "") : null,
          words.invoices(total.count),
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          <Stat
            key={total.currency ?? "none"}
            tone={tone}
            label={label}
            figure={money(amount) ?? words.unknown}
            detail={detail}
          />
        );
      })}
    </ul>
  );
}

/** One figure as a banking app shows it: a tinted card, an icon for which way the money goes, the
 * amount large, and a line of what it is made of. */
function Stat({ tone, label, figure, detail }: { tone: Tone; label: string; figure: string; detail?: string }) {
  const style = TONES[tone];
  return (
    <li className={cn("flex min-w-0 items-center gap-3 rounded-2xl px-3 py-3", style.card)}>
      <span className={cn("grid size-10 shrink-0 place-items-center rounded-full", style.chip)} aria-hidden>
        <HugeiconsIcon icon={tone === "plain" ? Invoice01Icon : style.icon} strokeWidth={1.75} className="size-5" />
      </span>
      <span className="grid min-w-0">
        <span className={cn("text-xs font-medium", style.ink)}>{label}</span>
        <span className="text-foreground truncate text-lg leading-tight font-semibold tabular-nums">{figure}</span>
        {detail ? <span className="text-muted-foreground text-xs text-pretty">{detail}</span> : null}
      </span>
    </li>
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

/** An invoice's row, as a banking app lists a payment: its number and amount on one line, then
 * the firm across the row, its days, and pills for what needs seeing. In one firm's list the
 * firm is the page's, so the row names the kind of invoice instead. */
function InvoiceRow({ bill, bothSides, ofFirm }: { bill: Invoice; bothSides: boolean; ofFirm: boolean }) {
  const { t, locale } = useI18n();
  const money = (amount: string | null) =>
    amount !== null && bill.currency ? formatMoney(Number(amount), bill.currency, locale) : null;
  const left = bill.remaining_amount !== null && !bill.cancelled ? Number(bill.remaining_amount) : null;
  const overdue = left !== null && left > 0 && bill.due_date !== null && isBeforeToday(bill.due_date);
  const paid = left === 0;
  const partly = left !== null && left > 0 && bill.remaining_amount !== bill.total_amount;
  const kind = bothSides || bill.kind === "expense" ? t.books.kinds[bill.kind] : null;

  return (
    <li className="py-1 ps-2 pe-3">
      <CanvasLink
        view={{ kind: "invoice", id: bill.id }}
        className="hover:bg-accent/40 aria-[current=true]:bg-secondary grid min-w-0 gap-0.5 rounded-xl px-2 py-2.5 transition-colors"
      >
        <span className="flex min-w-0 items-baseline justify-between gap-3">
          <span className="text-muted-foreground min-w-0 truncate font-mono text-xs">
            {bill.number ?? t.books.noNumber}
          </span>
          <span
            className={cn(
              "shrink-0 text-base font-semibold tabular-nums",
              bill.cancelled && "text-muted-foreground line-through",
            )}
          >
            {money(bill.total_amount) ?? "—"}
          </span>
        </span>
        <span className="text-base font-medium text-pretty break-words">
          {ofFirm ? (t.books.kinds[bill.kind] ?? "—") : (bill.party_name ?? "—")}
        </span>
        <span className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
          {kind && !ofFirm ? <span>{kind}</span> : null}
          {bill.issue_date ? <span>{t.books.issuedOn(formatDays(bill.issue_date, null, locale))}</span> : null}
          {bill.due_date ? <span>{t.books.dueOn(formatDays(bill.due_date, null, locale))}</span> : null}
        </span>
        {overdue || paid || partly || bill.cancelled ? (
          <span className="mt-1 flex flex-wrap gap-1.5">
            {overdue ? <Pill tone="late" icon={Alert02Icon} label={t.books.overdue} /> : null}
            {partly ? <Pill tone="out" label={t.books.totals.left(money(bill.remaining_amount) ?? "")} /> : null}
            {paid ? <Pill tone="in" icon={CheckmarkCircle02Icon} label={t.books.paid} /> : null}
            {bill.cancelled ? <Pill tone="plain" label={t.books.cancelled} /> : null}
          </span>
        ) : null}
      </CanvasLink>
    </li>
  );
}

/** A small status, coloured as its figure would be. */
function Pill({ tone, label, icon }: { tone: Tone; label: string; icon?: IconSvgElement }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONES[tone].pill,
      )}
    >
      {icon ? <HugeiconsIcon icon={icon} strokeWidth={2} className="size-3.5" aria-hidden /> : null}
      {label}
    </span>
  );
}

/** A firm's balance as the books keep it, as a pill: what it owes the company (sage), what the
 * company owes it (ochre), or a settled account. Nothing while the books gave none. */
function BalancePill({ party, large = false }: { party: Party; large?: boolean }) {
  const { t, locale } = useI18n();
  const words = t.books.balance;
  if (party.balance === null || party.balance_currency === null) return null;
  const amount = Number(party.balance);
  const figure = formatMoney(Math.abs(amount), party.balance_currency, locale);
  const tone: Tone = amount > 0 ? "in" : amount < 0 ? "out" : "plain";
  const said = amount > 0 ? words.receivable(figure) : amount < 0 ? words.payable(figure) : words.settled;
  return (
    <span
      className={cn(
        "inline-flex w-fit max-w-full items-center gap-1.5 rounded-full font-medium tabular-nums",
        large ? "px-3 py-1.5 text-base" : "px-2.5 py-1 text-sm",
        TONES[tone].pill,
      )}
    >
      <HugeiconsIcon icon={TONES[tone].icon} strokeWidth={2} className={large ? "size-4.5" : "size-4"} aria-hidden />
      <span className="truncate">{said}</span>
    </span>
  );
}

/** The firm a list is narrowed to: its balance in the books, and on each side of the books what
 * its invoices add up to, what was paid of them and what is left. */
function FirmSummary({ party, view, onClose }: { party: Party; view: Side; onClose: () => void }) {
  const { t, locale } = useI18n();
  const words = t.books.totals;
  const sides = view === "both" ? (["collect", "pay"] as const) : ([view] as const);
  const totals = useQuery({
    queryKey: ["invoice-totals", { party_id: party.id, firm: true }],
    queryFn: async () => {
      const [collect, pay] = await Promise.all([
        listInvoiceTotals({ party_id: party.id, kind: KINDS.collect }),
        listInvoiceTotals({ party_id: party.id, kind: KINDS.pay }),
      ]);
      return { collect, pay };
    },
  });
  return (
    <section className="border-border bg-card grid gap-3 rounded-2xl border p-3 sm:p-4" aria-label={party.name}>
      <div className="flex items-start gap-3">
        <div className="grid min-w-0 flex-1 gap-1.5">
          <h2 className="text-lg leading-tight font-semibold text-pretty break-words">{party.name}</h2>
          <BalancePill party={party} large />
          {party.balance_on ? (
            <span className="text-muted-foreground text-xs">
              {t.books.balance.asOf(formatDays(party.balance_on, null, locale))}
            </span>
          ) : null}
        </div>
        <IconButton label={t.books.everyFirm} icon={Cancel01Icon} onClick={onClose} />
      </div>
      {totals.isError ? (
        <p className="text-muted-foreground text-sm" role="status">
          {words.unavailable}
        </p>
      ) : totals.data ? (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2">
          {sides.flatMap((side) =>
            totals.data[side].map((total) => {
              const money = (value: string | null) =>
                value !== null && total.currency ? formatMoney(Number(value), total.currency, locale) : null;
              // What was invoiced of those not cancelled: what was paid of them and what is left.
              const invoiced =
                total.paid_amount !== null && total.remaining_amount !== null
                  ? String(Number(total.paid_amount) + Number(total.remaining_amount))
                  : null;
              const left = Number(total.remaining_amount ?? 0);
              return (
                <Stat
                  key={`${side}-${total.currency ?? "none"}`}
                  tone={left > 0 ? SIDE_TONE[side] : "plain"}
                  label={words.labels.open[side]}
                  figure={money(total.remaining_amount) ?? words.unknown}
                  detail={[
                    invoiced !== null
                      ? words.paidOf(money(total.paid_amount) ?? "", money(invoiced) ?? "")
                      : null,
                    words.invoices(total.count),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
              );
            }),
          )}
        </ul>
      ) : (
        <Skeleton className="h-16 rounded-2xl" />
      )}
    </section>
  );
}

function Firms({ q, balances, onChoose }: { q: string; balances: Balances; onChoose: (party: Party) => void }) {
  const { t, locale } = useI18n();
  const filters =
    balances === "all"
      ? { sort: "open" as const }
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
  // One day above the list when every firm's balance is of that day; else each says its own.
  const days = new Set(parties.items.map((party) => party.balance_on));
  const shared = days.size === 1 ? parties.items[0].balance_on : null;
  return (
    <div className="grid gap-3">
      {shared ? (
        <p className="text-muted-foreground px-1 text-xs">{t.books.balance.asOf(formatDays(shared, null, locale))}</p>
      ) : null}
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {parties.items.map((party) => (
          <li key={party.id} className="py-1 ps-2 pe-3">
            <button
              type="button"
              onClick={() => onChoose(party)}
              className="hover:bg-accent/40 flex w-full min-w-0 flex-col gap-1.5 rounded-xl px-2 py-2.5 text-start transition-colors sm:flex-row sm:items-center sm:gap-3"
            >
              <span className="grid min-w-0 flex-1">
                <span className="text-base font-medium text-pretty break-words">{party.name}</span>
                <span className="text-muted-foreground text-xs">{t.books.firmKinds[party.kind]}</span>
              </span>
              <span className="grid gap-0.5 sm:max-w-64 sm:shrink-0 sm:justify-items-end">
                <BalancePill party={party} />
                {!shared && party.balance_on && party.balance !== null ? (
                  <span className="text-muted-foreground text-xs">
                    {t.books.balance.asOf(formatDays(party.balance_on, null, locale))}
                  </span>
                ) : null}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <ShowMore list={parties} />
    </div>
  );
}
