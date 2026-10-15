"use client";

import { Delete02Icon } from "@hugeicons/core-free-icons";
import { useQuery } from "@tanstack/react-query";

import { IconButton } from "@/components/icon-button";
import { Row, usePolicyChange } from "@/components/policy-settings";
import { type CustomRule, getPolicy, listPartyGroups, type Policy } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

type Condition = CustomRule["when"][number];

/**
 * A rule of the company's own in words, worked out by code from its conditions (ADR-0033): what
 * it does, then when ("Pay first: firms in Needs cash and owed over 30 days"). Firms a rule names
 * one by one are counted rather than named: a rule names a group for that.
 */
export function useCustomRuleWords() {
  const { t, locale } = useI18n();
  const words = t.settings.yourRules;
  const groups = useQuery({ queryKey: ["party-groups"], queryFn: listPartyGroups, staleTime: 60_000 });
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const currency = policy.data?.currency ?? "EUR";

  const condition = (each: Condition): string => {
    const value = each.value;
    const list = Array.isArray(value) ? value : [String(value)];
    const out = each.operator === "not_in";
    const above = each.operator === "above";
    switch (each.attribute) {
      case "group":
        return words.group(
          list.map((id) => groups.data?.find((group) => group.id === id)?.name ?? words.unknownGroup).join(", "),
          out,
        );
      case "firm":
        return words.firm(words.firmsCount(list.length), out);
      case "firm_kind":
        return value === "person" ? words.kind.person : words.kind.organisation;
      case "days_owed":
        return words.daysOwed(Number(value), above);
      case "days_to_due":
        return words.daysToDue(Number(value), above);
      case "amount":
        return words.amount(formatMoney(Number(value), currency, locale), above);
      case "currency":
        return words.currency(list.join(", "), out);
      case "method":
        return words.method(t.payments.methods[value as keyof typeof t.payments.methods] ?? String(value));
      case "client_paid":
        return words.clientPaid(value === true);
    }
  };
  return (rule: CustomRule) => `${words.effects[rule.then]}: ${rule.when.map(condition).join(words.join)}`;
}

/** The rule a decision's result names (`custom:<effect>:<key>`), in words; null for a built-in
 * rule, or one no longer in the policy. */
export function useCustomResultWords() {
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const words = useCustomRuleWords();
  return (ruleId: string): string | null => {
    if (!ruleId.startsWith("custom:")) return null;
    const key = ruleId.split(":").slice(2).join(":");
    const rule = policy.data?.custom_rules?.find((one) => one.key === key);
    return rule ? words(rule) : null;
  };
}

/** Settings → Payments: the company's own rules in words, each removable; new ones come from the
 * chat, where Tamga drafts them and the owner confirms the card. */
export function CustomRulesField({ policy, owner }: { policy: Policy; owner: boolean }) {
  const { t } = useI18n();
  const words = t.settings.yourRules;
  const say = useCustomRuleWords();
  const change = usePolicyChange();
  const rules = policy.custom_rules ?? [];
  return (
    <Row label={words.title} hint={words.hint}>
      {rules.length === 0 ? (
        <p className="text-muted-foreground w-full text-sm">{words.none}</p>
      ) : (
        <ul className="divide-border border-border w-full divide-y rounded-xl border text-sm">
          {rules.map((rule) => (
            <li key={rule.key} className="flex items-start gap-2 px-3 py-2">
              <span className="min-w-0 flex-1 text-pretty">{say(rule)}</span>
              {owner ? (
                <IconButton
                  label={words.remove}
                  icon={Delete02Icon}
                  disabled={change.isPending}
                  onClick={() => change.mutate({ remove_custom: rule.key })}
                />
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Row>
  );
}
