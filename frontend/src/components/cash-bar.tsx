"use client";

import { callOf } from "@/components/bill-call";
import type { PaymentRun, RunItem } from "@/lib/api";
import { formatMoneyShort } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * The run's money as one bar (docs/architecture/05, The payment run): what the accounts hold, the
 * bills Tamga would pay now, those that wait for the owner, and the reserve's line. Every figure is
 * the server's, in the policy's currency; the bar only adds them up.
 */
export type Weighed = {
  balance: number;
  spare: number;
  approved: number;
  payNow: number;
  /** Waiting bills Tamga would hold for now. */
  hold: number;
  /** Waiting bills that are the owner's call. */
  needsYou: number;
  /** What paying every bill waiting would take below the reserve's line; 0 when it all fits. */
  dip: number;
  /** What the reserve keeps: the bills outside the run, and its floor. */
  kept: number;
  /** The bills Tamga would pay now that fit, the ones "Approve N" answers. */
  toApprove: RunItem[];
  /** Whether each bill Tamga would pay fits, weighed only against what is approved and the
   * bills above it that Tamga would pay too: a bill it would hold takes nothing from the spare.
   * Every other bill keeps the server's word. */
  fits: Map<string, boolean | null>;
};

const amountOf = (item: RunItem) => (item.in_policy_currency === null ? 0 : Number(item.in_policy_currency));

export function weigh(run: PaymentRun): Weighed | null {
  if (run.balance === null || run.spare === null) return null;
  let approved = 0;
  let payNow = 0;
  let hold = 0;
  let needsYou = 0;
  const toApprove: RunItem[] = [];
  const spare = Number(run.spare);
  const fits = new Map(run.items.map((item) => [item.decision.id, item.fits]));
  for (const item of run.items) {
    if (item.decision.state === "approved") approved += amountOf(item);
  }
  // In the run's order, the oldest due first, as the server weighs them.
  for (const item of run.items) {
    if (item.decision.state !== "awaiting_owner") continue;
    const call = callOf(item.decision).call;
    if (call === "pay") {
      // A bill with no rate is never weighed, nor approved at a guessed one.
      const fit = item.in_policy_currency === null ? null : approved + payNow + amountOf(item) <= spare;
      fits.set(item.decision.id, fit);
      if (fit) {
        payNow += amountOf(item);
        toApprove.push(item);
        continue;
      }
    }
    if (call === "hold") hold += amountOf(item);
    else needsYou += amountOf(item);
  }
  return {
    balance: Number(run.balance),
    spare,
    approved,
    payNow,
    hold,
    needsYou,
    dip: Math.max(0, approved + payNow + needsYou + hold - spare),
    kept: run.kept === null ? Math.max(0, Number(run.balance) - spare) : Number(run.kept),
    toApprove,
    fits,
  };
}

export function CashBar({ run, className }: { run: PaymentRun; className?: string }) {
  const { t, locale } = useI18n();
  const weighed = weigh(run);
  const money = (amount: number) => formatMoneyShort(amount, run.currency, locale);
  if (!weighed) return <p className={cn("text-muted-foreground text-sm", className)}>{t.run.balanceUnknown}</p>;
  const { balance, spare, approved, payNow, hold, needsYou, dip, kept } = weighed;
  const scale = Math.max(balance, approved + payNow + needsYou + hold, 1);
  const at = (amount: number) => `${Math.min(100, Math.max(0, (amount / scale) * 100))}%`;
  const line = Math.max(0, spare);
  return (
    <div className={cn("grid gap-2", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-muted-foreground text-xs font-medium tracking-[0.08em] uppercase">{t.run.cashVsProposed}</span>
        <span className="text-muted-foreground text-xs tabular-nums">{t.run.available(money(balance))}</span>
      </div>
      <div
        role="img"
        aria-label={t.run.barLabel(money(approved + payNow), money(needsYou + hold), money(kept))}
        className="bg-muted relative h-2.5 overflow-hidden rounded-full"
      >
        {/* What the reserve keeps: past its line, hatched. */}
        <span
          className="absolute inset-y-0 [background:repeating-linear-gradient(135deg,var(--border)_0_2px,transparent_2px_6px)]"
          style={{ left: at(line), width: at(balance - line) }}
        />
        <span className="bg-success absolute inset-y-0 left-0" style={{ width: at(approved + payNow) }} />
        <span
          className="bg-danger/70 absolute inset-y-0"
          style={{ left: at(approved + payNow), width: at(needsYou) }}
        />
        <span
          className="bg-warning/60 absolute inset-y-0"
          style={{ left: at(approved + payNow + needsYou), width: at(hold) }}
        />
        <span className="bg-foreground absolute -inset-y-0.5 w-0.5" style={{ left: at(line) }} aria-hidden />
      </div>
      <ul className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
        <li className="flex items-center gap-1.5">
          <span className="bg-success size-2 rounded-sm" aria-hidden />
          {t.run.legendPay(money(approved + payNow))}
        </li>
        {needsYou > 0 ? (
          <li className="flex items-center gap-1.5">
            <span className="bg-danger/70 size-2 rounded-sm" aria-hidden />
            {t.run.legendNeeds(money(needsYou))}
          </li>
        ) : null}
        {hold > 0 ? (
          <li className="flex items-center gap-1.5">
            <span className="bg-warning/60 size-2 rounded-sm" aria-hidden />
            {t.run.legendHold(money(hold))}
          </li>
        ) : null}
        <li className="flex items-center gap-1.5">
          <span className="bg-border size-2 rounded-sm" aria-hidden />
          {t.run.legendReserve(money(kept))}
        </li>
      </ul>
      <p className={cn("text-xs", dip > 0 ? "text-danger-foreground" : "text-muted-foreground")}>
        {dip > 0 ? t.run.wouldDip(money(dip)) : t.run.allFits(money(Math.max(0, spare - approved - payNow - needsYou - hold)))}
      </p>
    </div>
  );
}
