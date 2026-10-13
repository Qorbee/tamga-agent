import type { Decision } from "@/lib/api";

/**
 * Tamga's call on a bill, in one word the owner reads at a glance (docs/architecture/05): what
 * the rules allow, what the controller advises on the evaluation in force, and what the owner
 * already answered. It only labels; what can be approved is still the server's to decide.
 */
export type Call =
  | "pay"
  | "pay_part"
  | "hold"
  | "needs_you"
  | "held"
  | "rejected"
  | "approved"
  | "paid"
  | "failed";

export type BillCall = {
  call: Call;
  /** The rule behind the call, by its id, when one is; else the advice's reason. */
  rule: string | null;
  /** The reserve rule could not weigh the bill: no balance or no rate. Never said as breached. */
  unchecked: boolean;
};

// The approval limit asks the owner of every bill above it: the reason least worth a pill.
const QUIET = new Set(["approval_limit"]);

export function callOf(decision: Decision, fits: boolean | null = null): BillCall {
  // The worst outcome decides: a rejection names its own rule before any hold.
  const stopping =
    decision.rules.find((rule) => rule.outcome === "reject") ??
    decision.rules.find((rule) => rule.outcome === "hold");
  const asking = [...decision.rules]
    .filter((rule) => rule.outcome === "propose")
    .sort((a, b) => Number(QUIET.has(a.rule)) - Number(QUIET.has(b.rule)))[0];
  // The reserve rule says "cannot be checked" in its reason when it had nothing to weigh with.
  const unchecked = (rule: { rule: string; reason: string } | undefined) =>
    rule?.rule === "reserve" && /not checked|cannot be checked/.test(rule.reason);
  const of = (call: Call, rule: { rule: string; reason: string } | undefined = undefined): BillCall => ({
    call,
    rule: rule?.rule ?? null,
    unchecked: unchecked(rule),
  });

  switch (decision.state) {
    case "settled":
    case "paid_in_books":
      return of("paid");
    case "approved":
      return of("approved");
    case "failed":
      return of("failed");
    case "rejected":
      return of("rejected", stopping);
    case "held":
      return of("held", stopping);
    default:
      break;
  }
  if (stopping) return of("held", stopping);
  // The controller's opinion counts only for the evaluation shown.
  const advice = decision.advice?.evaluation === decision.evaluation ? decision.advice : null;
  if (advice?.proposal === "pay") {
    if (fits === false) return of("needs_you", decision.rules.find((rule) => rule.rule === "reserve") ?? asking);
    return of("pay", asking);
  }
  if (advice?.proposal === "pay_part") return of("pay_part", asking);
  if (advice?.proposal === "hold") return of("hold", asking);
  return of("needs_you", asking);
}

/** The calls that still wait for the owner's answer. */
export function waits(call: Call): boolean {
  return call === "pay" || call === "pay_part" || call === "hold" || call === "needs_you";
}
