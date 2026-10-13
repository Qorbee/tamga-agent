"use client";

import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { makeAssistantToolUI } from "@assistant-ui/react";
import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { CashBar } from "@/components/cash-bar";
import { DecisionRow } from "@/components/decision-card";
import { CallCounts, fitsOf, usePaymentRun } from "@/components/payment-run";
import { between, PeriodBars, SeriesLegend, type Series } from "@/components/period-bars";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getMe,
  getParty,
  listDecisions,
  listInvoiceTotals,
  listMoneyAccounts,
  listMovements,
  listProjects,
  type Decision,
  type MovementCategory,
  type ProjectStatus,
} from "@/lib/api";
import { formatDays, formatMoney, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

/**
 * The views the chat shows under an answer (ADR-0029): the model named one from the catalog and
 * its filters through the `show` tool, and each is drawn here from the API, like any page, so
 * every figure is the server's and the view stays current. Nothing is answered in a view: a row
 * opens its record, and Open goes to the page or the canvas where the owner acts.
 */

type Shown = { view: string; title: string; filters: Record<string, unknown> };
const LISTED = 8;
// The most movements a view adds up, in pages of the API's largest: a note says when more matched.
const PAGE = 200;
const SUMMED = 2000;

/** What `show` returned: an object, or the same as JSON; nothing when the tool refused. */
function shownOf(result: unknown): Shown | null {
  let value = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object" || !("view" in value) || typeof value.view !== "string") return null;
  const filters = "filters" in value && value.filters && typeof value.filters === "object" ? value.filters : {};
  const title = "title" in value && typeof value.title === "string" ? value.title : "";
  return { view: value.view, title, filters: filters as Record<string, unknown> };
}

const text = (value: unknown) => (typeof value === "string" ? value : undefined);
const texts = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);
const count = (value: unknown, fallback: number) => (typeof value === "number" ? value : fallback);

function addDays(day: string, days: number): string {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return todayIso(new Date(year, month - 1, date + days));
}

function firstOfMonth(day: string, months: number): string {
  const [year = 1970, month = 1] = day.split("-").map(Number);
  return todayIso(new Date(year, month - 1 + months, 1));
}

/** The frame every view shares: what it shows, what was asked, and the way to where it acts. */
function Frame({
  shown,
  chips,
  open,
  children,
}: {
  shown: Shown;
  chips: string[];
  open: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <section data-slot="chat-view" className="border-border bg-card my-3 rounded-2xl border">
      <header className="flex items-start gap-3 px-4 pt-3.5 pb-2">
        <span className="min-w-0 flex-1">
          <span className="text-muted-foreground block text-xs font-medium tracking-[0.08em] uppercase">
            {shown.title || t.views.kinds[shown.view as keyof typeof t.views.kinds] || t.views.view}
          </span>
          {chips.length > 0 ? (
            <span className="mt-1.5 flex flex-wrap gap-1" aria-label={t.views.asked}>
              {chips.map((chip) => (
                <span key={chip} className="bg-secondary text-muted-foreground rounded-full px-2 py-0.5 text-[11px]">
                  {chip}
                </span>
              ))}
            </span>
          ) : null}
        </span>
        {open}
      </header>
      <div className="px-4 pb-3.5">{children}</div>
    </section>
  );
}

function OpenLink({ href }: { href: string }) {
  const { t } = useI18n();
  return (
    <Link href={href} className="text-muted-foreground hover:text-foreground inline-flex shrink-0 items-center gap-0.5 text-xs">
      {t.run.open}
      <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
    </Link>
  );
}

function Loading({ error }: { error: unknown }) {
  const { t } = useI18n();
  return error ? <p className="text-danger-foreground text-sm">{t.errors.load}</p> : <Skeleton className="h-24 rounded-xl" />;
}

function BillsView({ shown }: { shown: Shown }) {
  const { t } = useI18n();
  const states = texts(shown.filters.states) as Decision["state"][];
  const party = text(shown.filters.party_id);
  const read = useQuery({
    queryKey: ["decisions", { view: "chat", states, party }],
    queryFn: () =>
      listDecisions({ state: states.length ? states : undefined, party_id: party, expand: ["invoice"], sort: "-issued_on", limit: LISTED }),
  });
  const firm = party ? read.data?.items[0]?.invoice?.party_name : null;
  const chips = [...states.map((state) => t.views.states[state] ?? state), ...(firm ? [firm] : [])];
  return (
    <Frame shown={shown} chips={chips} open={<OpenLink href="/app/payments" />}>
      {!read.data ? (
        <Loading error={read.error} />
      ) : read.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.views.none}</p>
      ) : (
        <>
          <CallCounts decisions={read.data.items} />
          <ul className="divide-border -mx-2 mt-2 divide-y">
            {read.data.items.map((decision) => (
              <li key={decision.id}>
                <DecisionRow decision={decision} payment={undefined} actions={false} />
              </li>
            ))}
          </ul>
          {read.data.total > read.data.items.length ? (
            <p className="text-muted-foreground mt-1 text-xs">{t.views.more(read.data.total - read.data.items.length)}</p>
          ) : null}
        </>
      )}
    </Frame>
  );
}

