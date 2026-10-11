"use client";

import Link from "next/link";
import { useQueries, useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getHoldings, getMe, getPolicy, listInvoiceTotals, type InvoiceTotal } from "@/lib/api";
import { formatMoney, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

/**
 * Where the money stands, in a few lines for the chat home: what the accounts hold in all, in the
 * policy's currency at the official rate, and what is due in and out over the next four weeks,
 * each currency on its own. The Money page has the rest.
 */
export function MoneyCard() {
  const { t, locale } = useI18n();
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy });
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const currency = policy.data?.currency;
  const holdings = useQuery({
    queryKey: ["money-holdings", currency],
    queryFn: () => getHoldings(currency ?? ""),
    enabled: Boolean(currency),
  });
  const [year = 1970, month = 1, day = 1] = (me.data?.today ?? todayIso()).split("-").map(Number);
  const until = todayIso(new Date(year, month - 1, day + 28));
  const sides = useQueries({
    queries: (["receivable", "payable"] as const).map((direction) => ({
      queryKey: ["invoice-totals", { direction, outstanding: true, due_before: until }],
      queryFn: () => listInvoiceTotals({ direction, outstanding: true, due_before: until }),
    })),
  });
  if (holdings.isPending || sides.some((side) => side.isPending)) return <Skeleton className="h-24 rounded-2xl" />;
  if (!holdings.data || holdings.data.items.length === 0) return null;

  const shown = (totals: InvoiceTotal[] | undefined) => {
    const amounts = (totals ?? []).filter((total) => total.currency && Number(total.remaining_amount ?? 0) > 0);
    return amounts.length === 0
      ? "—"
      : amounts.map((total) => formatMoney(Number(total.remaining_amount), total.currency ?? "", locale)).join(" · ");
  };
  const cells: [string, string][] = [
    [t.moneyCard.inAll, formatMoney(Number(holdings.data.total), holdings.data.currency, locale)],
    [t.moneyCard.collect, shown(sides[0]?.data)],
    [t.moneyCard.pay, shown(sides[1]?.data)],
  ];
  return (
    <section aria-label={t.moneyCard.title} className="border-border bg-card rounded-2xl border">
      {/* Rows on a phone, where three columns would break a figure in two. */}
      <dl className="divide-border grid divide-y sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        {cells.map(([label, value]) => (
          <div
            key={label}
            className="flex min-w-0 items-baseline justify-between gap-3 px-4 py-2.5 sm:flex-col sm:items-start sm:justify-between sm:gap-1 sm:py-3.5"
          >
            <dt className="text-muted-foreground text-xs leading-snug">{label}</dt>
            <dd className="text-end text-sm font-medium tabular-nums sm:text-start">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="border-border flex items-center justify-between gap-2 border-t px-4 py-2">
        <span className="text-muted-foreground truncate text-xs">
          {policy.data ? t.money.reserve(t.settings.howYouPay.presets[policy.data.reserve.key]?.name ?? policy.data.reserve.name) : null}
        </span>
        <Button asChild size="sm" variant="ghost">
          <Link href="/app/money">{t.moneyCard.open}</Link>
        </Button>
      </div>
    </section>
  );
}
