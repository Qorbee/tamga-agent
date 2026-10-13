"use client";

import { useState } from "react";

import { currencySign, formatMoney, formatMoneyCompact } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * Money over periods as columns, one currency at a time: currencies are never added together here.
 * Each period is a column of one bar per series (in and out, or one side alone), and a line above
 * reads out the period chosen (tapped or pointed at; the first with money to start), so no figure
 * is squeezed onto a bar too thin to hold it. The same chart on a phone and a desktop: a narrow one
 * writes its figures short ("€1.9K") with the currency as its sign. With two series, in then out,
 * `net` says what they leave. A table under it carries the same figures for a screen reader. Every
 * figure is the caller's, read from the API.
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
  net = false,
}: {
  currency: string;
  periods: Period[];
  series: Series[];
  shownCurrency: boolean;
  /** In less out, for two series given in that order. */
  net?: boolean;
}) {
  const { t, locale } = useI18n();
  const first = Math.max(0, periods.findIndex((period) => period.values.some((value) => value > 0)));
  const [chosen, setChosen] = useState<number | null>(null);
  const at = Math.min(chosen ?? first, periods.length - 1);
  const most = Math.max(1, ...periods.flatMap((period) => period.values));
  const share = (amount: number) => `${Math.max(amount > 0 ? 3 : 0, (amount / most) * 100)}%`;
  const leaves = (period: Period) => (period.values[0] ?? 0) - (period.values[1] ?? 0);
  const signed = (amount: number, format: (amount: number) => string) =>
    `${amount > 0 ? "+" : amount < 0 ? "−" : ""}${format(Math.abs(amount))}`;
  const shown = periods[at];
  // Both forms are drawn and the container's width shows one: no measuring in script.
  const both = (amount: number) => (
    <>
      <span className="@md:hidden">{formatMoneyCompact(amount, currency, locale)}</span>
      <span className="hidden @md:inline">{formatMoney(amount, currency, locale)}</span>
    </>
  );

  return (
    <figure className="@container grid gap-2">
      {/* Named whenever its sign alone could be another's ("$" for US, Canadian or Australian
          dollars), or beside another currency's chart. */}
      {shownCurrency || currencySign(currency, locale) !== currencySign(currency, locale, "symbol") ? (
        <figcaption className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <span className="bg-secondary text-foreground flex size-5 items-center justify-center rounded-full text-[11px] font-medium" aria-hidden>
            {currencySign(currency, locale)}
          </span>
          {currency}
        </figcaption>
      ) : null}
      {shown ? (
        <p className="flex min-h-5 flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs" aria-live="polite">
          <span className="font-medium">{shown.fullLabel}</span>
          {series.map((one, index) => (
            <span key={one.label} className="text-muted-foreground flex items-center gap-1.5">
              <span className={cn("size-2 rounded-sm", TONE[one.tone])} aria-hidden />
              <span className="@md:hidden sr-only">{one.label}</span>
              <span className="hidden @md:inline">{one.label}</span>
              <span className="text-foreground tabular-nums">{both(shown.values[index] ?? 0)}</span>
            </span>
          ))}
          {net && series.length === 2 ? (
            <span className="text-muted-foreground">
              {t.money.net}{" "}
              <span className={cn("tabular-nums", leaves(shown) < 0 ? "text-danger-foreground" : "text-foreground")}>
                {leaves(shown) > 0 ? "+" : leaves(shown) < 0 ? "−" : ""}
                {both(Math.abs(leaves(shown)))}
              </span>
            </span>
          ) : null}
        </p>
      ) : null}
      <div className="grid h-28 items-end gap-1.5 @md:h-32 @md:gap-2" style={{ gridTemplateColumns: `repeat(${periods.length}, minmax(0, 1fr))` }}>
        {periods.map((period, index) => (
          <button
            key={period.key}
            type="button"
            aria-pressed={index === at}
            aria-label={period.fullLabel}
            onClick={() => setChosen(index)}
            onPointerEnter={(event) => {
              if (event.pointerType === "mouse") setChosen(index);
            }}
            className={cn(
              "relative flex h-full items-end justify-center gap-1 rounded-md pt-4 transition-opacity outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
              index === at ? "opacity-100" : "opacity-45 hover:opacity-80",
            )}
          >
            {/* What the column leaves, short, over it: the chart reads at a glance before a tap. */}
            {net && series.length === 2 && period.values.some((value) => value > 0) ? (
              <span
                aria-hidden
                className={cn(
                  "absolute inset-x-0 top-0 truncate text-center text-[10px] leading-4 tabular-nums",
                  leaves(period) < 0 ? "text-danger-foreground" : "text-muted-foreground",
                )}
              >
                {signed(leaves(period), (amount) => formatMoneyCompact(amount, currency, locale))}
              </span>
            ) : null}
            {series.map((one, value) => (
              <span
                key={one.label}
                className={cn("w-full max-w-7 rounded-t-md", TONE[one.tone])}
                style={{ height: share(period.values[value] ?? 0) }}
              />
            ))}
          </button>
        ))}
      </div>
      <div
        aria-hidden
        className="text-muted-foreground border-border grid gap-1.5 border-t pt-1.5 text-center text-[11px] @md:gap-2"
        style={{ gridTemplateColumns: `repeat(${periods.length}, minmax(0, 1fr))` }}
      >
        {periods.map((period, index) => (
          <span key={period.key} className={cn("truncate", index === at && "text-foreground font-medium")}>
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
            {net && series.length === 2 ? <th>{t.money.net}</th> : null}
          </tr>
        </thead>
        <tbody>
          {periods.map((period) => (
            <tr key={period.key}>
              <th>{period.fullLabel}</th>
              {series.map((one, index) => (
                <td key={one.label}>{formatMoney(period.values[index] ?? 0, currency, locale)}</td>
              ))}
              {net && series.length === 2 ? <td>{formatMoney(leaves(period), currency, locale)}</td> : null}
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
