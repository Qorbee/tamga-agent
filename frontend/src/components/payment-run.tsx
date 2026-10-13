"use client";

import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { callOf, type Call } from "@/components/bill-call";
import { CallPill } from "@/components/call-pill";
import { CanvasLink } from "@/components/canvas-link";
import { CashBar, weigh } from "@/components/cash-bar";
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
import { approveRun, getPaymentRun, type Decision, type PaymentRun, type RunApproved, type RunItem } from "@/lib/api";
import { formatDays, formatMoney, formatMoneyShort } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

/**
 * The payment run (docs/architecture/05): the bills to pay on the next pay day, weighed in order
 * against what the accounts hold once the reserve keeps its share, so the owner sees at a glance
 * what Tamga would pay now, what it would hold and what is theirs to decide. Every figure is the
 * server's; approving the run approves each bill as the owner would one by one. The card sits on
 * the chat home and the Payments page; the run opens in the canvas (`?view=run:<day>`).
 */

export function usePaymentRun(on?: string) {
  return useQuery({ queryKey: ["payment-run", on ?? "next"], queryFn: () => getPaymentRun(on) });
}

/** Whether each bill fits, Tamga's own weighing where the balance is known (`weigh`). */
export function fitsOf(run: PaymentRun): Map<string, boolean | null> {
  return weigh(run)?.fits ?? new Map(run.items.map((item) => [item.decision.id, item.fits]));
}

/** How the bills chosen are paid: the vault settles on Arc, a bank transfer is the owner's. */
function Settles({ items }: { items: RunItem[] }) {
  const { t } = useI18n();
  const vault = items.some((item) => item.decision.method === "vault_usdc");
  const bank = items.some((item) => item.decision.method === "bank_transfer");
  if (!vault && !bank) return null;
  return (
    <span className="text-muted-foreground ms-auto text-xs">
      {vault && bank ? t.run.settlesMixed : vault ? t.run.settlesOnArc : t.run.paidByYou}
    </span>
  );
}

/** The bills "Approve N" answers: those Tamga would pay now that fit; with no balance to weigh
 * them, those Tamga would pay now. */
export function toApprove(run: PaymentRun): RunItem[] {
  const weighed = weigh(run);
  if (weighed) return weighed.toApprove;
  return run.items.filter(
    (item) =>
      item.decision.state === "awaiting_owner" &&
      item.in_policy_currency !== null &&
      callOf(item.decision, item.fits).call === "pay",
  );
}

/** How many bills of each call, in the order the owner reads them. */
const COUNTED: Call[] = ["pay", "pay_part", "approved", "hold", "needs_you", "held"];

export function CallCounts({ decisions, fits }: { decisions: Decision[]; fits?: Map<string, boolean | null> }) {
  const counts = new Map<Call, number>();
  for (const decision of decisions) {
    const call = callOf(decision, fits?.get(decision.id) ?? null).call;
    counts.set(call, (counts.get(call) ?? 0) + 1);
  }
  return (
    <span className="flex flex-wrap gap-1.5">
      {COUNTED.filter((call) => counts.has(call)).map((call) => (
        <span key={call} className="inline-flex items-center gap-1">
          <span className="text-sm font-medium tabular-nums">{counts.get(call)}</span>
          <CallPill call={call} />
        </span>
      ))}
    </span>
  );
}

export function useApproveRun(onDone: (result: RunApproved) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    // The endpoint answers at most fifty at a time: a longer run goes in turns, each its own.
    mutationFn: async (items: { decision_id: string; evaluation: number }[]) => {
      const done: RunApproved = { approved: [], refused: [] };
      for (let start = 0; start < items.length; start += 50) {
        const part = await approveRun(items.slice(start, start + 50));
        done.approved.push(...part.approved);
        done.refused.push(...part.refused);
      }
      return done;
    },
    onSuccess: onDone,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["payment-run"] });
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["decision"] });
    },
  });
}

/**
 * The run as a card: its day, what Tamga would pay now, the calls counted, and "Approve N". With
 * `full`, the cash bar and each bill too (the Payments page); without, it stays a few lines (the
 * chat home), and the run opens in the canvas.
 */
