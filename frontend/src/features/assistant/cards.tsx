"use client";

import { makeAssistantDataUI, makeAssistantToolUI, type AssistantDataUI, type DataMessagePartProps } from "@assistant-ui/react";
import { ArrowUpRight01Icon, File01Icon, Invoice01Icon, Mail01Icon, TruckIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { callOf, waits } from "@/components/bill-call";
import { Seal } from "@/components/brand/logo";
import { CanvasLink } from "@/components/canvas-link";
import { DecisionRow } from "@/components/decision-card";
import { DraftedEntry, Lessons } from "@/components/memory";
import { MoneyGlance } from "@/components/money-glance";
import { PolicyDraftCard } from "@/components/policy-draft";
import { T } from "@/components/translated";

import { ShownView } from "./views";
import { Skeleton } from "@/components/ui/skeleton";
import { ApproveMany, fitsOf, RunGroups, usePaymentRun } from "@/components/payment-run";
import { ProjectCard, Questions } from "@/components/waiting-cards";
import {
  ApiError,
  getDecision,
  getPlacement,
  getProject,
  getViewSnapshot,
  listAllLinks,
  listAllPlacements,
  listDecisions,
  listPayments,
  listShipments,
  type CardContent,
  type CardKind,
  type NewsItem,
  type RunItem,
  type TurnContent,
} from "@/lib/api";
import type { CanvasView } from "@/lib/canvas-view";
import { formatDate, formatMoney, formatMoneyShort } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

/**
 * The cards under what Tamga writes on its own (ADR-0018). The server says only which record a
 * card shows; the card reads that record like any page does, so it is the Inbox's own card, it
 * answers through the same requests, and it shows the record as it is now, not as it was when
 * Tamga wrote.
 */
function ProposalCard({ data }: DataMessagePartProps<CardContent>) {
  const project = useQuery({
    queryKey: ["project", data.id],
    queryFn: () => getProject(data.id),
    retry: false,
  });
  const placements = useQuery({
    queryKey: ["placements", { projects: [data.id] }],
    queryFn: () => listAllPlacements({ project_id: [data.id] }),
  });
  return (
    <Card pending={project.isPending || placements.isPending} error={project.error ?? placements.error}>
      {project.data ? (
        <ProjectCard
          project={project.data}
          waiting={(placements.data ?? []).filter((placement) => placement.conversation)}
        />
      ) : null}
    </Card>
  );
}

function QuestionCard({ data }: DataMessagePartProps<CardContent>) {
  const placement = useQuery({
    queryKey: ["placements", data.id],
    queryFn: () => getPlacement(data.id),
    retry: false,
  });
  return (
    <Card pending={placement.isPending} error={placement.error}>
      {placement.data?.conversation ? <Questions placements={[placement.data]} /> : null}
    </Card>
  );
}

/** A bill that waits for the owner's approval, as its row on the Payments page shows it now. */
function ApprovalCard({ data }: DataMessagePartProps<CardContent>) {
  const decision = useQuery({
    queryKey: ["decision", data.id],
    queryFn: () => getDecision(data.id),
    retry: false,
  });
  const settled = decision.data?.state === "settled";
  const payments = useQuery({
    queryKey: ["payments", { decisions: [data.id] }],
    queryFn: () => listPayments({ decision_id: [data.id] }),
    enabled: settled,
  });
  return (
    <Card pending={decision.isPending} error={decision.error}>
      {decision.data ? (
        <div className="border-border bg-card rounded-2xl border py-1 ps-2 pe-3">
          <DecisionRow
            decision={decision.data}
            payment={payments.data?.items.find(
              (payment) => payment.kind === "payment" && payment.state === "settled" && !payment.voided_at,
            )}
            mirror={payments.data?.items.find((payment) => payment.kind === "mirror" && payment.state === "settled")}
          />
        </div>
      ) : null}
    </Card>
  );
}

/**
 * The bills that waited for the owner when Tamga wrote, together (ADR-0018): what Tamga would pay
 * now, its calls counted, each bill as the run shows it, and one "Approve N" for those Tamga would
 * pay that fit. Each bill is read as it is now: one evaluated again since Tamga wrote says so, and
 * those already answered fold into a line.
 */
function BatchCard({ data }: DataMessagePartProps<CardContent>) {
  const { t, locale } = useI18n();
  const ids: string[] = data.ids && data.ids.length > 0 ? data.ids : [data.id];
  const evaluations: number[] = data.evaluations ?? [];
  const decisions = useQuery({
    queryKey: ["decisions", { ids }],
    queryFn: () => listDecisions({ id: ids, expand: ["invoice"], limit: 200 }),
  });
  const run = usePaymentRun();
  const [showDone, setShowDone] = useState(false);
  const written = new Map(ids.map((id, index) => [id, evaluations[index]]));
  const inRun = new Map((run.data?.items ?? []).map((item) => [item.decision.id, item]));
  const weighed = run.data ? fitsOf(run.data) : new Map<string, boolean | null>();
  // In the card's own order, the order Tamga wrote about them.
  const order = new Map(ids.map((id, index) => [id, index]));
  const items: RunItem[] = [...(decisions.data?.items ?? [])]
    .sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0))
    .map((decision) => {
      const inTheRun = inRun.get(decision.id);
      return {
        decision,
        fits: weighed.get(decision.id) ?? null,
        in_policy_currency: inTheRun?.in_policy_currency ?? null,
      };
    });
  const open = items.filter((item) => waits(callOf(item.decision, item.fits).call));
  const done = items.filter((item) => !open.includes(item));
  // Only once the run has weighed them against the reserve; with no run to weigh by, none.
  const chosen = run.data
    ? open.filter(
        (item) =>
          callOf(item.decision, item.fits).call === "pay" && item.fits !== false && item.in_policy_currency !== null,
      )
    : [];
  // What Tamga would pay now, in the policy's currency where the run weighed it, else in the
  // bills' own currency when they share one.
  const currency = run.data?.currency ?? null;
  const weighedAll = chosen.every((item) => item.in_policy_currency !== null);
  const sameCurrency = new Set(chosen.map((item) => item.decision.currency)).size === 1 ? chosen[0]?.decision.currency : null;
  const total =
    chosen.length === 0
      ? null
      : weighedAll && currency
        ? formatMoneyShort(chosen.reduce((sum, item) => sum + Number(item.in_policy_currency), 0), currency, locale)
        : sameCurrency
          ? formatMoneyShort(chosen.reduce((sum, item) => sum + Number(item.decision.amount), 0), sameCurrency, locale)
          : null;
  const fits = new Map(items.map((item) => [item.decision.id, item.fits]));
  return (
    <Card pending={decisions.isPending || run.isPending} error={decisions.error}>
      <section className="border-border bg-card rounded-2xl border">
        <header className="grid gap-1.5 px-4 pt-3.5 pb-3">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-muted-foreground text-xs font-medium tracking-[0.08em] uppercase">
              {t.assistant.cards.bills(items.length)}
            </h3>
            {run.data && items.some((item) => inRun.has(item.decision.id)) ? (
              <CanvasLink
                view={{ kind: "run", id: run.data.on }}
                className="text-muted-foreground hover:text-foreground inline-flex items-center gap-0.5 text-xs"
              >
                {t.run.open}
                <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
              </CanvasLink>
            ) : null}
          </div>
          {total ? (
            <p className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-2xl font-medium tabular-nums">{total}</span>
              <span className="text-muted-foreground text-sm">{t.run.toPayNow}</span>
            </p>
          ) : null}
        </header>
        <RunGroups
          decisions={open.map((item) => item.decision)}
          fits={fits}
          row={(decision) => (
            <>
              {written.get(decision.id) !== undefined && written.get(decision.id) !== decision.evaluation ? (
                <p className="text-muted-foreground px-2 pt-1.5 text-[11px] font-medium tracking-[0.06em] uppercase">
                  {t.assistant.cards.updated}
                </p>
              ) : null}
              <DecisionRow decision={decision} payment={undefined} fits={fits.get(decision.id) ?? null} inRun />
            </>
          )}
        />
        {done.length > 0 ? (
          <div className="border-border border-t">
            <button
              type="button"
              aria-expanded={showDone}
              onClick={() => setShowDone(!showDone)}
              className="text-muted-foreground hover:text-foreground w-full px-4 py-2.5 text-start text-xs"
            >
              {t.assistant.cards.done(done.length)}
            </button>
            {showDone ? (
              <ul className="divide-border divide-y">
                {done.map((item) => (
                  <li key={item.decision.id} className="py-1 ps-2 pe-3">
                    <DecisionRow decision={item.decision} payment={undefined} actions={false} />
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        {chosen.length > 0 ? <ApproveMany items={chosen} total={total} className="border-border border-t px-4 py-3" /> : null}
      </section>
    </Card>
  );
}

/** A card while its record loads, and what is said when the record cannot be shown. */
function Card({ pending, error, children }: { pending: boolean; error: unknown; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div data-slot="chat-card" className="my-3">
      {error ? (
        <p className="text-muted-foreground border-border rounded-2xl border px-4 py-3 text-sm" role="status">
          {error instanceof ApiError && error.status === 404 ? t.assistant.cards.gone : t.errors.load}
        </p>
      ) : pending ? (
        <Skeleton className="h-32 rounded-2xl" />
      ) : (
        children
      )}
    </div>
  );
}

/** The morning brief's card: where the money stands as it is read, as Money shows it. */
/** The money a brief was written over, as it stood then (ADR-0035); a brief written before the
 * figures were kept shows where the money stands now, and says so. */
function BriefCard({ data }: DataMessagePartProps<CardContent>) {
  const { t, locale } = useI18n();
  const kept = useQuery({
    queryKey: ["view-snapshot", data.snapshot],
    queryFn: () => getViewSnapshot(data.snapshot!),
    enabled: Boolean(data.snapshot),
    staleTime: Infinity,
    retry: false,
  });
  if (data.snapshot && kept.isPending) return <Skeleton className="my-3 h-24 w-64 rounded-2xl" />;
  return (
    <div data-slot="chat-card" className="my-3 flex flex-col gap-2">
      <p className="text-muted-foreground text-xs">
        {kept.data ? t.assistant.cards.briefThen(formatDate(new Date(kept.data.taken_at), locale, "dateTime")) : t.assistant.cards.briefNow}
      </p>
      <MoneyGlance kept={kept.data?.data} className="border-border bg-card rounded-2xl border px-4 py-3" />
    </div>
  );
}

type NewsKind = NewsItem["kind"];
const NEWS_ICONS = { message: Mail01Icon, document: File01Icon, invoice: Invoice01Icon, truck: TruckIcon } as const;

/**
 * What is new on a project: the project, then one row for each item Tamga told, which opens it
 * beside the chat. A truck's row names the truck's plates and opens the project, where its
 * position is.
 */
function NewsCard({ data }: DataMessagePartProps<CardContent>) {
  const { t, locale } = useI18n();
  const items: NewsItem[] = data.items ?? [];
  const project = useQuery({ queryKey: ["project", data.id], queryFn: () => getProject(data.id), retry: false });
  // The project page's own query (`linksKey`), so an answer given on a link refreshes this card.
  const links = useQuery({
    queryKey: ["project-links", { project_id: data.id, expand: "item" }],
    queryFn: () => listAllLinks({ project_id: data.id, status: ["suggested", "confirmed"], expand: ["item"] }),
  });
  const trucks = useQuery({
    queryKey: ["shipments", { projects: [data.id] }],
    queryFn: () => listShipments([data.id]),
    enabled: items.some((item) => item.kind === "truck"),
  });
  const byItem = new Map((links.data ?? []).map((link) => [link.item_id, link.item]));

  const row = (kind: NewsKind, id: string): { label: string; detail?: string; view: CanvasView } | null => {
    if (kind === "truck") {
      const truck = (trucks.data ?? []).find((each) => each.id === id);
      const plates = truck ? [truck.truck_plate, truck.trailer_plate].filter(Boolean).join(" + ") : "";
      return { label: plates || t.assistant.cards.truck, view: { kind: "project", id: data.id } };
    }
    const item = byItem.get(id);
    if (!item) return null;
    if (item.item_kind === "message")
      return {
        label: item.message.subject || t.inbox.noSubject,
        detail: item.message.sender_name || item.message.sender || undefined,
        view: { kind: "mail", id },
      };
    if (item.item_kind === "document") return { label: item.document.filename, view: { kind: "document", id } };
    const bill = item.invoice;
    return {
      label: [bill.party_name, bill.number].filter(Boolean).join(" · ") || t.assistant.cards.bill,
      detail:
        bill.total_amount !== null && bill.currency
          ? formatMoney(Number(bill.total_amount), bill.currency, locale)
          : undefined,
      view: { kind: "invoice", id },
    };
  };

  return (
    <Card pending={project.isPending || links.isPending} error={project.error ?? links.error}>
      {project.data ? (
        <article className="border-border bg-card overflow-hidden rounded-2xl border">
          <header className="flex min-w-0 items-baseline gap-2 px-4 pt-3 pb-2">
            <span className="text-muted-foreground shrink-0 font-mono text-xs">{project.data.number}</span>
            <CanvasLink
              view={{ kind: "project", id: project.data.id }}
              className="decoration-muted-foreground/60 truncate text-sm font-medium underline-offset-4 hover:underline"
            >
              <T>{project.data.name}</T>
            </CanvasLink>
          </header>
          <ul className="divide-border border-border divide-y border-t">
            {items.map((item) => {
              const shown = row(item.kind, item.id);
              if (!shown) return null;
              return (
                <li key={`${item.kind}:${item.id}`}>
                  <CanvasLink
                    view={shown.view}
                    className="hover:bg-accent/60 flex min-w-0 items-center gap-3 px-4 py-2.5 text-sm"
                  >
                    <HugeiconsIcon
                      icon={NEWS_ICONS[item.kind]}
                      strokeWidth={1.75}
                      className="text-muted-foreground size-4 shrink-0"
                      aria-label={t.assistant.cards.newsKinds[item.kind]}
                    />
                    <span className="min-w-0 flex-1 truncate">
                      <T>{shown.label}</T>
                    </span>
                    {shown.detail ? (
                      <span className="text-muted-foreground shrink-0 truncate text-xs tabular-nums">
                        <T>{shown.detail}</T>
                      </span>
                    ) : null}
                  </CanvasLink>
                </li>
              );
            })}
          </ul>
        </article>
      ) : null}
    </Card>
  );
}

/** The chat protocol's activity messages reach the thread as data parts under this prefix. */
const card = (kind: CardKind, render: (props: DataMessagePartProps<CardContent>) => ReactNode): AssistantDataUI =>
  makeAssistantDataUI<CardContent>({ name: `agui-activity/${kind}`, render });

/** A renderer for every kind of card the API names: a new kind fails the build until it has one. */
const CARDS: Record<CardKind, AssistantDataUI> = {
  project_proposal: card("project_proposal", ProposalCard),
  conversation_question: card("conversation_question", QuestionCard),
  decision_approval: card("decision_approval", ApprovalCard),
  decision_batch: card("decision_batch", BatchCard),
  morning_brief: card("morning_brief", BriefCard),
  learned: card("learned", ({ data }) => <Lessons ids={data.ids ?? []} />),
  project_news: card("project_news", NewsCard),
};

/** Where the chat was compacted: above it Tamga works from notes, and every message is still here. */
function CompactedHere() {
  const { t } = useI18n();
  return (
    <div data-slot="chat-compacted" role="separator" className="text-muted-foreground my-4 flex items-center gap-3 text-xs">
      <span className="bg-border h-px flex-1" />
      <span className="max-w-[75%] text-center text-pretty">{t.assistant.context.compacted}</span>
      <span className="bg-border h-px flex-1" />
    </div>
  );
}

const Compacted = makeAssistantDataUI({ name: "agui-activity/compaction", render: CompactedHere });

/** Where Tamga began a turn on its own, and when, so what follows reads as Tamga's own. Why it
 * began stays in the turn's record (`chat_runs.reason`): the message under it says that. */
function TurnBegan({ data }: DataMessagePartProps<TurnContent>) {
  const { t, locale } = useI18n();
  return (
    <div data-slot="chat-turn-began" role="separator" className="text-muted-foreground my-3 flex items-center gap-3 text-xs">
      <span className="bg-border h-px flex-1" />
      <span className="flex max-w-[80%] items-center gap-1.5 text-center text-pretty">
        <Seal small className="size-3.5 shrink-0" />
        <span>
          {t.assistant.turnBegan}
          {" · "}
          {formatDate(new Date(data.at), locale, "dateTime")}
        </span>
      </span>
      <span className="bg-border h-px flex-1" />
    </div>
  );
}

const Began = makeAssistantDataUI<TurnContent>({ name: "agui-activity/tamga_turn", render: TurnBegan });

/** What the chat's `remember` drafted, as a card the owner keeps with Tamga's answer
 * (ADR-0024); until the tool has answered, nothing. */
const Remembered = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: "remember",
  // Shown on its own, not folded into the run's closed list of steps: the owner confirms here.
  display: "standalone",
  render: ({ result }) => (result === undefined ? null : <DraftedEntry result={result} />),
});

/** An entry the chat's `forget` asks the owner to retire: the same card, with Forget. */
const Forgot = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: "forget",
  display: "standalone",
  render: ({ result }) => (result === undefined ? null : <DraftedEntry result={result} />),
});

/** What the chat's `draft_policy` drafted, as a card the owner confirms under Tamga's answer. */
const PolicyDrafted = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: "draft_policy",
  display: "standalone",
  render: ({ result }) => (result === undefined ? null : <PolicyDraftCard result={result} />),
});

/** A payment rule of the owner's own the chat's `draft_rule` drafted: the same card, which shows
 * the rule in words and the bills it applies to now (ADR-0033). */
const RuleDrafted = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: "draft_rule",
  display: "standalone",
  render: ({ result }) => (result === undefined ? null : <PolicyDraftCard result={result} />),
});

/** Registers the cards and the mark with the chat; rendered once, inside the chat's runtime. */
export function ChatCards() {
  return (
    <>
      {Object.entries(CARDS).map(([kind, Registered]) => (
        <Registered key={kind} />
      ))}
      <Compacted />
      <Began />
      <Remembered />
      <Forgot />
      <PolicyDrafted />
      <RuleDrafted />
      <ShownView />
    </>
  );
}