function TotalsView({ shown }: { shown: Shown }) {
  const { t, locale } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const direction = shown.filters.direction === "payable" ? "payable" : "receivable";
  const outstanding = shown.filters.outstanding !== false;
  const party = text(shown.filters.party_id);
  const months = Math.min(12, Math.max(1, count(shown.filters.months, 3)));
  const today = me.data?.today ?? todayIso();
  // The month's first day after each month shown: the books' cutoff is strict.
  const cutoffs = [firstOfMonth(today, 0), ...Array.from({ length: months }, (_, index) => firstOfMonth(today, index + 1))];
  const totals = useQueries({
    queries: cutoffs.map((due_before) => ({
      queryKey: ["invoice-totals", { direction, outstanding, party_id: party, due_before }],
      queryFn: () => listInvoiceTotals({ direction, outstanding, party_id: party, due_before }),
    })),
  });
  // The firm by its name, so a view scoped to one never reads as the company's whole.
  const firm = useQuery({ queryKey: ["party", party], queryFn: () => getParty(party!), enabled: Boolean(party) });
  const series: Series[] = [{ label: direction === "payable" ? t.money.toPay : t.money.toCollect, tone: direction === "payable" ? "warning" : "success" }];
  const chips = [
    direction === "payable" ? t.views.payable : t.views.receivable,
    ...(outstanding ? [t.views.outstanding] : []),
    ...(firm.data ? [firm.data.name] : []),
    t.views.months(months),
  ];
  const ready = totals.every((query) => query.data);
  const cumulative = totals.map((query) => query.data ?? []);
  const periods = cutoffs.map((cutoff, index) => ({
    key: cutoff,
    label: index === 0 ? t.money.overdue : new Intl.DateTimeFormat(locale, { month: "short" }).format(new Date(`${cutoffs[index - 1]}T12:00:00`)),
    fullLabel: index === 0 ? t.money.overdue : formatDays(cutoffs[index - 1]!, null, locale),
    // Settled invoices have nothing left on them: what they came to is their total.
    amounts: between(cumulative, index, outstanding ? "remaining_amount" : "total_amount"),
  }));
  const currencies = [...new Set(periods.flatMap((period) => period.amounts.map(([currency]) => currency)))];
  return (
    <Frame shown={shown} chips={chips} open={<OpenLink href="/app/books" />}>
      {!ready ? (
        <Loading error={totals.find((query) => query.error)?.error} />
      ) : currencies.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.views.none}</p>
      ) : (
        <div className="grid gap-3">
          <SeriesLegend series={series} />
          {currencies.map((currency) => (
            <PeriodBars
              key={currency}
              currency={currency}
              series={series}
              shownCurrency={currencies.length > 1}
              periods={periods.map((period) => ({
                key: period.key,
                label: period.label,
                fullLabel: period.fullLabel,
                values: [period.amounts.find(([unit]) => unit === currency)?.[1] ?? 0],
              }))}
            />
          ))}
        </div>
      )}
    </Frame>
  );
}

