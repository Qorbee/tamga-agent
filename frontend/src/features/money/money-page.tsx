"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { useRuleWords } from "@/components/policy-settings";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  changeMovement,
  getHoldings,
  getMe,
  getPolicy,
  listInvoiceTotals,
  listMoneyAccounts,
  listMovements,
  type InvoiceTotal,
  type MoneyAccount,
  type Movement,
  type MovementCategory,
} from "@/lib/api";
import { formatDays, formatMoney, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";
import { usePagedList } from "@/lib/use-paged-list";

const PAGE = 50;
const CATEGORIES: readonly MovementCategory[] = [
  "client_receipt",
  "subcontractor_payment",
  "salary",
  "tax",
  "bank_fee",
  "loan",
  "own_transfer",
  "partner_withdrawal",
  "other",
];

/**
 * Where the company's money is (ADR-0026): each account in its own currency, one total at the
 * official rate beside them, the reserve in force, what is due in and out over the next four
 * weeks, and what moved, with the owner's categories. Every currency stays apart; the total is the
 * only figure converted, and it says so.
 */
export function MoneyPage() {
  return (
    <div className="grid gap-6">
      <Holdings />
      <Accounts />
      <Outlook />
      <Movements />
    </div>
  );
}

function Holdings() {
  const { t, locale } = useI18n();
  const read = useRuleWords();
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const currency = policy.data?.currency;
  const holdings = useQuery({
    queryKey: ["money-holdings", currency],
    queryFn: () => getHoldings(currency ?? ""),
    enabled: currency !== undefined,
  });
  if (policy.isError || holdings.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!policy.data || !holdings.data) return <Skeleton className="h-20 rounded-2xl" />;
  const reserve = read(policy.data.reserve, policy.data.currency);
  return (
    <section className="border-border bg-card grid gap-1 rounded-2xl border px-4 py-3.5">
      {holdings.data.items.length > 0 ? (
        <>
          <p className="text-lg font-medium tabular-nums">
            {t.money.about(formatMoney(Number(holdings.data.total), holdings.data.currency, locale))}
          </p>
          <p className="text-muted-foreground text-xs text-pretty">
            {t.money.aboutHint}
            {holdings.data.complete ? null : ` ${t.money.incomplete}`}
          </p>
        </>
      ) : (
        <p className="text-muted-foreground text-sm text-pretty">{t.money.noAccounts}</p>
      )}
      <p className="text-muted-foreground border-border mt-2 border-t pt-2 text-xs text-pretty">
        <span className="text-foreground font-medium">{t.money.reserve(reserve.name)}</span>
        {" · "}
        {reserve.body}
      </p>
    </section>
  );
}

function Accounts() {
  const { t, locale } = useI18n();
  const accounts = useQuery({ queryKey: ["money-accounts"], queryFn: listMoneyAccounts });
  if (accounts.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!accounts.data) return <Skeleton className="h-32 rounded-2xl" />;
  if (accounts.data.items.length === 0) return null;
  // The banks and cash first; then what the books estimate, and the vault's test USDC, never
  // counted as money held.
  const order = { bank: 0, cash: 1, books: 2, vault: 3 } as const;
  const sorted = [...accounts.data.items].sort(
    (a, b) => order[a.kind] - order[b.kind] || (a.bank ?? "").localeCompare(b.bank ?? "") || a.name.localeCompare(b.name),
  );
  return (
    <section className="grid gap-2">
      <h2 className="text-muted-foreground text-xs font-medium">{t.money.accounts}</h2>
      <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
        {sorted.map((account) => (
          <li key={account.id} className="flex items-start gap-3 px-4 py-2.5">
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium break-words">{account.name}</span>
              <span className="text-muted-foreground block text-xs">{detail(account, t.money)}</span>
            </span>
            <span className="shrink-0 text-end text-sm font-medium tabular-nums">
              {account.balance !== null ? (
                formatMoney(Number(account.balance), account.currency, locale)
              ) : (
                <span className="text-muted-foreground text-xs font-normal">{t.money.noBalance}</span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function detail(account: MoneyAccount, words: { estimated: string; stale: string; vault: string }): string {
  const parts = [
    account.bank,
    account.iban ? `•••• ${account.iban.slice(-4)}` : null,
    account.kind === "books" ? words.estimated : null,
    account.kind === "vault" ? words.vault : null,
    account.link_error ? words.stale : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

/** The day `days` after `today` (the company's day, which a sandbox's skips move), as the API
 * writes one. */
function dayAfter(today: string, days: number): string {
  const [year = 1970, month = 1, date = 1] = today.split("-").map(Number);
  return todayIso(new Date(year, month - 1, date + days));
}

/** What is due in and out: overdue, then each of the next four weeks, by currency. The books'
 * totals are read up to each week's end and subtracted, so no list is cut short by a page. */
function Outlook() {
  const { t, locale } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const today = me.data?.today ?? todayIso();
  const cutoffs = [0, 7, 14, 21, 28].map((days) => dayAfter(today, days));
  const sides = ["receivable", "payable"] as const;
  const totals = useQueries({
    queries: sides.flatMap((direction) =>
      cutoffs.map((due_before) => ({
        queryKey: ["invoice-totals", { direction, outstanding: true, due_before }],
        queryFn: () => listInvoiceTotals({ direction, outstanding: true, due_before }),
      })),
    ),
  });
  if (totals.some((query) => query.isError))
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (totals.some((query) => !query.data)) return <Skeleton className="h-40 rounded-2xl" />;
  const byCutoff = (side: number) => totals.slice(side * cutoffs.length, (side + 1) * cutoffs.length).map((query) => query.data ?? []);
  const [collect, pay] = [byCutoff(0), byCutoff(1)];
  const rows = cutoffs.map((cutoff, index) => ({
    label: index === 0 ? t.money.overdue : t.money.weekOf(formatDays(cutoffs[index - 1]!, null, locale)),
    collect: between(collect, index),
    pay: between(pay, index),
    key: cutoff,
  }));
  const empty = rows.every((row) => row.collect.length === 0 && row.pay.length === 0);
  const shown = (amounts: [string, number][]) =>
    amounts.length === 0 ? "—" : amounts.map(([currency, amount]) => formatMoney(amount, currency, locale)).join(" · ");
  return (
    <section className="grid gap-2">
      <h2 className="text-muted-foreground text-xs font-medium">{t.money.next}</h2>
      {empty ? (
        <p className="text-muted-foreground border-border rounded-2xl border px-4 py-3 text-sm">{t.money.nothingDue}</p>
      ) : (
        <div className="border-border bg-card overflow-hidden rounded-2xl border">
          <div className="text-muted-foreground border-border grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b px-4 py-2 text-xs">
            <span />
            <span className="text-end">{t.money.toCollect}</span>
            <span className="text-end">{t.money.toPay}</span>
          </div>
          {rows.map((row) => (
            <div
              key={row.key}
              className="border-border grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b px-4 py-2.5 text-sm last:border-b-0"
            >
              <span className="text-muted-foreground text-xs">{row.label}</span>
              <span className="text-end tabular-nums break-words">{shown(row.collect)}</span>
              <span className="text-end tabular-nums break-words">{shown(row.pay)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** What is left per currency up to cutoff ``index``, less what was left up to the one before. */
function between(cumulative: InvoiceTotal[][], index: number): [string, number][] {
  const sum = (totals: InvoiceTotal[] | undefined) => {
    const out = new Map<string, number>();
    for (const total of totals ?? []) {
      if (total.currency && total.remaining_amount !== null) out.set(total.currency, Number(total.remaining_amount));
    }
    return out;
  };
  const now = sum(cumulative[index]);
  const before = index === 0 ? new Map<string, number>() : sum(cumulative[index - 1]);
  return [...now]
    .map(([currency, amount]): [string, number] => [currency, amount - (before.get(currency) ?? 0)])
    .filter(([, amount]) => Math.abs(amount) >= 0.005);
}

function Movements() {
  const { t } = useI18n();
  const movements = usePagedList(
    ["money-movements"],
    (page) => listMovements({ sort: "-booked_on", ...page }),
    PAGE,
    (movement) => movement.id,
  );
  if (movements.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!movements.items) return <Skeleton className="h-40 rounded-2xl" />;
  return (
    <section className="grid gap-2">
      <h2 className="text-muted-foreground text-xs font-medium">{t.money.movements}</h2>
      {movements.items.length === 0 ? (
        <p className="text-muted-foreground border-border rounded-2xl border px-4 py-3 text-sm">{t.money.noMovements}</p>
      ) : (
        <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
          {movements.items.map((movement) => (
            <li key={movement.id}>
              <MovementRow movement={movement} />
            </li>
          ))}
        </ul>
      )}
      {movements.more ? (
        <Button variant="ghost" className="justify-self-center" disabled={movements.loadingMore} onClick={movements.more}>
          {t.inbox.showMore}
        </Button>
      ) : null}
    </section>
  );
}

function MovementRow({ movement }: { movement: Movement }) {
  const { t, locale } = useI18n();
  const isOwner = useIsOwner();
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (category: MovementCategory) => changeMovement(movement.id, { category }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["money-movements"] }),
  });
  const amount = Number(movement.amount);
  const label = movement.category ? t.money.categories[movement.category] : t.money.uncategorised;
  return (
    <div className="flex items-start gap-3 px-4 py-2.5">
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium break-words">
          {movement.counterparty ?? movement.description ?? "—"}
        </span>
        <span className="text-muted-foreground flex flex-wrap items-center gap-x-2 text-xs">
          <span className="whitespace-nowrap">{formatDays(movement.booked_on, null, locale)}</span>
          {movement.estimated ? <span>{t.money.estimated}</span> : null}
          {isOwner ? (
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="hover:text-foreground underline-offset-4 hover:underline disabled:opacity-50"
                  aria-label={`${t.money.category}: ${label}`}
                  disabled={change.isPending}
                >
                  {label}
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {CATEGORIES.map((category) => (
                  <DropdownMenuItem key={category} onSelect={() => change.mutate(category)}>
                    {t.money.categories[category]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <span>{label}</span>
          )}
        </span>
      </span>
      <span className="shrink-0 text-end text-sm font-medium tabular-nums">
        {formatMoney(amount, movement.currency, locale)}
      </span>
    </div>
  );
}
