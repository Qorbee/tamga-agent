"use client";

import { useQuery } from "@tanstack/react-query";

import type { Invoice, LinkItem } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

import { linksKey, readLinks } from "./project-timeline";
import { useChecklist } from "./truck-board";

type Side = { total: Map<string, number>; left: Map<string, number>; count: number };

/** What the project's confirmed invoices on one side of the books add up to, per currency. */
function sideOf(invoices: Invoice[], direction: Invoice["direction"]): Side {
  const side: Side = { total: new Map(), left: new Map(), count: 0 };
  for (const invoice of invoices) {
    if (invoice.direction !== direction || invoice.cancelled || !invoice.currency) continue;
    side.count += 1;
    const add = (into: Map<string, number>, amount: string | null) =>
      into.set(invoice.currency ?? "", (into.get(invoice.currency ?? "") ?? 0) + Number(amount ?? 0));
    add(side.total, invoice.total_amount);
    // What is owed as read: on the firm's account, an invoice its account shows paid is paid.
    add(side.left, invoice.owed ?? invoice.remaining_amount);
  }
  return side;
}

/**
 * The project at a glance, above its trucks and timeline: what the client was invoiced and the
 * subcontractors billed, what of each is still open, what is left between them, and what waits
 * (papers missing, links to confirm). Only links the owner confirmed count toward the money; the
 * margin is shown only when both sides are in one currency, never converted.
 */
export function ProjectBrief({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const words = t.project.brief;
  const links = useQuery({ queryKey: linksKey(id), queryFn: () => readLinks(id) });
  const checklist = useChecklist(id).data ?? [];
  if (!links.data) return null;

  const invoices = links.data
    .filter((link) => link.status === "confirmed")
    .map((link) => link.item)
    .filter((item): item is Extract<LinkItem, { item_kind: "invoice" }> => item?.item_kind === "invoice")
    .map((item) => item.invoice);
  const invoiced = sideOf(invoices, "receivable");
  const billed = sideOf(invoices, "payable");
  const missing = checklist.filter((item) => item.state === "missing").length;
  const toConfirm = links.data.filter((link) => link.status === "suggested").length;
  if (invoiced.count === 0 && billed.count === 0 && missing === 0 && toConfirm === 0) return null;

  const money = (sums: Map<string, number>) =>
    sums.size === 0 ? "—" : [...sums].map(([currency, amount]) => formatMoney(amount, currency, locale)).join(" · ");
  const open = (side: Side) =>
    side.count === 0 ? "" : [...side.left.values()].every((left) => left === 0) ? words.paid : words.left(money(side.left));
  const [only] = invoiced.total.size === 1 && billed.total.size === 1 ? [...invoiced.total.keys()] : [];
  const margin =
    only !== undefined && billed.total.has(only)
      ? formatMoney((invoiced.total.get(only) ?? 0) - (billed.total.get(only) ?? 0), only, locale)
      : "—";
  const marginNote =
    invoiced.count === 0 ? words.noInvoice : billed.count === 0 ? words.noBill : only === undefined || margin === "—" ? words.mixed : "";
  const cells: [string, string, string][] = [
    [words.invoiced, money(invoiced.total), open(invoiced)],
    [words.billed, money(billed.total), open(billed)],
    [words.margin, margin, marginNote],
  ];

  return (
    <section aria-label={words.title} className="border-border bg-card rounded-2xl border">
      {/* Rows on a phone, where three columns would break a figure in two. */}
      <dl className="divide-border grid divide-y sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        {cells.map(([label, value, note]) => (
          <div key={label} className="flex min-w-0 items-baseline justify-between gap-3 px-4 py-2.5 sm:flex-col sm:items-start sm:justify-start sm:gap-0.5 sm:py-3">
            <dt className="text-muted-foreground text-xs">{label}</dt>
            <dd className="text-end sm:text-start">
              <span className="block text-sm font-medium tabular-nums">{value}</span>
              {note ? <span className="text-muted-foreground block text-xs">{note}</span> : null}
            </dd>
          </div>
        ))}
      </dl>
      {missing > 0 || toConfirm > 0 ? (
        <p className="border-border flex flex-wrap gap-x-4 gap-y-1 border-t px-4 py-2 text-xs">
          {missing > 0 ? <span className="text-warning-foreground">{words.missing(missing)}</span> : null}
          {toConfirm > 0 ? <span className="text-muted-foreground">{words.toConfirm(toConfirm)}</span> : null}
        </p>
      ) : null}
    </section>
  );
}
