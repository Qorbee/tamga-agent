"use client";

import {
  Alert02Icon,
  LinkSquare02Icon,
  SecurityCheckIcon,
  TransactionHistoryIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getDecisionChain, getMe, listDecisionEvents, type DecisionEvent } from "@/lib/api";
import { formatListDate, formatMoney, formatNumber, formatUsdc } from "@/lib/format";
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { usePagedList } from "@/lib/use-paged-list";
import { cn } from "@/lib/utils";

const PAGE = 50;
// How much of a hash a row shows: enough to tell two apart, and to compare with the next row's.
const SEAL = 8;

type Tone = "done" | "stopped" | "quiet";
const DOT: Record<Tone, string> = {
  done: "bg-success",
  stopped: "bg-warning",
  quiet: "bg-muted-foreground/50",
};

/**
 * The company's decision log (docs/architecture/05): every evaluation of a bill, the owner's
 * answers and every payment, newest first, each entry sealed with the hash of the one before it.
 * Above it, whether the chain still holds; a paid entry links to its transaction on Arcscan.
 */
export function LogPage() {
  const { t } = useI18n();
  return (
    <div className="grid gap-4">
      <ChainState />
      <Entries />
      <p className="text-muted-foreground text-xs text-pretty">{t.payments.mirror}</p>
    </div>
  );
}

/** Whether every entry's hash still matches what it says and the entry before it. */
function ChainState() {
  const { t, locale } = useI18n();
  const chain = useQuery({ queryKey: ["decision-chain"], queryFn: getDecisionChain });
  // Unread, the entries below are not checked: never shown as if there were nothing to check.
  if (chain.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.log.unchecked}
      </p>
    );
  if (!chain.data?.length) return null;
  const { broken_at: broken, head, length } = chain.data;
  return (
    <div
      role={broken ? "alert" : undefined}
      className={cn(
        "flex items-start gap-3 rounded-2xl border px-4 py-3",
        broken ? "border-danger/40 bg-danger-soft" : "border-border bg-card",
      )}
    >
      <HugeiconsIcon
        icon={broken ? Alert02Icon : SecurityCheckIcon}
        strokeWidth={1.75}
        className={cn("mt-0.5 size-5 shrink-0", broken ? "text-danger-foreground" : "text-success-foreground")}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{broken ? t.log.broken(formatNumber(broken, locale)) : t.log.holds}</p>
        <p className="text-muted-foreground text-xs text-pretty">
          {broken ? t.log.brokenBody : t.log.holdsBody(formatNumber(length, locale))}
        </p>
        {head && !broken ? (
          <p className="text-muted-foreground mt-1 text-xs">
            {t.log.head} <span className="font-mono break-all">{head}</span>
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Entries() {
  const { t } = useI18n();
  const events = usePagedList(
    ["decision-events", { sort: "-seq" }],
    (page) => listDecisionEvents({ sort: "-seq", expand: ["invoice"], ...page }),
    PAGE,
    (event) => event.id,
  );

  if (events.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!events.items) return <Skeleton className="h-40 rounded-2xl" />;
  if (events.items.length === 0)
    return <EmptyState icon={TransactionHistoryIcon} title={t.empty.log.title} body={t.empty.log.body} />;
  return (
    <div className="grid gap-3">
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {events.items.map((event) => (
          <li key={event.id} className="py-1 ps-2 pe-3">
            <Entry event={event} />
          </li>
        ))}
      </ul>
      {events.more ? (
        <Button variant="ghost" className="justify-self-center" disabled={events.loadingMore} onClick={events.more}>
          {t.inbox.showMore}
        </Button>
      ) : null}
    </div>
  );
}

/** One entry: what happened to which bill, for how much test USDC, when, and its seal. */
function Entry({ event }: { event: DecisionEvent }) {
  const { t, locale } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const bill = event.invoice ?? null;
  // The firm as it was when this evaluation was made; the bill's own is today's.
  const firm = event.party_name ?? bill?.party_name ?? null;
  const yours = event.user_id !== null && event.user_id === me.data?.user.id;
  const reason = event.event === "failed" && typeof event.body.reason === "string" ? event.body.reason : null;
  const units = typeof event.body.usdc_units === "number" ? event.body.usdc_units : null;
  const money = (amount: unknown) =>
    typeof amount === "string" && bill?.currency ? formatMoney(Number(amount), bill.currency, locale) : String(amount);
  const [label, tone] = described(event, t.log.events, money, yours);
  const content: ReactNode = (
    <>
      <span className={cn("mt-2 size-2 shrink-0 rounded-full", DOT[tone])} aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="text-muted-foreground flex flex-wrap items-baseline gap-x-2 text-xs">
          <span className="font-mono whitespace-nowrap">{bill?.number ?? t.books.noNumber}</span>
          <span className="whitespace-nowrap">{formatListDate(new Date(event.at), locale)}</span>
        </span>
        <span className="block text-sm font-medium text-pretty">{label}</span>
        {firm ? <span className="text-muted-foreground block truncate text-xs">{firm}</span> : null}
        {/* The vault's own words, for whoever looks into it. */}
        {reason ? (
          <span className="text-muted-foreground mt-0.5 block font-mono text-xs break-words">{reason}</span>
        ) : null}
        {/* The entry's place in the chain and the start of its hash: the next entry is sealed with it. */}
        <span className="text-muted-foreground/80 mt-0.5 block font-mono text-[11px]">
          #{event.seq} · {event.hash.slice(0, SEAL)}
        </span>
      </span>
      {units !== null ? (
        <span className="shrink-0 text-sm font-medium tabular-nums">{formatUsdc(units, locale)}</span>
      ) : null}
    </>
  );
  const row = "flex min-w-0 items-start gap-3 rounded-xl px-2 py-2.5";
  return (
    <>
      {bill ? (
        <CanvasLink
          view={{ kind: "invoice", id: bill.id }}
          className={cn(row, "hover:bg-accent/40 aria-[current=true]:bg-secondary transition-colors")}
        >
          {content}
        </CanvasLink>
      ) : (
        <div className={row}>{content}</div>
      )}
      {event.explorer_url ? (
        <p className="px-2 ps-7 pb-2 text-xs">
          <a
            href={event.explorer_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 underline-offset-4 hover:underline"
          >
            {t.payments.viewTx}
            <HugeiconsIcon icon={LinkSquare02Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
          </a>
        </p>
      ) : null}
    </>
  );
}

function described(
  event: DecisionEvent,
  words: Dictionary["log"]["events"],
  money: (amount: unknown) => string,
  yours: boolean,
): [string, Tone] {
  const { body } = event;
  switch (event.event) {
    case "proposed":
      return body.state === "awaiting_owner"
        ? [words.waits, "quiet"]
        : body.state === "approved"
          ? [words.ruleApproved, "done"]
          : body.state === "rejected"
            ? [words.ruleRejected, "stopped"]
            : [words.ruleHeld, "stopped"];
    case "edited":
      return [words.edited(money(body.from), money(body.to)), "quiet"];
    // Only an owner answers; the viewer may be another member, or another owner.
    case "approved":
      return [yours ? words.approved : words.approvedByOwner, "done"];
    case "held":
      return [yours ? words.held : words.heldByOwner, "stopped"];
    case "rejected":
      return [yours ? words.rejected : words.rejectedByOwner, "stopped"];
    case "settled":
      return [words.settled, "done"];
    case "failed":
      return [body.evaluated_again ? words.sentBack : words.failed, "stopped"];
    case "would_release":
      return [words.wouldRelease, "quiet"];
  }
}
