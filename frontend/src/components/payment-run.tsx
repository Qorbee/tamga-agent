"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { DecisionRow } from "@/components/decision-card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { approveRun, getPaymentRun, type PaymentRun, type RunApproved, type RunItem } from "@/lib/api";
import { formatDays, formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

/**
 * The payment run (docs/architecture/05): the bills to pay on the next pay day, weighed in order
 * against what the accounts hold once the reserve keeps its share. Every figure is the server's;
 * approving the run approves each bill as the owner would one by one. The card sits on the chat
 * home; the run opens in the canvas (`?view=run:<day>`).
 */

export function usePaymentRun(on?: string) {
  return useQuery({ queryKey: ["payment-run", on ?? "next"], queryFn: () => getPaymentRun(on) });
}

/** The bills a click on "approve" answers: those waiting that fit, or all waiting when no balance is known. */
function toApprove(run: PaymentRun): RunItem[] {
  const waiting = run.items.filter((item) => item.decision.state === "awaiting_owner");
  const weighed = run.items.some((item) => item.fits !== null);
  return weighed ? waiting.filter((item) => item.fits === true) : waiting;
}

function useApproveRun(onDone: (result: RunApproved) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (items: RunItem[]) =>
      approveRun(items.map((item) => ({ decision_id: item.decision.id, evaluation: item.decision.evaluation }))),
    onSuccess: onDone,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["payment-run"] });
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["decision"] });
    },
  });
}

