"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getInvoice, type Invoice } from "@/lib/api";
import { formatDate, formatDays, formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

const invoiceKey = (id: string) => ["invoice", id] as const;

function useInvoice(id: string) {
  return useQuery({ queryKey: invoiceKey(id), queryFn: () => getInvoice(id), retry: false });
}

/** The invoice's number, for the canvas's title bar. */
export function InvoiceTitle({ id }: { id: string }) {
  const { t } = useI18n();
  const invoice = useInvoice(id);
  if (!invoice.data) return <Skeleton className="h-4 w-24" />;
  return <span className="truncate font-mono">{invoice.data.number ?? t.books.noNumber}</span>;
}

/**
 * An invoice as the books hold it: who it is with, its days, what it comes to and what is left
 * to pay. Read only: the books are the record, and Tamga never writes to them.
 */
export function InvoiceView({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const invoice = useInvoice(id);

  if (invoice.isError)
    return (
      <p className="text-muted-foreground p-5 text-sm" role="alert">
        {invoice.error instanceof ApiError && invoice.error.status === 404 ? t.books.notFound : t.errors.load}
      </p>
    );
  if (!invoice.data) return <Skeleton className="m-5 h-32 rounded-2xl" />;

  const bill = invoice.data;
  const money = (amount: string | null) =>
    amount !== null && bill.currency ? formatMoney(Number(amount), bill.currency, locale) : null;
  const day = (value: string | null) => (value ? formatDays(value, null, locale) : null);
  const rows: [string, ReactNode][] = [
    [t.books.party, bill.party_name],
    [t.books.issued, day(bill.issue_date)],
    [t.books.due, day(bill.due_date)],
    [t.books.paidOn, day(bill.payment_date)],
    [t.books.net, money(bill.net_amount)],
    [t.books.tax, money(bill.tax_amount)],
    [t.books.status, bill.status],
    [t.books.eDocument, bill.e_document_id ? <span className="font-mono text-xs break-all">{bill.e_document_id}</span> : null],
  ];

  return (
    <div className="grid grid-cols-1 gap-6 p-4 sm:p-5">
      <header className="grid gap-1">
        <span className="text-muted-foreground text-sm">
          {t.books.kinds[bill.kind]}
          {bill.cancelled ? <span className="text-danger-foreground"> · {t.books.cancelled}</span> : null}
        </span>
        <p className={cn("text-2xl font-medium tabular-nums", bill.cancelled && "line-through")}>
          {money(bill.total_amount) ?? "—"}
        </p>
        <Left bill={bill} money={money} />
      </header>

      <dl className="divide-border border-border divide-y rounded-2xl border text-sm">
        {rows
          .filter(([, value]) => value !== null && value !== "")
          .map(([label, value]) => (
            <div key={label} className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-3 px-4 py-2.5">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
            </div>
          ))}
      </dl>

      {bill.description ? (
        <section className="grid gap-1.5">
          <h4 className="text-sm font-medium">{t.books.description}</h4>
          <p className="text-muted-foreground text-sm whitespace-pre-line [overflow-wrap:anywhere]">{bill.description}</p>
        </section>
      ) : null}

      <p className="text-muted-foreground text-xs">
        {t.books.read} · {formatDate(new Date(bill.synced_at), locale, "dateTime")}
      </p>
    </div>
  );
}

/** What is left to pay, or that nothing is; nothing shown when the books do not say. */
function Left({ bill, money }: { bill: Invoice; money: (amount: string | null) => string | null }) {
  const { t } = useI18n();
  if (bill.remaining_amount === null || bill.cancelled) return null;
  const left = Number(bill.remaining_amount);
  return (
    <span className={cn("text-sm tabular-nums", left > 0 ? "text-warning-foreground" : "text-success-foreground")}>
      {t.books.left}: {money(bill.remaining_amount)}
    </span>
  );
}
