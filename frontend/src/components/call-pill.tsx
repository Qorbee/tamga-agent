"use client";

import type { BillCall, Call } from "@/components/bill-call";
import type { Decision } from "@/lib/api";
import { useCustomResultWords } from "@/components/custom-rule";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** Money in and agreed is sage, waiting on a reason ochre, the owner's turn terracotta, done or
 * set aside neutral (the Books review's palette, `ui-direction`). */
const TONE: Record<Call, string> = {
  pay: "bg-success-soft text-success-foreground",
  pay_part: "bg-success-soft text-success-foreground",
  approved: "bg-success-soft text-success-foreground",
  paid: "bg-success-soft text-success-foreground",
  hold: "bg-warning-soft text-warning-foreground",
  held: "bg-warning-soft text-warning-foreground",
  needs_you: "bg-danger-soft text-danger-foreground",
  failed: "bg-danger-soft text-danger-foreground",
  rejected: "bg-muted text-muted-foreground",
};

const DOT: Record<Call, string> = {
  pay: "bg-success",
  pay_part: "bg-success",
  approved: "bg-success",
  paid: "bg-success",
  hold: "bg-warning",
  held: "bg-warning",
  needs_you: "bg-danger",
  failed: "bg-danger",
  rejected: "bg-muted-foreground",
};

/** Tamga's call as a pill; with `count`, how many bills have it, inside the pill, so the two
 * never wrap apart. */
export function CallPill({ call, count, className }: { call: Call; count?: number; className?: string }) {
  const { t } = useI18n();
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONE[call],
        className,
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", DOT[call])} />
      {count !== undefined ? <span className="font-semibold tabular-nums">{count}</span> : null}
      {t.payments.calls[call]}
    </span>
  );
}

/** The few words beside the pill: the rule behind the call, what made a bill ready to pay, or,
 * where only the approval limit asks, the controller's own reason (in Tamga's language, so the
 * caller shows it translated). */
export type CallReason = { text: string; advice: boolean } | null;

export function useCallReason(): (decision: Decision, bill: BillCall) => CallReason {
  const { t } = useI18n();
  const customWords = useCustomResultWords();
  return (decision, bill) => {
    const word = (text: string | null | undefined): CallReason => (text ? { text, advice: false } : null);
    if (bill.unchecked) return word(t.payments.reserveUnchecked);
    if (bill.call === "paid" || bill.call === "approved") return null;
    if (bill.call === "pay" || bill.call === "pay_part") {
      const passed = (id: string) => decision.rules.some((rule) => rule.rule === id && rule.outcome === "ok");
      return word(passed("client_paid") && passed("proof_of_delivery") ? t.payments.readyToPay : t.payments.rulesPassed);
    }
    const advice = decision.advice?.evaluation === decision.evaluation ? decision.advice : null;
    if ((bill.call === "hold" || bill.call === "needs_you") && advice && (!bill.rule || bill.rule === "approval_limit"))
      return { text: advice.reason, advice: true };
    if (bill.rule?.startsWith("custom:")) return word(customWords(bill.rule) ?? t.settings.yourRules.ownRule);
    if (bill.rule) return word(t.payments.rules[bill.rule]);
    if (bill.call === "held" && decision.decided_by) return word(t.payments.heldByYou);
    return null;
  };
}