/** The run in a few lines, for the chat home: nothing while there is nothing to pay. */
export function PaymentRunCard() {
  const { t, locale } = useI18n();
  const run = usePaymentRun();
  if (run.isPending) return <Skeleton className="h-28 rounded-2xl" />;
  // Shown while anything is due, held bills too: the card is the way into the run.
  if (!run.data || (run.data.items.length === 0 && run.data.held.length === 0)) return null;
  const data = run.data;
  const fit = data.items.filter((item) => item.fits === true).length;
  const weighed = data.items.some((item) => item.fits !== null);
  return (
    <section aria-label={t.run.title} className="border-border bg-card rounded-2xl border">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-4 pt-3.5">
        <h2 className="text-sm font-medium">{t.run.on(formatDays(data.on, null, locale))}</h2>
        <span className="text-muted-foreground text-xs">
          {weighed ? t.run.summary(fit, data.items.length) : t.run.summaryUnknown(data.items.length)}
          {data.held.length > 0 ? ` · ${t.run.heldSome(data.held.length)}` : null}
        </span>
      </header>
      {data.items.length > 0 ? (
        <ul className="grid gap-1 px-4 py-3">
          {data.items.slice(0, 4).map((item) => (
            <RunLine key={item.decision.id} item={item} />
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground px-4 py-3 text-sm">{t.run.onlyHeld}</p>
      )}
      {/* What the accounts hold is the money card's, just below. */}
      <Figures run={data} balance={false} />
      <RunActions run={data} />
    </section>
  );
}

function RunLine({ item }: { item: RunItem }) {
  const { t, locale } = useI18n();
  const bill = item.decision.invoice;
  const amount = item.decision.currency ? formatMoney(Number(item.decision.amount), item.decision.currency, locale) : "—";
  return (
    <li className="flex items-center gap-2 text-sm">
      <FitMark item={item} />
      <span className="min-w-0 flex-1 truncate">{bill?.party_name ?? "—"}</span>
      <span className="text-muted-foreground hidden font-mono text-xs sm:inline">{bill?.number}</span>
      <span className="shrink-0 tabular-nums">{amount}</span>
      <span className="sr-only">{fitWord(item, t)}</span>
    </li>
  );
}

function fitWord(item: RunItem, t: ReturnType<typeof useI18n>["t"]): string {
  if (item.decision.state === "approved") return t.run.approved;
  return item.fits === true ? t.run.fits : item.fits === false ? t.run.over : t.run.unknown;
}

/** Filled when it fits, hollow when it would go under the reserve, ticked once approved. */
function FitMark({ item }: { item: RunItem }) {
  const approved = item.decision.state === "approved";
  return (
    <span
      aria-hidden
      className={cn(
        "size-2 shrink-0 rounded-full",
        approved ? "bg-success" : item.fits === true ? "bg-primary" : item.fits === false ? "border-warning border" : "bg-muted-foreground/40",
      )}
    />
  );
}

/** What the accounts hold, what the reserve keeps, and what is left to pay with. */
function Figures({ run, balance = true }: { run: PaymentRun; balance?: boolean }) {
  const { t, locale } = useI18n();
  const money = (amount: string | null) => (amount === null ? "—" : formatMoney(Number(amount), run.currency, locale));
  const cells: [string, string][] = [
    ...(balance ? [[t.run.balance, money(run.balance)] as [string, string]] : []),
    [t.run.kept, money(run.kept)],
    [t.run.spare, money(run.spare)],
  ];
  return (
    <dl className={cn("border-border divide-border grid divide-x border-t", balance ? "grid-cols-3" : "grid-cols-2")}>
      {cells.map(([label, value]) => (
        <div key={label} className="min-w-0 px-4 py-2.5">
          <dt className="text-muted-foreground truncate text-xs">{label}</dt>
          <dd className="truncate text-sm font-medium tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function RunActions({ run, inCanvas = false }: { run: PaymentRun; inCanvas?: boolean }) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<RunApproved | null>(null);
  const approve = useApproveRun((done) => {
    setConfirming(false);
    setResult(done);
  });
  const chosen = toApprove(run);
  const weighed = run.items.some((item) => item.fits !== null);
  return (
    <div className="border-border flex flex-wrap items-center gap-2 border-t px-4 py-3">
      {isOwner && chosen.length > 0 ? (
        <Button size="sm" disabled={approve.isPending} onClick={() => setConfirming(true)}>
          {weighed ? t.run.approveFitting(chosen.length) : t.run.approveAll}
        </Button>
      ) : null}
      {!inCanvas ? (
        <Button asChild size="sm" variant="outline">
          <CanvasLink view={{ kind: "run", id: run.on }}>{t.run.open}</CanvasLink>
        </Button>
      ) : null}
      {result ? (
        <span className="text-muted-foreground text-xs" role="status">
          {t.run.approvedSome(result.approved.length)}
          {result.refused.length > 0 ? ` · ${t.run.refusedSome(result.refused.length)}: ${result.refused[0]?.reason}` : null}
        </span>
      ) : null}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.run.confirmTitle(chosen.length)}</DialogTitle>
            <DialogDescription>{t.run.confirmBody}</DialogDescription>
          </DialogHeader>
          <ul className="grid gap-1">
            {chosen.map((item) => (
              <RunLine key={item.decision.id} item={item} />
            ))}
          </ul>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              {t.payments.cancel}
            </Button>
            <Button disabled={approve.isPending} onClick={() => approve.mutate(chosen)}>
              {t.payments.approve}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function PaymentRunTitle({ on }: { on: string }) {
  const { t, locale } = useI18n();
  // The server's day: a day already past is asked as today.
  const day = usePaymentRun(on).data?.on ?? on;
  return <>{t.run.on(formatDays(day, null, locale))}</>;
}

/** The whole run, in the canvas: each bill as the Payments page shows it, with whether it fits. */
export function PaymentRunView({ on }: { on: string }) {
  const { t } = useI18n();
  const run = usePaymentRun(on);
  if (run.isPending) return <Skeleton className="m-4 h-64 rounded-2xl" />;
  if (!run.data) return <p className="text-danger-foreground p-4 text-sm">{t.errors.load}</p>;
  const data = run.data;
  return (
    <div className="grid gap-4 p-4">
      <div className="border-border bg-card rounded-2xl border">
        <Figures run={data} />
        <RunActions run={data} inCanvas />
      </div>
      {data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.run.nothing}</p>
      ) : (
        <ol className="border-border bg-card divide-border divide-y rounded-2xl border">
          {data.items.map((item) => (
            <li key={item.decision.id} className="py-1 ps-2 pe-3">
              <p className="flex items-center gap-2 px-2 pt-2 text-xs">
                <FitMark item={item} />
                <span className="text-muted-foreground">{fitWord(item, t)}</span>
              </p>
              <DecisionRow decision={item.decision} payment={undefined} />
            </li>
          ))}
        </ol>
      )}
      {data.held.length > 0 ? (
        <section className="grid gap-2">
          <h3 className="text-muted-foreground text-xs font-medium">{t.run.held}</h3>
          <ol className="border-border divide-border divide-y rounded-2xl border">
            {data.held.map((decision) => (
              <li key={decision.id} className="py-1 ps-2 pe-3">
                <DecisionRow decision={decision} payment={undefined} />
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}
