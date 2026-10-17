"use client";

import { AiIdeaIcon, ArrowDown01Icon, LinkSquare02Icon, MoreHorizontalIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { callOf, waits } from "@/components/bill-call";
import { CallPill, useCallReason } from "@/components/call-pill";
import { CanvasLink } from "@/components/canvas-link";
import { DayInput } from "@/components/day-calendar";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { T } from "@/components/translated";
import {
  ApiError,
  answerDecision,
  getPolicy,
  listMoneyAccounts,
  type Decision,
  type DecisionAction,
  type Payment,
  type PaymentMethod,
} from "@/lib/api";
import { formatDate, formatDays, formatMoney, formatUsdc } from "@/lib/format";
import { useCustomResultWords } from "@/components/custom-rule";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

/**
 * One bill's decision (docs/architecture/05), read at a glance: who, how much in the bill's own
 * currency, and Tamga's call as a pill with a few words of why; the rest (the controller's reason,
 * the rules, how it is paid, its transaction) opens under "Why?". The buttons follow the call: a
 * bill Tamga would hold offers Hold first and paying it as the second choice. An approval is
 * confirmed first, since a payment cannot be taken back; each answer names the evaluation shown,
 * and one evaluated again meanwhile is refused and read again. A bill paid by bank is the owner's
 * to pay: they mark it paid, or the bank's movement settles it (ADR-0027).
 *
 * `fits` is the payment run's word on the reserve, where the row sits in one.
 */
export function DecisionRow({
  decision,
  payment: given,
  mirror,
  fits = null,
  actions = true,
  inRun = false,
}: {
  decision: Decision;
  payment: Payment | undefined;
  /** In shadow, the test USDC released on Arc beside the bank's payment: a record, never the bill's payment. */
  mirror?: Payment;
  fits?: boolean | null;
  /** False: the row only shows (a chat's view), and the owner answers where it links to. */
  actions?: boolean;
  /** In a run, the bills Tamga would pay are approved together by "Approve N": their rows offer
   * no button of their own, and the menu still holds every answer. */
  inRun?: boolean;
}) {
  const { t, locale } = useI18n();
  const customWords = useCustomResultWords();
  // A payment the owner undid is kept for the record, and shows as none.
  const payment = given?.voided_at ? undefined : given;
  const isOwner = useIsOwner();
  const queryClient = useQueryClient();
  const reasonOf = useCallReason();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [marking, setMarking] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const bill = decision.invoice ?? null;
  const firm = bill?.party_name ?? "—";
  const usdc = decision.usdc_units !== null ? formatUsdc(decision.usdc_units, locale) : null;
  const money = (amount: string | null) =>
    amount !== null && decision.currency ? formatMoney(Number(amount), decision.currency, locale) : null;

  const answer = useMutation({
    mutationFn: (body: Omit<DecisionAction, "evaluation">) =>
      answerDecision(decision.id, { ...body, evaluation: decision.evaluation }),
    onSuccess: () => {
      setNote(null);
      setConfirming(false);
      setEditing(false);
      setMarking(false);
      setUndoing(false);
    },
    // Evaluated again meanwhile: the list is read again and the owner answers what it says now.
    onError: (error) => {
      setConfirming(false);
      setMarking(false);
      setNote(error instanceof ApiError && error.status === 409 ? t.payments.changed : t.payments.failed);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["decision"] });
      void queryClient.invalidateQueries({ queryKey: ["payments"] });
      void queryClient.invalidateQueries({ queryKey: ["payment-run"] });
    },
  });

  const call = callOf(decision, fits);
  const reason = reasonOf(decision, call);
  const stopping = decision.rules.filter((rule) => rule.outcome === "hold" || rule.outcome === "reject");
  const byOwner = decision.decided_by !== null;
  const byBank = decision.method === "bank_transfer";
  // The executor's, or done: nobody answers it any more; one approved to pay by bank waits for the
  // owner's payment, which only Mark paid answers.
  const answered = decision.state === "approved" || decision.state === "settled" || decision.state === "paid_in_books";
  // The owner's own hold or rejection is theirs to take back; a rule's is not.
  // What leaves the account must be known: never approved at a guessed rate.
  const approvable =
    (byBank ? decision.pay_amount !== null : usdc !== null) &&
    stopping.length === 0 &&
    (decision.state === "awaiting_owner" ||
      ((decision.state === "held" || decision.state === "rejected") && byOwner));
  const holdable = decision.state === "awaiting_owner" || (decision.state === "rejected" && byOwner);
  const markable = byBank && (approvable || decision.state === "approved");
  // Marked paid by mistake, or a movement matched to the wrong bill: the owner takes it back. A
  // vault payment is on the chain and has no undo.
  const undoable = byBank && decision.state === "settled" && payment?.method === "bank_transfer";
  const paidFrom =
    decision.pay_amount !== null && decision.pay_currency ? formatMoney(Number(decision.pay_amount), decision.pay_currency, locale) : null;
  // What leaves the company, under the bill's own amount: test USDC from the vault, or the bank
  // amount when it is paid from an account in another currency.
  const secondary =
    decision.state === "paid_in_books"
      ? null
      : !byBank
        ? usdc
        : decision.pay_currency && decision.pay_currency !== decision.currency
          ? (paidFrom ? `≈ ${paidFrom}` : null)
          : t.payments.methods.bank_transfer;
  // How it is paid, in words, for "Why?".
  const how =
    decision.state === "paid_in_books"
      ? null
      : !byBank
        ? usdc !== null
          ? t.payments.fromVault(usdc)
          : null
        : decision.pay_currency === decision.currency || !decision.pay_currency
          ? t.payments.methods.bank_transfer
          : paidFrom && decision.pay_rate_on
            ? t.payments.byBankAbout(paidFrom, formatDays(decision.pay_rate_on, null, locale))
            : t.payments.byBankNoRate(decision.pay_currency);
  const status =
    decision.state === "paid_in_books"
      ? bill?.settled === "paid_on_account"
        ? t.payments.paidOnAccount
        : t.payments.paidInBooks
      : decision.state === "approved"
        ? byBank
          ? t.payments.waitsForBank
          : t.payments.beingPaid
        : decision.state === "rejected"
          ? byOwner
            ? t.payments.rejectedByYou
            : t.payments.rejected
          : decision.state === "failed"
            ? t.payments.refused
            : null;
  // The controller's opinion on the evaluation shown: it moves nothing, the owner answers.
  const advice = !answered && decision.advice?.evaluation === decision.evaluation ? decision.advice : null;
  const part = advice?.proposal === "pay_part" ? money(advice.amount) : null;
  // Tamga would wait: holding is the first answer offered, paying it the second.
  const holdFirst = call.call === "hold";
  const together = inRun && call.call === "pay" && !byBank;
  const showActions =
    actions && !together && isOwner && (approvable || markable || (holdFirst && holdable)) && !editing && !marking;

  return (
    <>
      <div className="flex items-start gap-1">
        <CanvasLink
          view={{ kind: "invoice", id: decision.invoice_id }}
          className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex min-w-0 flex-1 items-start gap-3 rounded-xl px-2 py-2 transition-colors"
        >
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="truncate text-sm font-medium">{firm}</span>
              <span className="text-muted-foreground hidden shrink-0 font-mono text-xs sm:inline">{bill?.number ?? t.books.noNumber}</span>
            </span>
            <span className="mt-1 flex min-w-0 items-center gap-2">
              <CallPill call={call.call} />
              {reason ? (
                <span className="text-muted-foreground min-w-0 truncate text-xs">
                  {reason.advice ? <T>{reason.text}</T> : reason.text}
                </span>
              ) : bill?.due_date && waits(call.call) ? (
                <span className="text-muted-foreground truncate text-xs">{t.books.dueOn(formatDays(bill.due_date, null, locale))}</span>
              ) : null}
            </span>
          </span>
          {/* The bill's own amount and currency lead; what leaves the company follows (ADR-0027,
              point 8). Paid outside Tamga, the bill's own total is all there is to show. */}
          <span className="shrink-0 text-end tabular-nums">
            <span className="block text-sm font-medium">
              {(decision.state === "paid_in_books" ? money(bill?.total_amount ?? decision.amount) : money(decision.amount)) ?? "—"}
            </span>
            {decision.amount_edited && bill?.total_amount ? (
              <span className="text-muted-foreground block text-xs">{t.payments.partOf(money(bill.total_amount) ?? "")}</span>
            ) : secondary ? (
              <span className="text-muted-foreground block text-xs">{secondary}</span>
            ) : null}
          </span>
        </CanvasLink>
        {actions && isOwner && (undoable || !answered) && !undoing ? (
          <DropdownMenu modal={false}>
            <Hint label={t.payments.actions}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground mt-1"
                  aria-label={t.payments.actions}
                  disabled={answer.isPending}
                >
                  <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                </Button>
              </DropdownMenuTrigger>
            </Hint>
            <DropdownMenuContent align="end">
              {undoable ? <DropdownMenuItem onSelect={() => setUndoing(true)}>{t.payments.undoPaid}</DropdownMenuItem> : null}
              {!answered ? (
                <>
                  {together && approvable ? (
                    <DropdownMenuItem onSelect={() => setConfirming(true)}>{t.payments.approveThis}</DropdownMenuItem>
                  ) : null}
                  <PayByItems decision={decision} onPick={(body) => answer.mutate(body)} />
                  {holdable && !holdFirst ? (
                    <DropdownMenuItem onSelect={() => answer.mutate({ action: "hold" })}>{t.payments.hold}</DropdownMenuItem>
                  ) : null}
                  <DropdownMenuItem onSelect={() => setEditing(true)}>{t.payments.changeAmount}</DropdownMenuItem>
                  {decision.state !== "rejected" ? (
                    <DropdownMenuItem onSelect={() => answer.mutate({ action: "reject" })}>{t.payments.reject}</DropdownMenuItem>
                  ) : null}
                </>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="text-muted-foreground hover:text-foreground ms-2 -mt-1 mb-1 inline-flex items-center gap-0.5 text-xs"
      >
        {open ? t.payments.hideWhy : t.payments.why}
        <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open ? (
        <div className="text-muted-foreground grid gap-1.5 px-2 pb-2 text-xs">
          {advice ? (
            <p className="flex items-start gap-1.5">
              <HugeiconsIcon icon={AiIdeaIcon} strokeWidth={1.75} className="text-primary mt-px size-3.5 shrink-0" aria-hidden />
              <span className="min-w-0 text-pretty">
                <span className="text-foreground font-medium">
                  {advice.proposal === "pay_part" ? t.payments.advice.payPart(part ?? "") : t.payments.advice[advice.proposal]}
                </span>
                {" · "}
                <T>{advice.reason}</T>
              </span>
            </p>
          ) : null}
          {stopping.length > 0 && !answered ? (
            <ul className="grid gap-0.5">
              {stopping.map((rule) => (
                <li key={rule.rule} className="text-warning-foreground flex items-center gap-1.5">
                  <span className="bg-warning size-1.5 shrink-0 rounded-full" aria-hidden />
                  {customWords(rule.rule) ?? t.payments.rules[rule.rule] ?? rule.reason}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="flex flex-wrap gap-x-3">
            <span className="font-mono sm:hidden">{bill?.number ?? t.books.noNumber}</span>
            {bill?.due_date ? <span>{t.books.dueOn(formatDays(bill.due_date, null, locale))}</span> : null}
            {how ? <span>{how}</span> : null}
            {status ? <span>{status}</span> : null}
          </p>
          {/* The vault's own words, for whoever looks into it. */}
          {decision.state === "failed" && payment?.failure ? <p className="font-mono break-words">{payment.failure}</p> : null}
        </div>
      ) : null}

      {payment && payment.method === "bank_transfer" && payment.paid_on ? (
        <p className="text-muted-foreground px-2 pb-2 text-xs">
          {t.payments.paidByBank(formatDays(payment.paid_on, null, locale))}
          {payment.pay_amount && payment.pay_currency
            ? ` · ${formatMoney(Number(payment.pay_amount), payment.pay_currency, locale)}`
            : null}
        </p>
      ) : null}

      {payment?.explorer_url ? (
        <p className="text-muted-foreground flex flex-wrap items-center gap-x-3 px-2 pb-2 text-xs">
          {payment.finished_at ? <span>{t.payments.paidOn(formatDate(new Date(payment.finished_at), locale))}</span> : null}
          <a
            href={payment.explorer_url}
            target="_blank"
            rel="noreferrer"
            className="text-foreground inline-flex items-center gap-1 underline-offset-4 hover:underline"
          >
            {t.payments.viewTx}
            <HugeiconsIcon icon={LinkSquare02Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
          </a>
        </p>
      ) : null}

      {mirror?.explorer_url ? (
        <p className="text-muted-foreground flex flex-wrap items-center gap-x-3 px-2 pb-2 text-xs">
          <span>
            {t.payments.mirrored}
            {mirror.pay_amount ? ` · ${formatMoney(Number(mirror.pay_amount), "USDC", locale)}` : null}
          </span>
          <a
            href={mirror.explorer_url}
            target="_blank"
            rel="noreferrer"
            className="text-foreground inline-flex items-center gap-1 underline-offset-4 hover:underline"
          >
            {t.payments.viewTx}
            <HugeiconsIcon icon={LinkSquare02Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
          </a>
        </p>
      ) : null}

      {editing ? (
        <AmountForm
          initial={decision.amount}
          pending={answer.isPending}
          onCancel={() => setEditing(false)}
          onSave={(amount) => answer.mutate({ action: "edit", amount })}
        />
      ) : null}

      {showActions ? (
        <div className="flex flex-wrap gap-2 px-2 pb-2">
          {holdFirst && holdable ? (
            <Button size="sm" disabled={answer.isPending} onClick={() => answer.mutate({ action: "hold" })}>
              {t.payments.hold}
            </Button>
          ) : null}
          {approvable ? (
            <Button
              size="sm"
              variant={holdFirst ? "outline" : "default"}
              disabled={answer.isPending}
              onClick={() => setConfirming(true)}
            >
              {holdFirst ? t.payments.payAnyway : t.payments.approve}
            </Button>
          ) : null}
          {part && approvable && advice?.amount ? (
            <Button
              size="sm"
              variant="outline"
              disabled={answer.isPending}
              onClick={() => answer.mutate({ action: "edit", amount: advice.amount ?? decision.amount })}
            >
              {t.payments.payPartInstead(part)}
            </Button>
          ) : null}
          {call.call === "needs_you" && holdable ? (
            <Button size="sm" variant="outline" disabled={answer.isPending} onClick={() => answer.mutate({ action: "hold" })}>
              {t.payments.hold}
            </Button>
          ) : null}
          {markable ? (
            <Button
              size="sm"
              variant={approvable ? "outline" : "default"}
              disabled={answer.isPending}
              onClick={() => setMarking(true)}
            >
              {t.payments.markPaid}
            </Button>
          ) : null}
        </div>
      ) : null}

      {marking ? (
        <MarkPaidForm
          decision={decision}
          firm={firm}
          pending={answer.isPending}
          onCancel={() => setMarking(false)}
          onSave={(paid) => answer.mutate({ action: "mark_paid", ...paid })}
        />
      ) : null}

      {undoing ? (
        <UndoPaidForm
          pending={answer.isPending}
          onCancel={() => setUndoing(false)}
          onUndo={(reason) => answer.mutate({ action: "undo_paid", ...(reason ? { reason } : {}) })}
        />
      ) : null}

      {note ? (
        <p className="text-danger-foreground px-2 pb-2 text-xs" role="alert">
          {note}
        </p>
      ) : null}

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {byBank ? t.payments.confirmTitleBank(paidFrom ?? money(decision.amount) ?? "", firm) : t.payments.confirmTitle(usdc ?? "", firm)}
            </DialogTitle>
            <DialogDescription>
              {(byBank ? t.payments.confirmBodyBank : t.payments.confirmBody)(bill?.number ?? t.books.noNumber)}
              {holdFirst ? ` ${t.payments.againstAdvice}` : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              {t.payments.cancel}
            </Button>
            <Button disabled={answer.isPending} onClick={() => answer.mutate({ action: "approve" })}>
              {t.payments.approve}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** A new amount for the bill, a part payment: a new proposal the rules evaluate again. */
function AmountForm({
  initial,
  pending,
  onCancel,
  onSave,
}: {
  initial: string;
  pending: boolean;
  onCancel: () => void;
  onSave: (amount: string) => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(initial);
  const amount = value.trim().replace(",", ".");
  const valid = /^\d+(\.\d{1,2})?$/.test(amount) && Number(amount) > 0;
  return (
    <form
      className="flex flex-wrap items-center gap-2 px-2 pb-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSave(amount);
      }}
    >
      <Input
        inputMode="decimal"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-label={t.payments.amount}
        className="w-36"
        autoFocus
      />
      <Button type="submit" size="sm" disabled={!valid || pending}>
        {t.project.save}
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
        {t.payments.cancel}
      </Button>
    </form>
  );
}

/** The other ways to pay it the policy allows, and the other currencies the company holds an
 * account in: each a new proposal, every rule run again. */
function PayByItems({
  decision,
  onPick,
}: {
  decision: Decision;
  onPick: (body: Omit<DecisionAction, "evaluation">) => void;
}) {
  const { t } = useI18n();
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const accounts = useQuery({ queryKey: ["money-accounts"], queryFn: listMoneyAccounts, retry: false });
  const others = (policy.data?.methods ?? []).filter((method: PaymentMethod) => method !== decision.method);
  const currencies =
    decision.method === "bank_transfer"
      ? [
          ...new Set(
            (accounts.data?.items ?? [])
              .filter((account) => account.kind === "bank" || account.kind === "cash")
              .map((account) => account.currency),
          ),
        ].filter((currency) => currency !== decision.pay_currency && /^[A-Z]{3}$/.test(currency))
      : [];
  return (
    <>
      {others.map((method) => (
        <DropdownMenuItem key={method} onSelect={() => onPick({ action: "pay_by", method })}>
          {t.payments.payBy[method]}
        </DropdownMenuItem>
      ))}
      {currencies.map((currency) => (
        <DropdownMenuItem
          key={currency}
          onSelect={() => onPick({ action: "pay_by", method: "bank_transfer", pay_currency: currency })}
        >
          {t.payments.payFrom(currency)}
        </DropdownMenuItem>
      ))}
    </>
  );
}

/** What left the owner's account for the bill, and the day: Tamga records it, nothing is sent. */
function MarkPaidForm({
  decision,
  firm,
  pending,
  onCancel,
  onSave,
}: {
  decision: Decision;
  firm: string;
  pending: boolean;
  onCancel: () => void;
  onSave: (paid: { pay_amount: string; pay_currency: string; paid_on?: string }) => void;
}) {
  const { t } = useI18n();
  const [amount, setAmount] = useState(
    decision.pay_amount !== null ? Number(decision.pay_amount).toFixed(2) : decision.amount,
  );
  const [currency, setCurrency] = useState(decision.pay_currency ?? decision.currency ?? "");
  // Empty: today on the company's own clock, which the server knows (a sandbox's may run ahead).
  const [day, setDay] = useState("");
  const figure = amount.trim().replace(",", ".");
  const valid =
    /^\d+(\.\d{1,6})?$/.test(figure) && Number(figure) > 0 && /^[A-Z]{3}$/.test(currency);
  return (
    <form
      className="grid gap-2 px-2 pb-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSave({ pay_amount: figure, pay_currency: currency, ...(day ? { paid_on: day } : {}) });
      }}
    >
      <p className="text-sm font-medium">{t.payments.markPaidTitle(firm)}</p>
      <p className="text-muted-foreground text-xs text-pretty">{t.payments.markPaidBody}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          inputMode="decimal"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          aria-label={t.payments.paidAmount}
          className="w-32"
          autoFocus
        />
        <Input
          value={currency}
          onChange={(event) => setCurrency(event.target.value.toUpperCase().slice(0, 3))}
          aria-label={t.payments.paidCurrency}
          className="w-20 uppercase"
        />
        <DayInput value={day} onChange={setDay} label={t.payments.paidDay} className="w-40" />
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!valid || pending}>
          {t.payments.markPaid}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t.payments.cancel}
        </Button>
      </div>
    </form>
  );
}

/** Takes back a bank payment: the owner's reason, if they give one, goes on the log. */
function UndoPaidForm({
  pending,
  onCancel,
  onUndo,
}: {
  pending: boolean;
  onCancel: () => void;
  onUndo: (reason: string) => void;
}) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  return (
    <form
      className="grid gap-2 px-2 pb-2"
      onSubmit={(event) => {
        event.preventDefault();
        onUndo(reason.trim());
      }}
    >
      <p className="text-sm font-medium">{t.payments.undoTitle}</p>
      <p className="text-muted-foreground text-xs text-pretty">{t.payments.undoBody}</p>
      <Textarea
        value={reason}
        maxLength={300}
        rows={2}
        onChange={(event) => setReason(event.target.value)}
        placeholder={t.payments.undoReason}
        aria-label={t.payments.undoReason}
        aria-describedby="undo-reason-hint"
        autoFocus
      />
      <p id="undo-reason-hint" className="text-muted-foreground text-xs">
        {t.payments.undoReasonHint}
      </p>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="outline" disabled={pending}>
          {t.payments.undo}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t.payments.cancel}
        </Button>
      </div>
    </form>
  );
}
