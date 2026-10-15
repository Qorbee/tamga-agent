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
import { CallCounts, fitsOf, toApprove, usePaymentRun } from "@/components/payment-run";
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
import { formatDayShort, formatDays, formatMoney, formatMoneyShort, todayIso } from "@/lib/format";
import type { Locale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

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

type Stat = { label: string; value: string; tone?: "danger" };

/** The answer's figures first, each the code's sum of what the view draws below. */
function Headline({ stats }: { stats: Stat[] }) {
  if (stats.length === 0) return null;
  return (
    <dl className="mb-3 flex flex-wrap gap-x-6 gap-y-2">
      {stats.map((stat) => (
        <div key={stat.label} className="min-w-0">
          <dt className="text-muted-foreground text-xs">{stat.label}</dt>
          <dd className={cn("text-xl font-medium tabular-nums", stat.tone === "danger" && "text-danger-foreground")}>{stat.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Each currency's sum over the periods a view draws, never across currencies. */
function sumOf(periods: [string, number][][]): Map<string, number> {
  const out = new Map<string, number>();
  for (const amounts of periods) for (const [currency, amount] of amounts) out.set(currency, (out.get(currency) ?? 0) + amount);
  return out;
}

/** In, out and what they leave: one line the eye reads first. None when the view holds more than
 * one currency, since one line could not carry them all and the chart below draws each. */
function flowStats(collect: Map<string, number>, pay: Map<string, number>, words: { in: string; out: string; net: string }, locale: Locale): Stat[] {
  const currencies = [...new Set([...collect.keys(), ...pay.keys()])];
  if (currencies.length !== 1) return [];
  const currency = currencies[0]!;
  const into = collect.get(currency) ?? 0;
  const out = pay.get(currency) ?? 0;
  const net = into - out;
  return [
    { label: words.in, value: formatMoneyShort(into, currency, locale) },
    { label: words.out, value: formatMoneyShort(out, currency, locale) },
    { label: words.net, value: `${net < 0 ? "−" : net > 0 ? "+" : ""}${formatMoneyShort(Math.abs(net), currency, locale)}`, tone: net < 0 ? "danger" : undefined },
  ];
}

/** How many bills, and what they come to while every one is listed, each with its amount in one
 * currency: a sum of a page would read as the whole, and an amount of no known currency as one. */
function billStats(bills: Decision[], total: number, words: { count: string; sum: string }, locale: Locale): Stat[] {
  const stats: Stat[] = [{ label: words.count, value: String(total) }];
  const currency = bills[0]?.currency;
  if (total === bills.length && currency && bills.every((bill) => bill.currency === currency && bill.amount != null)) {
    const sum = bills.reduce((all, bill) => all + Number(bill.amount), 0);
    stats.push({ label: words.sum, value: formatMoneyShort(sum, currency, locale) });
  }
  return stats;
}

function BillsView({ shown }: { shown: Shown }) {
  const { t, locale } = useI18n();
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
          <Headline stats={billStats(read.data.items, read.data.total, t.views.billStats, locale)} />
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
  const direction = shown.filters.direction === "payable" || shown.filters.direction === "receivable" ? shown.filters.direction : "both";
  const sides = direction === "both" ? (["receivable", "payable"] as const) : ([direction] as const);
  const outstanding = shown.filters.outstanding !== false;
  const party = text(shown.filters.party_id);
  const months = Math.min(12, Math.max(1, count(shown.filters.months, 3)));
  const today = me.data?.today ?? todayIso();
  // The month's first day after each month shown: the books' cutoff is strict.
  const cutoffs = [firstOfMonth(today, 0), ...Array.from({ length: months }, (_, index) => firstOfMonth(today, index + 1))];
  const totals = useQueries({
    queries: sides.flatMap((side) =>
      cutoffs.map((due_before) => ({
        queryKey: ["invoice-totals", { direction: side, outstanding, party_id: party, due_before }],
        queryFn: () => listInvoiceTotals({ direction: side, outstanding, party_id: party, due_before }),
      })),
    ),
  });
  // The firm by its name, so a view scoped to one never reads as the company's whole.
  const firm = useQuery({ queryKey: ["party", party], queryFn: () => getParty(party!), enabled: Boolean(party) });
  const label = { receivable: t.money.toCollect, payable: t.money.toPay } as const;
  const tone = { receivable: "success", payable: "warning" } as const;
  const series: Series[] = sides.map((side) => ({ label: label[side], tone: tone[side] }));
  const chips = [
    direction === "both" ? t.views.bothSides : direction === "payable" ? t.views.payable : t.views.receivable,
    ...(outstanding ? [t.views.outstanding] : []),
    ...(firm.data ? [firm.data.name] : []),
    t.views.months(months),
  ];
  const ready = totals.every((query) => query.data);
  const side = (index: number) => totals.slice(index * cutoffs.length, (index + 1) * cutoffs.length).map((query) => query.data ?? []);
  // Settled invoices have nothing left on them: what they came to is their total.
  const field = outstanding ? "owed_amount" : "total_amount";
  const periods = cutoffs.map((cutoff, index) => ({
    key: cutoff,
    label: index === 0 ? t.money.overdue : new Intl.DateTimeFormat(locale, { month: "short" }).format(new Date(`${cutoffs[index - 1]}T12:00:00`)),
    fullLabel: index === 0 ? t.money.overdue : formatDays(cutoffs[index - 1]!, null, locale),
    amounts: sides.map((_, which) => between(side(which), index, field)),
  }));
  const currencies = [...new Set(periods.flatMap((period) => period.amounts.flat().map(([currency]) => currency)))];
  const each = sides.map((_, which) => sumOf(periods.map((period) => period.amounts[which] ?? [])));
  const first = currencies[0];
  const stats: Stat[] =
    sides.length === 2
      ? flowStats(each[0]!, each[1]!, { in: t.money.toCollect, out: t.money.toPay, net: t.money.net }, locale)
      : first
        ? [{ label: label[sides[0]], value: formatMoneyShort(each[0]!.get(first) ?? 0, first, locale) }]
        : [];
  return (
    <Frame shown={shown} chips={chips} open={<OpenLink href="/app/books" />}>
      {!ready ? (
        <Loading error={totals.find((query) => query.error)?.error} />
      ) : currencies.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.views.none}</p>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          <Headline stats={stats} />
          <SeriesLegend series={series} />
          {currencies.map((currency) => (
            <PeriodBars
              key={currency}
              currency={currency}
              series={series}
              shownCurrency={currencies.length > 1}
              net={sides.length === 2}
              periods={periods.map((period) => ({
                key: period.key,
                label: period.label,
                fullLabel: period.fullLabel,
                values: period.amounts.map((amounts) => amounts.find(([unit]) => unit === currency)?.[1] ?? 0),
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
    label: index === 0 ? t.money.overdue : formatDayShort(cutoffs[index - 1]!, locale),
    fullLabel: index === 0 ? t.money.overdue : t.money.weekOf(formatDays(cutoffs[index - 1]!, null, locale)),
    collect: between(collect, index),
    pay: between(pay, index),
  }));
  const currencies = [...new Set(periods.flatMap((period) => [...period.collect, ...period.pay].map(([currency]) => currency)))];
  const stats = flowStats(
    sumOf(periods.map((period) => period.collect)),
    sumOf(periods.map((period) => period.pay)),
    { in: t.money.toCollect, out: t.money.toPay, net: t.money.net },
    locale,
  );
  return (
    <Frame shown={shown} chips={[t.views.weeks(weeks)]} open={<OpenLink href="/app/money" />}>
      {!ready ? (
        <Loading error={totals.find((query) => query.error)?.error} />
      ) : currencies.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.money.nothingDue}</p>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          <Headline stats={stats} />
          <SeriesLegend series={series} />
          {currencies.map((currency) => (
            <PeriodBars
              key={currency}
              currency={currency}
              series={series}
              shownCurrency={currencies.length > 1}
              net
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
  // In, out and what they leave, from every movement read.
  const flows: [string, number][][] = (read.data?.items ?? []).map((movement) => [[movement.currency, Number(movement.amount)]]);
  const movementStats = flowStats(
    sumOf(flows.map((amounts) => amounts.filter(([, amount]) => amount > 0))),
    sumOf(flows.map((amounts) => amounts.filter(([, amount]) => amount < 0).map(([currency, amount]): [string, number] => [currency, -amount]))),
    { in: t.views.cameIn, out: t.views.wentOut, net: t.money.net },
    locale,
  );
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
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          <Headline stats={movementStats} />
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
        <>
          <Headline stats={[{ label: t.views.projectCount, value: String(read.data.total) }]} />
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
        </>
      )}
    </Frame>
  );
}

function RunView({ shown }: { shown: Shown }) {
  const { t, locale } = useI18n();
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
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          <Headline
            stats={[
              {
                label: t.run.toPayNow,
                value: formatMoneyShort(
                  toApprove(run.data).reduce((sum, item) => sum + Number(item.in_policy_currency ?? 0), 0),
                  run.data.currency,
                  locale,
                ),
              },
            ]}
          />
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
