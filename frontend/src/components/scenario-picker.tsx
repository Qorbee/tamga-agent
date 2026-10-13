"use client";

import { ArrowLeft01Icon, ArrowRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Field } from "@/components/field";
import { Segmented } from "@/components/segmented";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, createGuestSession, listScenarios, type GuestStart, type Scenario } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

// The reserve presets every company can pick: their keys are the server's (`policy/reserve.py`).
const RESERVES = ["off", "light", "steady", "careful"] as const;
// Monday to Friday, as the policy counts them.
const WEEKDAYS = [0, 1, 2, 3, 4] as const;

/**
 * The judge's way in (ADR-0004): a scenario picked here, then who they are, the company they run
 * and how it pays, opens a private sandbox company with it, no account needed. Each answer is
 * optional: the scenario's own stands in. Used on the landing page, and in the playground bar for
 * a fresh sandbox.
 */
export function ScenarioPicker({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: listScenarios, enabled: open, staleTime: Infinity });
  const [picked, setPicked] = useState<Scenario | null>(null);
  const start = useMutation({
    mutationFn: createGuestSession,
    onSuccess: () => {
      // A full load, not a client navigation: a previous sandbox's cache, chat session and
      // event stream must not carry into the new one.
      queryClient.clear();
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full load on purpose, above
      window.location.assign("/app");
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (start.isPending) return;
        if (!next) setPicked(null);
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-h-[calc(var(--app-height,100dvh)-2rem)] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{picked ? t.playground.setupTitle : t.playground.pickTitle}</DialogTitle>
          <DialogDescription>{picked ? picked.title : t.playground.pickDescription}</DialogDescription>
        </DialogHeader>
        {picked ? (
          <Setup
            key={picked.key}
            scenario={picked}
            opening={start.isPending}
            onBack={() => setPicked(null)}
            onOpen={(body) => start.mutate(body)}
          />
        ) : (
          <ul className="grid gap-2">
            {scenarios.isPending
              ? [0, 1].map((key) => <Skeleton key={key} className="h-20 rounded-xl" />)
              : (scenarios.data ?? []).map((scenario) => (
                  <li key={scenario.key}>
                    <button
                      type="button"
                      onClick={() => setPicked(scenario)}
                      className="border-border hover:bg-muted focus-visible:ring-ring group flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-start transition-colors outline-none focus-visible:ring-2"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{scenario.title}</span>
                        <span className="text-muted-foreground mt-0.5 block text-xs text-pretty">{scenario.summary}</span>
                      </span>
                      <span className="text-muted-foreground group-hover:text-foreground flex shrink-0 items-center gap-1 text-xs">
                        {t.playground.open}
                        <HugeiconsIcon icon={ArrowRight01Icon} className="size-4" />
                      </span>
                    </button>
                  </li>
                ))}
          </ul>
        )}
        {start.isError || scenarios.isError ? (
          <p className="text-danger-foreground text-xs" role="alert">
            {start.error instanceof ApiError
              ? ((t.auth.errors as Record<string, string>)[start.error.code] ?? t.auth.errors.generic)
              : t.errors.load}
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}


/** Who the judge is, the company, and how it pays: the scenario's own, theirs to change. */
function Setup({
  scenario,
  opening,
  onBack,
  onOpen,
}: {
  scenario: Scenario;
  opening: boolean;
  onBack: () => void;
  onOpen: (body: GuestStart) => void;
}) {
  const { t, locale } = useI18n();
  const words = t.playground.setup;
  const policy = scenario.policy;
  const [name, setName] = useState("");
  const [company, setCompany] = useState(scenario.company);
  const [limit, setLimit] = useState(policy ? String(Number(policy.approval_limit)) : "");
  const [reserve, setReserve] = useState(policy?.reserve_rule ?? "steady");
  // A scenario with no fixed pay day keeps none unless the judge picks one.
  const [payDay, setPayDay] = useState<number | null>(policy?.payment_weekday ?? null);
  const limitValid = /^\d{1,9}(\.\d{1,2})?$/.test(limit);
  const weekday = (day: number) =>
    new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }).format(new Date(Date.UTC(2026, 0, 5 + day)));

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (policy && !limitValid) return;
        onOpen({
          scenario: scenario.key,
          name: name.trim() || null,
          company: company.trim() || null,
          choices: policy ? { approval_limit: limit, reserve_rule: reserve, payment_weekday: payDay } : null,
        });
      }}
    >
      <Field label={words.name} htmlFor="sandbox-name" hint={words.nameHint}>
        <Input id="sandbox-name" value={name} onChange={(event) => setName(event.target.value)} placeholder={scenario.owner} maxLength={60} autoComplete="name" />
      </Field>
      <Field label={words.company} htmlFor="sandbox-company" hint={words.companyHint}>
        <Input id="sandbox-company" value={company} onChange={(event) => setCompany(event.target.value)} placeholder={scenario.company} maxLength={80} autoComplete="organization" />
      </Field>
      {policy ? (
        <fieldset className="grid gap-4">
          <legend className="pb-1 text-sm font-medium">{words.howYouPay}</legend>
          <Field label={words.limit} htmlFor="sandbox-limit" hint={words.limitHint} error={limitValid ? undefined : words.limitInvalid}>
            <span className="flex items-center gap-2">
              <Input id="sandbox-limit" inputMode="decimal" value={limit} onChange={(event) => setLimit(event.target.value)} className="w-32 text-end tabular-nums" />
              <span className="text-muted-foreground text-sm">{policy.currency}</span>
            </span>
          </Field>
          <div className="grid gap-1.5">
            <span className="text-sm font-medium">{words.reserve}</span>
            <Segmented
              fill
              label={words.reserve}
              value={reserve}
              onChange={setReserve}
              options={RESERVES.map((key) => ({ value: key, label: words.reserves[key] }))}
            />
            <p className="text-muted-foreground text-xs">{words.reserveHints[reserve as (typeof RESERVES)[number]] ?? ""}</p>
          </div>
          <div className="grid gap-1.5">
            <span className="text-sm font-medium">{words.payDay}</span>
            <Segmented
              fill
              label={words.payDay}
              value={payDay === null ? "any" : String(payDay)}
              onChange={(value) => setPayDay(value === "any" ? null : Number(value))}
              options={[
                ...(policy.payment_weekday === null ? [{ value: "any", label: t.settings.howYouPay.anyDay }] : []),
                ...WEEKDAYS.map((day) => ({ value: String(day), label: weekday(day) })),
              ]}
            />
          </div>
        </fieldset>
      ) : null}
      <div className="flex items-center justify-between gap-3 pt-1">
        <Button type="button" variant="ghost" onClick={onBack} disabled={opening}>
          <HugeiconsIcon icon={ArrowLeft01Icon} strokeWidth={1.75} />
          {words.back}
        </Button>
        <Button type="submit" disabled={opening || (policy !== null && !limitValid)}>
          {opening ? t.auth.openingSandbox : words.open}
        </Button>
      </div>
    </form>
  );
}
