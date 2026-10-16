"use client";

import { ArrowDownLeft01Icon, ArrowUpRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { usePaymentRun } from "@/components/payment-run";
import { Skeleton } from "@/components/ui/skeleton";
import { getHoldings, getPolicy, listInvoiceTotals, type InvoiceTotal } from "@/lib/api";
import { formatDays, formatMoney, formatMoneyShort, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** The calendar day after one the API wrote. */
function dayAfter(day: string): string {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return todayIso(new Date(year, month - 1, date + 1));
}

/**
 * Where the money stands, as a bank's app shows it: what the accounts hold, large, and under it
 * one quiet line of what comes in and what goes out by the next pay day. Nothing else: what waits
 * for you is on Payments, and the reserve in Settings. Money and the chat's brief show it; each
 * figure is the server's, and one with nothing known is left out (the balance shows a dash).
 */
export function MoneyGlance({ className }: { className?: string }) {
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
  if (policy.isPending || run.isPending || holdings.isPending) return <Skeleton className={cn("h-20 w-56 rounded-xl", className)} />;
  if (!policy.data || !currency) return null;

  // What the books say is owed by then, held bills included: a hold is no less a debt. Each
  // currency apart, joined: "₺340K + €12K".
  const owed = (totals: InvoiceTotal[] | undefined): string | null => {
    const amounts = (totals ?? []).filter((total) => total.currency && Number(owedOf(total) ?? 0) > 0);
    const words = amounts.map((total) => formatMoneyShort(Number(owedOf(total)), total.currency ?? currency, locale));
    return words.length > 0 ? words.join(" + ") : null;
  };
  // A total that failed to load is said so: never left out as if nothing were owed.
  const failed = incoming?.isError || outgoing?.isError || holdings.isError;
  const held = holdings.data && holdings.data.items.length > 0 ? Number(holdings.data.total) : null;
  const coming = owed(incoming?.data);
  const going = owed(outgoing?.data);
  const day = run.data ? formatDays(run.data.through, null, locale) : null;
  return (
    <div className={cn("grid gap-1", className)}>
      <p className="text-muted-foreground text-sm">{t.glance.available}</p>
      <p className="text-3xl font-semibold tracking-tight tabular-nums">
        {held === null ? "—" : formatMoney(held, currency, locale)}
      </p>
      {failed ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : null}
      {coming || going ? (
        <p className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm tabular-nums">
          {coming ? (
            <span className="inline-flex items-center gap-1">
              <HugeiconsIcon icon={ArrowDownLeft01Icon} strokeWidth={2} className="text-success size-3.5" aria-hidden />
              {t.glance.comingIn(coming)}
            </span>
          ) : null}
          {going ? (
            <span className="inline-flex items-center gap-1">
              <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={2} className="text-warning size-3.5" aria-hidden />
              {day ? t.glance.dueBy(going, day) : t.glance.dueToday(going)}
            </span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

/** What is still owed of a list's invoices as read (on the firm's account, less what the accounts
 * show paid), or the books' own remainder where nothing was read. */
function owedOf(total: { owed_amount?: string | null; remaining_amount: string | null }): string | null {
  return total.owed_amount ?? total.remaining_amount;
}
