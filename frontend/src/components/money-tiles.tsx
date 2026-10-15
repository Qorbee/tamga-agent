"use client";

import { useQueries, useQuery } from "@tanstack/react-query";

import { usePaymentRun } from "@/components/payment-run";
import { Skeleton } from "@/components/ui/skeleton";
import { getHoldings, getPolicy, listDecisions, listInvoiceTotals, type InvoiceTotal } from "@/lib/api";
import { formatDays, formatMoneyCompact, formatMoneyShort, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** The calendar day after one the API wrote. */
function dayAfter(day: string): string {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return todayIso(new Date(year, month - 1, date + 1));
}

/**
 * Where the money stands, in four figures (the owner's mockup, Oct 13): what the accounts hold,
 * what is due by the next pay day, what clients owe by then, and how many bills wait for the owner.
 * Each is the server's figure; a tile with nothing known says so with a dash.
 */
export function MoneyTiles({ className }: { className?: string }) {
  const { t, locale } = useI18n();
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const currency = policy.data?.currency;
  const run = usePaymentRun();
  const holdings = useQuery({
    queryKey: ["money-holdings", currency],
    queryFn: () => getHoldings(currency ?? ""),
    enabled: Boolean(currency),
  });
  // The run's last day counts: the books' cutoff is strict, so it is the day after.
  const cutoff = run.data ? dayAfter(run.data.through) : undefined;
  const [incoming, outgoing] = useQueries({
    queries: (["receivable", "payable"] as const).map((direction) => ({
      queryKey: ["invoice-totals", { direction, outstanding: true, due_before: cutoff }],
      queryFn: () => listInvoiceTotals({ direction, outstanding: true, due_before: cutoff }),
      enabled: Boolean(cutoff),
    })),
  });
  const waiting = useQuery({
    queryKey: ["decisions", { view: "count-waiting" }],
    queryFn: () => listDecisions({ state: ["awaiting_owner"], limit: 1 }),
  });
  if (policy.isPending || run.isPending || holdings.isPending) return <Skeleton className={cn("h-20 rounded-2xl", className)} />;
  if (!currency) return null;

  // Each figure in full and short ("₺4.7M"): a narrow tile shows the short one, never a cut one.
  const money = (amount: number, unit = currency) => ({
    full: formatMoneyShort(amount, unit, locale),
    short: formatMoneyCompact(amount, unit, locale, "symbol"),
  });
  const none = { full: "—", short: "—" };
  // What the books say is owed by then, held bills included: a hold is no less a debt.
  const totalOf = (totals: InvoiceTotal[] | undefined) => {
    const amounts = (totals ?? []).filter((total) => total.currency && Number(owedOf(total) ?? 0) > 0);
    if (amounts.length === 0) return none;
    const each = amounts.map((total) => money(Number(owedOf(total)), total.currency ?? currency));
    return { full: each.map((one) => one.full).join(" · "), short: each.map((one) => one.short).join(" · ") };
  };
  const count = waiting.data ? String(waiting.data.total) : "—";
  const tiles: { label: string; value: { full: string; short: string }; tone?: string }[] = [
    {
      label: t.tiles.available,
      value: holdings.data && holdings.data.items.length > 0 ? money(Number(holdings.data.total)) : none,
    },
    {
      label: run.data ? t.tiles.dueBy(formatDays(run.data.through, null, locale)) : t.tiles.due,
      value: totalOf(outgoing?.data),
    },
    { label: t.tiles.expected, value: totalOf(incoming?.data) },
    {
      label: t.tiles.toApprove,
      // Unknown until it loads: never a zero that says nothing waits.
      value: { full: count, short: count },
      tone: (waiting.data?.total ?? 0) > 0 ? "text-danger-foreground" : undefined,
    },
  ];
  return (
    // Four in a row where the page is wide, two by two where it is not (a phone, or a page beside
    // the World and the canvas): by the page's own width, so no figure is ever cut.
    <div className={cn("@container", className)}>
    <dl className="grid grid-cols-2 gap-2 @2xl:grid-cols-4">
      {tiles.map((tile) => (
        <div key={tile.label} className="border-border bg-card @container min-w-0 rounded-2xl border px-3.5 py-3">
          <dt className="text-muted-foreground truncate text-[11px] font-medium tracking-[0.08em] uppercase">{tile.label}</dt>
          <dd className={cn("mt-1 truncate text-lg font-medium tabular-nums", tile.tone)} title={tile.value.full}>
            <span className="hidden @[13rem]:inline">{tile.value.full}</span>
            <span className="@[13rem]:hidden">{tile.value.short}</span>
          </dd>
        </div>
      ))}
    </dl>
    </div>
  );
}

/** What is still owed of a list's invoices as read (on the firm's account, less what the accounts
 * show paid), or the books' own remainder where nothing was read. */
function owedOf(total: { owed_amount?: string | null; remaining_amount: string | null }): string | null {
  return total.owed_amount ?? total.remaining_amount;
}