function OutlookView({ shown }: { shown: Shown }) {
  const { t, locale } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const weeks = Math.min(8, Math.max(1, count(shown.filters.weeks, 4)));
  const today = me.data?.today ?? todayIso();
  const cutoffs = Array.from({ length: weeks + 1 }, (_, index) => addDays(today, index * 7));
  const sides = ["receivable", "payable"] as const;
  const totals = useQueries({
    queries: sides.flatMap((direction) =>
      cutoffs.map((due_before) => ({
        queryKey: ["invoice-totals", { direction, outstanding: true, due_before }],
        queryFn: () => listInvoiceTotals({ direction, outstanding: true, due_before }),
      })),
    ),
  });
  const series: Series[] = [
    { label: t.money.toCollect, tone: "success" },
    { label: t.money.toPay, tone: "warning" },
  ];
  const ready = totals.every((query) => query.data);
  const side = (index: number) => totals.slice(index * cutoffs.length, (index + 1) * cutoffs.length).map((query) => query.data ?? []);
  const [collect, pay] = [side(0), side(1)];
  const periods = cutoffs.map((cutoff, index) => ({
    key: cutoff,
    label: index === 0 ? t.money.overdue : formatDays(cutoffs[index - 1]!, null, locale),
    fullLabel: index === 0 ? t.money.overdue : t.money.weekOf(formatDays(cutoffs[index - 1]!, null, locale)),
    collect: between(collect, index),
    pay: between(pay, index),
  }));
  const currencies = [...new Set(periods.flatMap((period) => [...period.collect, ...period.pay].map(([currency]) => currency)))];
  return (
    <Frame shown={shown} chips={[t.views.weeks(weeks)]} open={<OpenLink href="/app/money" />}>
      {!ready ? (
        <Loading error={totals.find((query) => query.error)?.error} />
      ) : currencies.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.money.nothingDue}</p>
      ) : (
        <div className="grid gap-3">
          <SeriesLegend series={series} />
          {currencies.map((currency) => (
            <PeriodBars
              key={currency}
              currency={currency}
              series={series}
              shownCurrency={currencies.length > 1}
              periods={periods.map((period) => ({
                key: period.key,
                label: period.label,
                fullLabel: period.fullLabel,
                values: [
                  period.collect.find(([unit]) => unit === currency)?.[1] ?? 0,
                  period.pay.find(([unit]) => unit === currency)?.[1] ?? 0,
                ],
              }))}
            />
          ))}
        </div>
      )}
    </Frame>
  );
}

