"use client";

import { useQuery } from "@tanstack/react-query";

import { type Invoice, type InvoiceLine, listInvoiceLines } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { Dictionary } from "@/lib/i18n/en";
import { useI18n } from "@/lib/i18n/client";

/** The lines the bill reader read of these bills' files, each bill's in order. */
export function useInvoiceLines(ids: readonly string[]) {
  const sorted = [...new Set(ids)].sort();
  return useQuery({
    queryKey: ["invoice-lines", sorted],
    queryFn: () => listInvoiceLines(sorted),
    enabled: sorted.length > 0,
  });
}

const chargeLabel = (charge: string, t: Dictionary) => t.books.charges[charge] ?? charge;

/** A bill's charges in a few words, "Freight · Waiting 2 days"; empty when none was read. */
export function chargesWord(lines: readonly InvoiceLine[], t: Dictionary): string {
  const days = new Map<string, number>();
  for (const line of lines) days.set(line.charge, (days.get(line.charge) ?? 0) + (line.days ?? 0));
  return [...days]
    .map(([charge, count]) => (count > 0 ? `${chargeLabel(charge, t)} ${t.books.days(count)}` : chargeLabel(charge, t)))
    .join(" · ");
}

/** What the bill's file says it is for, and each line it charges for: the kind of charge, its
 * words as the file has them, its plates and its amount. */
export function InvoiceLines({ bill }: { bill: Invoice }) {
  const { t, locale } = useI18n();
  const lines = useInvoiceLines([bill.id]);
  const read = lines.data ?? [];
  if (!bill.about && read.length === 0) return null;
  const money = (line: InvoiceLine) => {
    const currency = line.currency ?? bill.currency;
    return line.amount !== null && currency ? formatMoney(Number(line.amount), currency, locale) : null;
  };
  return (
    <section className="grid gap-5">
      {bill.about ? (
        <div className="grid gap-1">
          <h4 className="text-sm font-medium">{t.books.about}</h4>
          <p className="text-sm text-pretty [overflow-wrap:anywhere]">{bill.about}</p>
        </div>
      ) : null}
      {read.length > 0 ? (
        <div className="grid gap-1.5">
          <h4 className="text-sm font-medium">{t.books.lines}</h4>
          <ul className="divide-border border-border divide-y rounded-2xl border text-sm">
            {read.map((line) => (
              <li key={line.id} className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 px-4 py-2.5">
                <span className="text-muted-foreground text-xs">
                  {chargeLabel(line.charge, t)}
                  {line.days ? ` · ${t.books.days(line.days)}` : null}
                  {line.origin || line.destination ? ` · ${line.origin ?? "?"} → ${line.destination ?? "?"}` : null}
                </span>
                <span className="row-span-2 self-center tabular-nums">{money(line) ?? "—"}</span>
                <span className="min-w-0 [overflow-wrap:anywhere]">{line.quote}</span>
                {line.plates.length > 0 ? (
                  <span className="col-span-2 flex flex-wrap gap-1">
                    {line.plates.map((plate) => (
                      <span key={plate} className="bg-muted rounded-full px-2 py-0.5 font-mono text-xs">
                        {plate}
                      </span>
                    ))}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          {bill.lines_balance === false ? (
            <p className="text-warning-foreground text-xs text-pretty" role="note">
              {t.books.unbalanced}
            </p>
          ) : null}
        </div>
      ) : null}
      <p className="text-muted-foreground -mt-3 text-xs">{t.books.linesRead}</p>
    </section>
  );
}
