"use client";

import { useQueries, useQuery } from "@tanstack/react-query";

import { usePaymentRun } from "@/components/payment-run";
import { Skeleton } from "@/components/ui/skeleton";
import { getHoldings, getPolicy, listDecisions, listInvoiceTotals, type InvoiceTotal } from "@/lib/api";
import { useRuleWords } from "@/components/policy-settings";
import { formatDays, formatMoneyShort, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** The calendar day after one the API wrote. */
function dayAfter(day: string): string {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return todayIso(new Date(year, month - 1, date + 1));
}

/**
 * Where the money stands, as rows on one surface: what the accounts hold, what is due by the next
 * pay day, what clients owe by then, what waits for you, and the reserve in force. A label wraps
 * as long as its language needs; each currency's figure stands on a line of its own, right-aligned,
 * never cut. Two columns where the page is wide. The home, Payments, Money and the chat's brief
 * show the same rows. Each figure is the server's; one with nothing known shows a dash.
 */
export function MoneySummary({ className }: { className?: string }) {
  const { t, locale } = useI18n();
  const ruleWords = useRuleWords();
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
  if (policy.isPending || run.isPending || holdings.isPending) return <Skeleton className={cn("h-40 rounded-2xl", className)} />;
  if (!policy.data || !currency) return null;

  // What the books say is owed by then, held bills included: a hold is no less a debt. One line
  // a currency.
  const owed = (totals: InvoiceTotal[] | undefined): string[] => {
    const amounts = (totals ?? []).filter((total) => total.currency && Number(owedOf(total) ?? 0) > 0);
    return amounts.map((total) => formatMoneyShort(Number(owedOf(total)), total.currency ?? currency, locale));
  };
  const count = waiting.data?.total;
  const rows: { label: string; figures: string[]; tone?: string }[] = [
    {
      label: t.tiles.available,
      figures:
        holdings.data && holdings.data.items.length > 0 ? [formatMoneyShort(Number(holdings.data.total), currency, locale)] : [],
    },
    {
      label: run.data ? t.tiles.dueBy(formatDays(run.data.through, null, locale)) : t.tiles.due,
      figures: owed(outgoing?.data),
    },
    { label: t.tiles.expected, figures: owed(incoming?.data) },
    {
      label: t.tiles.toApprove,
      // Unknown until it loads: never a zero that says nothing waits.
      figures: count === undefined ? [] : [String(count)],
      tone: (count ?? 0) > 0 ? "text-danger-foreground" : undefined,
    },
    { label: t.tiles.reserve, figures: [ruleWords(policy.data.reserve, currency).name] },
  ];
  return (
    <div className={cn("@container", className)}>
      <dl className="border-border bg-card grid grid-cols-1 overflow-hidden rounded-2xl border @2xl:grid-cols-2">
        {rows.map((row) => (
          <div
            key={row.label}
            className="border-border flex items-start justify-between gap-4 border-b px-4 py-3 last:border-b-0 @2xl:[&:nth-last-child(2):nth-child(odd)]:border-b-0 @2xl:odd:border-e"
          >
            <dt className="text-muted-foreground min-w-0 text-sm text-pretty">{row.label}</dt>
            <dd className={cn("grid shrink-0 justify-items-end text-sm font-medium tabular-nums", row.tone)}>
              {row.figures.length > 0 ? row.figures.map((figure) => <span key={figure}>{figure}</span>) : <span>—</span>}
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
