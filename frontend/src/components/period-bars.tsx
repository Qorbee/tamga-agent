"use client";

import { formatMoney, formatMoneyCompact } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * Money over periods as bars, one currency at a time: currencies are never added together here.
 * Each period has one bar per series (in and out, or one side alone), each labelled with its
 * figure; a table under it carries the same figures for a screen reader. Every figure is the
 * caller's, read from the API.
 */
export type Period = { key: string; label: string; fullLabel: string; values: number[] };
export type Series = { label: string; tone: "success" | "warning" | "muted" };

const TONE = { success: "bg-success", warning: "bg-warning", muted: "bg-muted-foreground/50" } as const;

export function SeriesLegend({ series }: { series: Series[] }) {
  return (
    <p className="text-muted-foreground flex flex-wrap gap-x-4 text-xs">
      {series.map((one) => (
        <span key={one.label} className="flex items-center gap-1.5">
          <span className={cn("size-2 rounded-sm", TONE[one.tone])} aria-hidden />
          {one.label}
        </span>
      ))}
    </p>
  );
}

export function PeriodBars({
  currency,
  periods,
  series,
  shownCurrency,
}: {
  currency: string;
  periods: Period[];
  series: Series[];
  shownCurrency: boolean;
}) {
  const { locale } = useI18n();
  const most = Math.max(1, ...periods.flatMap((period) => period.values));
  const short = (amount: number) => (amount > 0 ? formatMoneyCompact(amount, currency, locale) : "");
  const height = (amount: number) => `${Math.max(amount > 0 ? 4 : 0, (amount / most) * 100)}%`;
  return (
    <figure className="grid gap-2">
      {shownCurrency ? <figcaption className="text-muted-foreground font-mono text-xs">{currency}</figcaption> : null}
      <div aria-hidden className="grid h-36 items-end gap-2" style={{ gridTemplateColumns: `repeat(${periods.length}, minmax(0, 1fr))` }}>
        {periods.map((period) => (
          <div key={period.key} className="flex h-full items-end justify-center gap-1">
            {series.map((one, index) => (
              <div key={one.label} className="flex h-full w-full max-w-7 flex-col justify-end">
                <span className="text-muted-foreground mb-1 truncate text-center text-[10px] tabular-nums">
                  {short(period.values[index] ?? 0)}
                </span>
                <span className={cn("w-full rounded-t-md", TONE[one.tone])} style={{ height: height(period.values[index] ?? 0) }} />
              </div>
            ))}
          </div>
        ))}
      </div>
      <div
        aria-hidden
        className="text-muted-foreground border-border grid gap-2 border-t pt-1.5 text-center text-[11px]"
        style={{ gridTemplateColumns: `repeat(${periods.length}, minmax(0, 1fr))` }}
      >
        {periods.map((period) => (
          <span key={period.key} className="truncate">
            {period.label}
          </span>
        ))}
      </div>
      <table className="sr-only">
        <thead>
          <tr>
            <th />
            {series.map((one) => (
              <th key={one.label}>{one.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {periods.map((period) => (
            <tr key={period.key}>
              <th>{period.fullLabel}</th>
              {series.map((one, index) => (
                <td key={one.label}>{formatMoney(period.values[index] ?? 0, currency, locale)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

type Cumulative = { currency: string | null; remaining_amount: string | null; total_amount?: string | null };

/** What is left per currency up to cutoff `index` (or what was invoiced, by `field`), less the
 * same up to the one before: the books' totals are cumulative up to each cutoff, so no list is cut
 * short by a page. */
export function between(
  cumulative: Cumulative[][],
  index: number,
  field: "remaining_amount" | "total_amount" = "remaining_amount",
): [string, number][] {
  const sum = (totals: Cumulative[] | undefined) => {
    const out = new Map<string, number>();
    for (const total of totals ?? []) {
      const amount = total[field];
      if (total.currency && amount !== null && amount !== undefined) out.set(total.currency, Number(amount));
    }
    return out;
  };
  const now = sum(cumulative[index]);
  const before = index === 0 ? new Map<string, number>() : sum(cumulative[index - 1]);
  return [...now]
    .map(([currency, amount]): [string, number] => [currency, amount - (before.get(currency) ?? 0)])
    .filter(([, amount]) => Math.abs(amount) >= 0.005);
}
