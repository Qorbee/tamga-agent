"use client";

import { ArrowRight02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useRuleWords } from "@/components/policy-settings";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  confirmPolicyDraft,
  discardPolicyDraft,
  getPolicy,
  getPolicyDraft,
  getReservePresets,
  type Policy,
  type PolicyDraft,
} from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";

type Field = keyof typeof FIELDS;
type DraftChange = PolicyDraft["change"];
// The fields a draft may change, in the order the card lists them.
const FIELDS = {
  approval_limit: true,
  currency: true,
  pay_when_paid: true,
  reserve_rule: true,
  payment_weekday: true,
  methods: true,
  default_method: true,
  payments_recorded: true,
  remainder_tolerance: true,
} as const;

/**
 * What the chat's `draft_policy` drafted from the owner's words, as a card under Tamga's answer:
 * each rule it changes, as it stands and as it would be, and Confirm, which puts it in force over
 * the version in force then, or Discard. Read from the draft itself, so it shows whether it was
 * answered since; until the tool has answered, nothing.
 */
export function PolicyDraftCard({ result }: { result: unknown }) {
  const { t } = useI18n();
  const id = draftId(result);
  const draft = useQuery({
    queryKey: ["policy-draft", id],
    queryFn: () => getPolicyDraft(id ?? ""),
    enabled: id !== null,
    retry: false,
  });
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  if (id === null) return null;
  return (
    <div data-slot="chat-card" className="border-border bg-card my-3 rounded-2xl border px-4 py-3">
      {draft.data && policy.data ? (
        <Drafted draft={draft.data} policy={policy.data} />
      ) : draft.isError || policy.isError ? (
        <p className="text-muted-foreground text-sm">{t.assistant.cards.gone}</p>
      ) : (
        <Skeleton className="h-24 rounded-xl" />
      )}
    </div>
  );
}

function Drafted({ draft, policy }: { draft: PolicyDraft; policy: Policy }) {
  const { t } = useI18n();
  const words = t.assistant.policyDraft;
  const owner = useIsOwner();
  const queryClient = useQueryClient();
  const value = useValueWords(policy);
  const answer = useMutation({
    mutationFn: (confirm: boolean) => (confirm ? confirmPolicyDraft(draft.id) : discardPolicyDraft(draft.id)),
    onSuccess: (answered) => {
      queryClient.setQueryData(["policy-draft", draft.id], answered);
      if (answered.state !== "confirmed") return;
      // As a change made in Settings: the next evaluation reads the new version.
      for (const key of ["policy", "decisions", "invoices", "invoice", "payment-run"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  const change = draft.change;
  const open = draft.state === "draft";
  // While it waits, only what it would change: a rule the owner restated as it stands is left out.
  const fields = (Object.keys(FIELDS) as Field[]).filter(
    (field) =>
      change[field] !== null && change[field] !== undefined && (!open || value(field, policy) !== value(field, policy, change)),
  );

  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-sm font-medium">{words.title}</p>
        <p className="text-muted-foreground mt-0.5 text-sm italic">“{draft.said}”</p>
      </div>
      <dl className="flex flex-col gap-2 text-sm">
        {fields.map((field) => (
          <div key={field} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
            <dt className="text-muted-foreground">{words.fields[field]}</dt>
            <dd className="flex items-center gap-1.5 font-medium">
              {/* Before, only while it waits: once in force, the policy already says the after. */}
              {open ? (
                <>
                  <span className="text-muted-foreground font-normal line-through">{value(field, policy)}</span>
                  <HugeiconsIcon icon={ArrowRight02Icon} strokeWidth={1.75} className="text-muted-foreground size-3.5" aria-hidden />
                </>
              ) : null}
              <span>{value(field, policy, change)}</span>
            </dd>
          </div>
        ))}
      </dl>
      {open ? (
        owner ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={answer.isPending} onClick={() => answer.mutate(true)}>
              {words.confirm}
            </Button>
            <Button size="sm" variant="ghost" disabled={answer.isPending} onClick={() => answer.mutate(false)}>
              {words.discard}
            </Button>
            <span className="text-muted-foreground text-xs">{words.waits}</span>
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">{words.ownerOnly}</p>
        )
      ) : (
        <p className="text-muted-foreground text-xs" role="status">
          {draft.state === "confirmed" && draft.made_version !== null ? words.confirmed(draft.made_version) : words.discarded}
        </p>
      )}
      {answer.isError ? (
        <p className="text-destructive text-xs" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
    </div>
  );
}

/** A field's value in the owner's words: as the policy has it, or as the change would make it. */
function useValueWords(policy: Policy) {
  const { t, locale } = useI18n();
  const ruleWords = useRuleWords();
  const presets = useQuery({ queryKey: ["reserve-presets"], queryFn: getReservePresets, staleTime: Infinity });
  const settings = t.settings.howYouPay;
  const words = t.assistant.policyDraft;
  return (field: Field, from: Policy, change?: DraftChange): string => {
    const currency = change?.currency ?? from.currency;
    switch (field) {
      case "approval_limit":
        return formatMoney(Number(change?.approval_limit ?? from.approval_limit), currency, locale);
      case "currency":
        return currency;
      case "pay_when_paid":
        return (change?.pay_when_paid ?? from.pay_when_paid) ? words.yes : words.no;
      case "reserve_rule": {
        const key = change?.reserve_rule ?? from.reserve.key;
        const rule = [...(presets.data ?? []), ...policy.reserve_rules].find((one) => one.key === key);
        return rule ? ruleWords(rule, currency).name : key;
      }
      case "payment_weekday": {
        const day = change ? change.payment_weekday : from.payment_weekday;
        return day === null || day === undefined || day === -1 ? settings.anyDay : (settings.weekdays[day] ?? String(day));
      }
      case "methods":
        return (change?.methods ?? from.methods).map((method) => t.payments.methods[method]).join(", ");
      case "default_method":
        return t.payments.methods[change?.default_method ?? from.default_method];
      case "payments_recorded":
        return t.settings.payments[change?.payments_recorded ?? from.payments_recorded];
      case "remainder_tolerance":
        return `${Number(change?.remainder_tolerance ?? from.remainder_tolerance)}%`;
    }
  };
}

/** The draft's id in what `draft_policy` returned: an object, or the same as JSON; nothing when
 * the tool refused. */
function draftId(result: unknown): string | null {
  let value = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (value && typeof value === "object" && "draft_id" in value && typeof value.draft_id === "string") {
    return value.draft_id;
  }
  return null;
}
