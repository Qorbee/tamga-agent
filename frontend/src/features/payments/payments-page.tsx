"use client";

import { LinkSquare02Icon, MoreHorizontalIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
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
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  ApiError,
  answerDecision,
  listDecisions,
  listPayments,
  type Decision,
  type DecisionAction,
  type Payment,
} from "@/lib/api";
import { formatDate, formatDays, formatMoney, formatUsdc } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { usePagedList } from "@/lib/use-paged-list";
import { useIsOwner } from "@/lib/use-role";
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
  const paid = view === "paid" ? (decisions.items ?? []).map((decision) => decision.id) : [];
  const payments = useQuery({
    queryKey: ["payments", { decisions: paid }],
    queryFn: () => listPayments({ decision_id: paid, limit: 200 }),
    enabled: paid.length > 0,
  });
  const settled = new Map(
    (payments.data?.items ?? [])
      .filter((payment) => payment.state === "settled")
      .map((payment) => [payment.decision_id, payment]),
  );

  if (decisions.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!decisions.items) return <Skeleton className="h-40 rounded-2xl" />;
  if (decisions.items.length === 0)
    return <p className="text-muted-foreground py-10 text-center text-sm">{t.payments.empty[view]}</p>;
  return (
    <div className="grid gap-3">
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {decisions.items.map((decision) => (
          <DecisionRow key={decision.id} decision={decision} payment={settled.get(decision.id)} />
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

/** One bill's decision: who and how much, why it waits or is held, and the owner's answers. */
function DecisionRow({ decision, payment }: { decision: Decision; payment: Payment | undefined }) {
  const { t, locale } = useI18n();
  const isOwner = useIsOwner();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
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
    },
    // Evaluated again meanwhile: the list is read again and the owner answers what it says now.
    onError: (error) => {
      setConfirming(false);
      setNote(error instanceof ApiError && error.status === 409 ? t.payments.changed : t.payments.failed);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["payments"] });
    },
  });

  const stopping = decision.rules.filter((rule) => rule.outcome === "hold" || rule.outcome === "reject");
  const byOwner = decision.decided_by !== null;
  // The executor's, or done: nobody answers it any more.
  const answered = decision.state === "approved" || decision.state === "settled";
  const approvable =
    usdc !== null &&
    stopping.length === 0 &&
    (decision.state === "awaiting_owner" || (decision.state === "held" && byOwner));
  const status =
    decision.state === "approved"
      ? t.payments.beingPaid
      : decision.state === "held" && byOwner
        ? t.payments.heldByYou
        : decision.state === "rejected"
          ? byOwner
            ? t.payments.rejectedByYou
            : t.payments.rejected
          : decision.state === "failed"
            ? t.payments.refused
            : null;

  return (
    <li className="py-1 ps-2 pe-3">
      <div className="flex items-start gap-1">
        <CanvasLink
          view={{ kind: "invoice", id: decision.invoice_id }}
          className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex min-w-0 flex-1 items-start gap-3 rounded-xl px-2 py-2.5 transition-colors"
        >
          <span className="min-w-0 flex-1">
            <span className="text-muted-foreground flex flex-wrap items-baseline gap-x-2 text-xs">
              <span className="font-mono whitespace-nowrap">{bill?.number ?? t.books.noNumber}</span>
              {bill?.due_date ? (
                <span className="whitespace-nowrap">{t.books.dueOn(formatDays(bill.due_date, null, locale))}</span>
              ) : null}
            </span>
            <span className="block text-base font-medium text-pretty break-words">{firm}</span>
            {status ? <span className="text-muted-foreground mt-1 block text-xs">{status}</span> : null}
            {stopping.length > 0 && !answered ? (
              <ul className="mt-1 grid gap-0.5 text-xs">
                {stopping.map((rule) => (
                  <li key={rule.rule} className="text-warning-foreground flex items-center gap-1.5">
                    <span className="bg-warning size-1.5 shrink-0 rounded-full" aria-hidden />
                    {t.payments.rules[rule.rule] ?? rule.reason}
                  </li>
                ))}
              </ul>
            ) : null}
          </span>
          {/* What moves is the stablecoin; the bill's own figure is beside it (07, Currency). */}
          <span className="shrink-0 text-end tabular-nums">
            <span className="block text-sm font-medium">{usdc ?? "—"}</span>
            <span className="text-muted-foreground block text-xs">{money(decision.amount)}</span>
            {decision.amount_edited && bill?.total_amount ? (
              <span className="text-muted-foreground block text-xs">{t.payments.partOf(money(bill.total_amount) ?? "")}</span>
            ) : null}
          </span>
        </CanvasLink>
        {isOwner && !answered ? (
          <DropdownMenu modal={false}>
            <Hint label={t.payments.actions}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground mt-1.5"
                  aria-label={t.payments.actions}
                  disabled={answer.isPending}
                >
                  <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                </Button>
              </DropdownMenuTrigger>
            </Hint>
            <DropdownMenuContent align="end">
              {decision.state === "awaiting_owner" ? (
                <DropdownMenuItem onSelect={() => answer.mutate({ action: "hold" })}>{t.payments.hold}</DropdownMenuItem>
              ) : null}
              <DropdownMenuItem onSelect={() => setEditing(true)}>{t.payments.changeAmount}</DropdownMenuItem>
              {decision.state !== "rejected" ? (
                <DropdownMenuItem onSelect={() => answer.mutate({ action: "reject" })}>{t.payments.reject}</DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

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

      {editing ? (
        <AmountForm
          initial={decision.amount}
          pending={answer.isPending}
          onCancel={() => setEditing(false)}
          onSave={(amount) => answer.mutate({ action: "edit", amount })}
        />
      ) : null}

      {isOwner && approvable && !editing ? (
        <div className="px-2 pb-2">
          <Button size="sm" disabled={answer.isPending} onClick={() => setConfirming(true)}>
            {t.payments.approve}
          </Button>
        </div>
      ) : null}

      {note ? (
        <p className="text-danger-foreground px-2 pb-2 text-xs" role="alert">
          {note}
        </p>
      ) : null}

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.payments.confirmTitle(usdc ?? "", firm)}</DialogTitle>
            <DialogDescription>{t.payments.confirmBody(bill?.number ?? t.books.noNumber)}</DialogDescription>
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
    </li>
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