function MovementsView({ shown }: { shown: Shown }) {
  const { t, locale } = useI18n();
  const category = text(shown.filters.category) as MovementCategory | undefined;
  const since = text(shown.filters.since);
  const until = text(shown.filters.until);
  const account = text(shown.filters.account_id);
  const read = useQuery({
    queryKey: ["money-movements", { view: "chat", category, since, until, account }],
    // Every page up to SUMMED, so each category's sum holds all it matched, not the newest page.
    queryFn: async () => {
      const filters = { account_id: account, category: category ? [category] : undefined, since, until, sort: "-booked_on" as const };
      const first = await listMovements({ ...filters, limit: PAGE });
      const items = [...first.items];
      while (items.length < Math.min(first.total, SUMMED)) {
        const next = await listMovements({ ...filters, limit: PAGE, offset: items.length });
        if (next.items.length === 0) break;
        items.push(...next.items);
      }
      return { items, total: first.total };
    },
  });
  const accounts = useQuery({ queryKey: ["money-accounts"], queryFn: listMoneyAccounts, enabled: Boolean(account) });
  const named = accounts.data?.items.find((item) => item.id === account);
  const chips = [
    ...(named ? [`${named.name} · ${named.currency}`] : []),
    ...(category ? [t.money.categories[category]] : []),
    ...(since || until ? [`${since ? formatDays(since, null, locale) : "…"} – ${until ? formatDays(until, null, locale) : "…"}`] : []),
  ];
  // What each category came to, per currency: sums of the server's own figures.
  const sums = new Map<string, Map<string, number>>();
  for (const movement of read.data?.items ?? []) {
    const key = movement.category ?? "uncategorised";
    const byCurrency = sums.get(key) ?? new Map<string, number>();
    byCurrency.set(movement.currency, (byCurrency.get(movement.currency) ?? 0) + Number(movement.amount));
    sums.set(key, byCurrency);
  }
  return (
    <Frame shown={shown} chips={chips} open={<OpenLink href="/app/money" />}>
      {!read.data ? (
        <Loading error={read.error} />
      ) : read.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.money.noMovements}</p>
      ) : (
        <div className="grid gap-3">
          {read.data.total > read.data.items.length ? (
            <p className="text-muted-foreground text-xs">{t.views.newest(read.data.items.length, read.data.total)}</p>
          ) : null}
          <ul className="flex flex-wrap gap-1.5">
            {[...sums].map(([key, byCurrency]) => (
              <li key={key} className="bg-secondary rounded-xl px-2.5 py-1.5">
                <span className="text-muted-foreground block text-[11px]">
                  {key === "uncategorised" ? t.money.uncategorised : t.money.categories[key as MovementCategory]}
                </span>
                <span className="block text-sm font-medium tabular-nums">
                  {[...byCurrency].map(([currency, amount]) => formatMoney(amount, currency, locale)).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
          <ul className="divide-border divide-y">
            {read.data.items.slice(0, LISTED).map((movement) => (
              <li key={movement.id} className="flex items-baseline gap-3 py-1.5 text-sm">
                <span className="text-muted-foreground w-20 shrink-0 text-xs">{formatDays(movement.booked_on, null, locale)}</span>
                <span className="min-w-0 flex-1 truncate">{movement.counterparty ?? movement.description ?? "—"}</span>
                <span className="shrink-0 tabular-nums">{formatMoney(Number(movement.amount), movement.currency, locale)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Frame>
  );
}

function ProjectsView({ shown }: { shown: Shown }) {
  const { t } = useI18n();
  const statuses = texts(shown.filters.statuses) as ProjectStatus[];
  const words = text(shown.filters.words);
  const read = useQuery({
    queryKey: ["projects", { view: "chat", statuses, words }],
    queryFn: () => listProjects({ status: statuses.length ? statuses : undefined, q: words, limit: LISTED }),
  });
  const label = (status: ProjectStatus) => (t.board.columns as Record<string, string>)[status] ?? status;
  const chips = [...statuses.map(label), ...(words ? [`“${words}”`] : [])];
  return (
    <Frame shown={shown} chips={chips} open={<OpenLink href="/app/projects" />}>
      {!read.data ? (
        <Loading error={read.error} />
      ) : read.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.views.none}</p>
      ) : (
        <ul className="divide-border -mx-2 divide-y">
          {read.data.items.map((project) => (
            <li key={project.id}>
              <CanvasLink
                view={{ kind: "project", id: project.id }}
                className="hover:bg-accent/40 aria-[current=true]:bg-secondary flex items-baseline gap-3 rounded-xl px-2 py-2"
              >
                <span className="text-muted-foreground shrink-0 font-mono text-xs">{project.number}</span>
                <span className="min-w-0 flex-1 truncate text-sm">{project.name}</span>
                <span className="bg-secondary text-muted-foreground shrink-0 rounded-full px-2 py-0.5 text-[11px]">{label(project.status)}</span>
              </CanvasLink>
            </li>
          ))}
          {read.data.total > read.data.items.length ? (
            <li className="text-muted-foreground px-2 pt-1 text-xs">{t.views.more(read.data.total - read.data.items.length)}</li>
          ) : null}
        </ul>
      )}
    </Frame>
  );
}

function RunView({ shown }: { shown: Shown }) {
  const { t } = useI18n();
  const on = text(shown.filters.on);
  const run = usePaymentRun(on);
  return (
    <Frame
      shown={shown}
      chips={[]}
      open={
        run.data ? (
          <CanvasLink
            view={{ kind: "run", id: run.data.on }}
            className="text-muted-foreground hover:text-foreground inline-flex shrink-0 items-center gap-0.5 text-xs"
          >
            {t.run.open}
            <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={1.75} className="size-3.5" aria-hidden />
          </CanvasLink>
        ) : null
      }
    >
      {!run.data ? (
        <Loading error={run.error} />
      ) : (
        <div className="grid gap-3">
          <CallCounts decisions={[...run.data.items.map((item) => item.decision), ...run.data.held]} fits={fitsOf(run.data)} />
          <CashBar run={run.data} />
          <ul className="divide-border -mx-2 divide-y">
            {run.data.items.map((item) => (
              <li key={item.decision.id}>
                <DecisionRow decision={item.decision} payment={undefined} fits={fitsOf(run.data!).get(item.decision.id) ?? null} actions={false} />
              </li>
            ))}
            {run.data.held.map((decision) => (
              <li key={decision.id}>
                <DecisionRow decision={decision} payment={undefined} actions={false} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Frame>
  );
}

const KINDS: Record<string, (props: { shown: Shown }) => ReactNode> = {
  decisions: BillsView,
  invoice_totals: TotalsView,
  money_outlook: OutlookView,
  movements: MovementsView,
  projects: ProjectsView,
  payment_run: RunView,
};

/** Registers the views with the chat; until the tool has answered, nothing. */
export const ShownView = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: "show",
  // Shown on its own, under the answer, not folded into the run's closed list of steps.
  display: "standalone",
  render: ({ result }) => {
    if (result === undefined) return null;
    const shown = shownOf(result);
    const View = shown ? KINDS[shown.view] : undefined;
    return shown && View ? <View shown={shown} /> : null;
  },
});
