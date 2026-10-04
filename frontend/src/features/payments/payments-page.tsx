"use client";

import { useQueries } from "@tanstack/react-query";
import { useState } from "react";

import { DecisionRow } from "@/components/decision-card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { listDecisions, listPayments, type Decision, type Payment } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { usePagedList } from "@/lib/use-paged-list";
import { cn } from "@/lib/utils";

const PAGE = 50;
const VIEWS = ["waiting", "held", "paid"] as const;
type View = (typeof VIEWS)[number];
/** What each tab lists: an approved decision waits for the executor beside those that wait for
 * the owner, since both are still to be paid. */
const STATES: Record<View, Decision["state"][]> = {
  waiting: ["awaiting_owner", "approved"],
  held: ["held", "failed", "rejected"],
  paid: ["settled"],
};
// The chosen option is filled and in full ink; the others recede.
const SEGMENT = "text-muted-foreground aria-checked:bg-secondary aria-checked:text-foreground";

/**
 * The bills Tamga decided about (docs/architecture/05): what waits for the owner's approval, what
 * a rule or the owner holds and why, and what the vault paid, with its transaction. The owner
 * answers here; nothing is paid without that answer, and an approval is confirmed first, since a
 * payment cannot be taken back.
 */
export function PaymentsPage() {
  const { t } = useI18n();
  const [view, setView] = useState<View>("waiting");
  return (
    <div className="grid gap-4">
      <ToggleGroup
        type="single"
        value={view}
        spacing={1}
        className="w-full"
        aria-label={t.nav.payments}
        onValueChange={(value) => {
          if (value) setView(value as View);
        }}
      >
        {VIEWS.map((value) => (
          <ToggleGroupItem key={value} value={value} className={cn(SEGMENT, "min-w-0 flex-1 px-2")}>
            <span className="truncate">{t.payments.views[value]}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <Decisions view={view} />
      <p className="text-muted-foreground text-xs text-pretty">{t.payments.mirror}</p>
    </div>
  );
}

function Decisions({ view }: { view: View }) {
  const { t } = useI18n();
  const decisions = usePagedList(
    ["decisions", { view }],
    (page) => listDecisions({ state: STATES[view], expand: ["invoice"], ...page }),
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
  const shown = new Map<string, Payment>();
  for (const payment of payments.flatMap((query) => query.data?.items ?? [])) {
    const kept = shown.get(payment.decision_id);
    const newer = !kept || (kept.state !== "settled" && payment.evaluation > kept.evaluation);
    if (payment.state === "settled" || (payment.state === "failed" && newer)) shown.set(payment.decision_id, payment);
  }

  if (decisions.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  // While another tab's rows are read, the last tab's are never shown under its name.
  if (!decisions.items || decisions.isPlaceholderData) return <Skeleton className="h-40 rounded-2xl" />;
  if (decisions.items.length === 0)
    return <p className="text-muted-foreground py-10 text-center text-sm">{t.payments.empty[view]}</p>;
  return (
    <div className="grid gap-3">
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {decisions.items.map((decision) => (
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