export function PaymentRunCard({ full = false }: { full?: boolean }) {
  const { t, locale } = useI18n();
  const run = usePaymentRun();
  if (run.isPending) return <Skeleton className="h-28 rounded-2xl" />;
  // Shown while anything is due, held bills too: the card is the way into the run.
  if (!run.data || (run.data.items.length === 0 && run.data.held.length === 0)) return null;
  const data = run.data;
  const chosen = toApprove(data);
  const payNow = chosen.reduce((sum, item) => sum + Number(item.in_policy_currency ?? 0), 0);
  const fits = fitsOf(data);
  return (
    <section aria-label={t.run.title} className="border-border bg-card rounded-2xl border">
      <header className="grid gap-1.5 px-4 pt-3.5 pb-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-muted-foreground text-xs font-medium tracking-[0.08em] uppercase">
            {t.run.on(formatDays(data.on, null, locale))}
          </h2>
          <CanvasLink
            view={{ kind: "run", id: data.on }}
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-0.5 text-xs"
          >
            {t.run.open}
            <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
          </CanvasLink>
        </div>
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-2xl font-medium tabular-nums">{formatMoneyShort(payNow, data.currency, locale)}</span>
          <span className="text-muted-foreground text-sm">{t.run.toPayNow}</span>
        </p>
        <CallCounts decisions={[...data.items.map((item) => item.decision), ...data.held]} fits={fits} />
      </header>
      {full ? (
        <>
          <CashBar run={data} className="border-border border-t px-4 py-3" />
          {data.items.length > 0 ? (
            <ul className="divide-border border-border divide-y border-t">
              {data.items.map((item) => (
                <li key={item.decision.id} className="py-1 ps-2 pe-3">
                  <DecisionRow decision={item.decision} payment={undefined} fits={fits.get(item.decision.id) ?? null} inRun />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground border-border border-t px-4 py-3 text-sm">{t.run.onlyHeld}</p>
          )}
        </>
      ) : null}
      <RunActions run={data} />
    </section>
  );
}

function RunLine({ item }: { item: RunItem }) {
  const { locale } = useI18n();
  const bill = item.decision.invoice;
  const amount = item.decision.currency ? formatMoney(Number(item.decision.amount), item.decision.currency, locale) : "—";
  return (
    <li className="flex items-center gap-2 text-sm">
      <CallPill call={callOf(item.decision, item.fits).call} />
      <span className="min-w-0 flex-1 truncate">{bill?.party_name ?? "—"}</span>
      <span className="text-muted-foreground hidden font-mono text-xs sm:inline">{bill?.number}</span>
      <span className="shrink-0 tabular-nums">{amount}</span>
    </li>
  );
}

/** "Approve N payments", with the bills it answers listed in its confirmation. */
export function ApproveMany({ items, className }: { items: RunItem[]; className?: string }) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<RunApproved | null>(null);
  const approve = useApproveRun((done) => {
    setConfirming(false);
    setResult(done);
  });
  if (!isOwner) return null;
  return (
    <span className={cn("flex flex-wrap items-center gap-2", className)}>
      {items.length > 0 ? (
        <Button disabled={approve.isPending} onClick={() => setConfirming(true)}>
          {t.run.approveN(items.length)}
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
            <DialogTitle>{t.run.confirmTitle(items.length)}</DialogTitle>
            <DialogDescription>{t.run.confirmBody}</DialogDescription>
          </DialogHeader>
          <ul className="grid gap-1.5">
            {items.map((item) => (
              <RunLine key={item.decision.id} item={item} />
            ))}
          </ul>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              {t.payments.cancel}
            </Button>
            <Button
              disabled={approve.isPending}
              onClick={() =>
                approve.mutate(items.map((item) => ({ decision_id: item.decision.id, evaluation: item.decision.evaluation })))
              }
            >
              {t.payments.approve}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </span>
  );
}

function RunActions({ run }: { run: PaymentRun }) {
  const chosen = toApprove(run);
  if (chosen.length === 0) return null;
  return <ApproveMany items={chosen} className="border-border border-t px-4 py-3" />;
}

export function PaymentRunTitle({ on }: { on: string }) {
  const { t, locale } = useI18n();
  // The server's day: a day already past is asked as today.
  const day = usePaymentRun(on).data?.on ?? on;
  return <>{t.run.on(formatDays(day, null, locale))}</>;
}

/** The whole run, in the canvas: the bar, each bill with its call, what is held, and the approval
 * at the foot, where the thumb is. */
export function PaymentRunView({ on }: { on: string }) {
  const { t } = useI18n();
  const run = usePaymentRun(on);
  if (run.isPending) return <Skeleton className="m-4 h-64 rounded-2xl" />;
  if (!run.data) return <p className="text-danger-foreground p-4 text-sm">{t.errors.load}</p>;
  const data = run.data;
  const chosen = toApprove(data);
  const fits = fitsOf(data);
  return (
    <div className="flex min-h-full flex-col">
      <div className="grid gap-4 p-4">
        <CallCounts decisions={[...data.items.map((item) => item.decision), ...data.held]} fits={fits} />
        <CashBar run={data} />
        {data.items.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t.run.nothing}</p>
        ) : (
          <ol className="divide-border -mx-2 divide-y">
            {data.items.map((item) => (
              <li key={item.decision.id} className="py-1">
                <DecisionRow decision={item.decision} payment={undefined} fits={fits.get(item.decision.id) ?? null} inRun />
              </li>
            ))}
          </ol>
        )}
        {data.held.length > 0 ? (
          <section className="grid gap-1">
            <h3 className="text-muted-foreground text-xs font-medium tracking-[0.08em] uppercase">{t.run.held}</h3>
            <ol className="divide-border -mx-2 divide-y">
              {data.held.map((decision) => (
                <li key={decision.id} className="py-1">
                  <DecisionRow decision={decision} payment={undefined} />
                </li>
              ))}
            </ol>
          </section>
        ) : null}
      </div>
      <footer className="border-border bg-card sticky bottom-0 mt-auto flex flex-wrap items-center gap-3 border-t px-4 py-3">
        <ApproveMany items={chosen} />
        <Settles items={chosen} />
      </footer>
    </div>
  );
}
