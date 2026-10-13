"use client";

import { useQueries } from "@tanstack/react-query";
import { useState } from "react";

import { DecisionRow } from "@/components/decision-card";
import { MoneyTiles } from "@/components/money-tiles";
import { PaymentRunCard, usePaymentRun } from "@/components/payment-run";
import { Segmented } from "@/components/segmented";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { listDecisions, listPayments, type Decision, type Payment } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { usePagedList } from "@/lib/use-paged-list";

const PAGE = 50;
const VIEWS = ["later", "held", "paid"] as const;
type View = (typeof VIEWS)[number];
/** What each tab lists: what is still to be paid after the run above (an approved decision waits
 * for the executor beside those that wait for the owner); what is held; and what is paid, by the
 * vault or not. */
const STATES: Record<View, Decision["state"][]> = {
  later: ["awaiting_owner", "approved"],
  held: ["held", "failed", "rejected"],
  paid: ["settled", "paid_in_books"],
};

/**
 * The bills Tamga decided about (docs/architecture/05), at a glance: where the money stands, the
 * next payment run with each bill's call and "Approve N", then what is due later, what is held and
 * what was paid. The owner answers here; nothing is paid without that answer, and an approval is
 * confirmed first, since a payment cannot be taken back.
 */
export function PaymentsPage() {
  const { t } = useI18n();
  const [view, setView] = useState<View>("later");
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <MoneyTiles />
      <PaymentRunCard full />
      <Segmented
        fill
        label={t.nav.payments}
        value={view}
        onChange={setView}
        options={VIEWS.map((value) => ({ value, label: <span className="truncate">{t.payments.views[value]}</span> }))}
      />
      <Decisions view={view} />
      <p className="text-muted-foreground text-xs">{t.payments.mirror}</p>
    </div>
  );
}

/** A settled payment the owner has not undone. */
function inForce(payment: Payment): boolean {
  return payment.state === "settled" && !payment.voided_at;
}

function Decisions({ view }: { view: View }) {
  const { t } = useI18n();
  const run = usePaymentRun();
  // The run's bills are on its card above; "Later" lists the rest still to pay.
  const inRun = new Set(view === "later" ? (run.data?.items ?? []).map((item) => item.decision.id) : []);
  const decisions = usePagedList(
    ["decisions", { view }],
    (page) => listDecisions({ state: STATES[view], expand: ["invoice"], sort: "-issued_on", ...page }),
    PAGE,
    (decision) => decision.id,
  );
  // The payments of what was paid or refused, a page of decisions at a time: a paid row shows its
  // transaction, a refused one the vault's reason.
  const finished = (decisions.items ?? [])
    .filter((decision) => decision.state === "settled" || decision.state === "failed")
    .map((decision) => decision.id);
  const pages = Array.from({ length: Math.ceil(finished.length / PAGE) }, (_, index) =>
    finished.slice(index * PAGE, (index + 1) * PAGE),
  );
  const payments = useQueries({
    queries: pages.map((ids) => ({
      queryKey: ["payments", { decisions: ids }],
      queryFn: () => listPayments({ decision_id: ids, limit: 200 }),
    })),
  });
  // Each bill's payment in force; one the owner undid only while none replaced it, and a failed
  // attempt only while nothing settled.
  const shown = new Map<string, Payment>();
  for (const payment of payments.flatMap((query) => query.data?.items ?? [])) {
    const kept = shown.get(payment.decision_id);
    if (kept && inForce(kept)) continue;
    const settled = payment.state === "settled";
    const newer = !kept || (kept.state !== "settled" && payment.evaluation > kept.evaluation);
    if (inForce(payment) || (settled && kept?.state !== "settled") || (payment.state === "failed" && newer))
      shown.set(payment.decision_id, payment);
  }

  if (decisions.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  // While another tab's rows are read, the last tab's are never shown under its name.
  if (!decisions.items || decisions.isPlaceholderData) return <Skeleton className="h-40 rounded-2xl" />;
  const rows = decisions.items.filter((decision) => !inRun.has(decision.id));
  if (rows.length === 0 && !decisions.more)
    return <p className="text-muted-foreground py-10 text-center text-sm">{t.payments.empty[view]}</p>;
  return (
    <div className="grid gap-3">
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {rows.map((decision) => (
          <li key={decision.id} className="py-1 ps-2 pe-3">
            <DecisionRow decision={decision} payment={shown.get(decision.id)} />
          </li>
        ))}
      </ul>
      {decisions.more ? (
        <Button
          variant="ghost"
          className="justify-self-center"
          disabled={decisions.loadingMore}
          onClick={decisions.more}
        >
          {t.inbox.showMore}
        </Button>
      ) : null}
    </div>
  );
}
