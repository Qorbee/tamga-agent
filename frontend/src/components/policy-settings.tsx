"use client";

import { Copy01Icon, Delete02Icon, PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { changePolicy, type PaymentMethod, type Policy, type PolicyChange, type ReserveRule } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * How the company pays, as the owner sets it (docs/architecture/05): the ways it pays, the reserve
 * and the day it likes to pay on. Each field is controlled, so Settings saves a change at once and
 * the setup card on the chat home saves its three answers together, as one version of the policy.
 */

// The chosen option is filled and in full ink; the others recede.
export const SEGMENT =
  "text-muted-foreground data-[state=on]:bg-accent data-[state=on]:text-accent-foreground";
const METHODS: readonly PaymentMethod[] = ["bank_transfer", "vault_usdc"];
const WEEK = [0, 1, 2, 3, 4, 5, 6] as const;

/** A new version of the policy with only these fields changed; every page reading it follows. */
export function usePolicyChange(onDone?: (sent: PolicyChange) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: changePolicy,
    onSuccess: (updated, sent) => {
      queryClient.setQueryData(["policy"], updated);
      // The next evaluation reads the new version; the open lists show it then.
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["invoice"] });
      // The pay day and the reserve set the run's day and what fits in it.
      void queryClient.invalidateQueries({ queryKey: ["payment-run"] });
      onDone?.(sent);
    },
  });
}

export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="border-border flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t py-3 first-of-type:border-t-0">
      <span className="min-w-0 flex-1 basis-48 text-sm">
        {label}
        {hint ? <span className="text-muted-foreground block text-xs text-pretty">{hint}</span> : null}
      </span>
      {children}
    </div>
  );
}

/** The ways the company pays, and the one proposed first when a payee can take both. */
export function MethodsField({
  methods,
  first,
  disabled,
  onChange,
}: {
  methods: PaymentMethod[];
  first: PaymentMethod;
  disabled?: boolean;
  onChange: (methods: PaymentMethod[], first: PaymentMethod) => void;
}) {
  const { t } = useI18n();
  const words = t.settings.howYouPay;
  return (
    <>
      <Row label={words.methods} hint={words.methodsHint}>
        <ToggleGroup
          type="multiple"
          variant="outline"
          spacing={0}
          value={methods}
          aria-label={words.methods}
          disabled={disabled}
          onValueChange={(value) => {
            const chosen = METHODS.filter((method) => value.includes(method));
            if (chosen.length === 0) return;
            onChange(chosen, chosen.includes(first) ? first : chosen[0]!);
          }}
        >
          {METHODS.map((method) => (
            <ToggleGroupItem key={method} value={method} className={SEGMENT}>
              {t.payments.methods[method]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Row>
      {methods.length > 1 ? (
        <Row label={words.first}>
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={0}
            value={first}
            aria-label={words.first}
            disabled={disabled}
            onValueChange={(value) => {
              const chosen = METHODS.find((method) => method === value);
              if (chosen && chosen !== first) onChange(methods, chosen);
            }}
          >
            {methods.map((method) => (
              <ToggleGroupItem key={method} value={method} className={SEGMENT}>
                {t.payments.methods[method]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </Row>
      ) : null}
    </>
  );
}

/** The day the owner likes to pay on: a preference, never a rule. */
export function PayDayField({
  value,
  disabled,
  onChange,
}: {
  value: number | null;
  disabled?: boolean;
  onChange: (day: number | null) => void;
}) {
  const { t } = useI18n();
  const words = t.settings.howYouPay;
  return (
    <Row label={words.payDay} hint={words.payDayHint}>
      <ToggleGroup
        type="single"
        variant="outline"
        spacing={0}
        value={value === null ? "any" : String(value)}
        aria-label={words.payDay}
        disabled={disabled}
        className="flex-wrap"
        onValueChange={(chosen) => {
          if (!chosen) return;
          const day = chosen === "any" ? null : Number(chosen);
          if (day !== value) onChange(day);
        }}
      >
        <ToggleGroupItem value="any" className={SEGMENT}>
          {words.anyDay}
        </ToggleGroupItem>
        {WEEK.map((day) => (
          <ToggleGroupItem key={day} value={String(day)} className={cn(SEGMENT, "px-2")}>
            {words.weekdays[day]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </Row>
  );
}

/** A reserve rule as the owner reads it: a preset by its own words, one of theirs by its figures. */
export function useRuleWords() {
  const { t, locale } = useI18n();
  const words = t.settings.howYouPay;
  return (rule: ReserveRule, currency: string) => {
    const preset = rule.key.startsWith("own:") ? null : words.presets[rule.key];
    if (preset) return preset;
    const floor = Number(rule.floor) > 0 ? formatMoney(Number(rule.floor), currency, locale) : null;
    return { name: rule.name, body: words.ownBody(rule.cover_weeks, floor, rule.on_breach === "hold") };
  };
}

/**
 * The presets and the owner's own rules, the one in use marked. Settings may duplicate a rule into
 * one of the owner's own and change it; a preset itself never changes.
 */
export function ReserveChoices({
  presets,
  own,
  inUse,
  currency,
  disabled,
  onUse,
  onEdit,
  onRemove,
}: {
  presets: ReserveRule[];
  own: ReserveRule[];
  inUse: string;
  currency: string;
  disabled?: boolean;
  onUse: (key: string) => void;
  // Without these, only a choice: the setup card.
  onEdit?: (rule: ReserveRule, copy: boolean) => void;
  onRemove?: (key: string) => void;
}) {
  const { t } = useI18n();
  const words = t.settings.howYouPay;
  const read = useRuleWords();
  return (
    <ul className="grid gap-2" aria-label={words.reserve}>
      {[...presets, ...own].map((rule) => {
        const { name, body } = read(rule, currency);
        const used = rule.key === inUse;
        const mine = rule.key.startsWith("own:");
        return (
          <li
            key={rule.key}
            className={cn(
              "border-border flex items-start gap-3 rounded-xl border px-3 py-2.5",
              used && "border-primary bg-accent/40",
            )}
          >
            <button
              type="button"
              className="min-w-0 flex-1 text-start disabled:cursor-default"
              disabled={disabled || used}
              aria-pressed={used}
              onClick={() => onUse(rule.key)}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                {name}
                {used ? <span className="text-primary text-xs font-normal">{words.inUse}</span> : null}
              </span>
              <span className="text-muted-foreground block text-xs text-pretty">{body}</span>
            </button>
            {onEdit ? (
              <span className="flex shrink-0 gap-0.5">
                {rule.key !== "off" ? (
                  <IconAction label={words.duplicate} icon={Copy01Icon} disabled={disabled} onClick={() => onEdit(rule, true)} />
                ) : null}
                {mine ? (
                  <IconAction label={words.edit} icon={PencilEdit02Icon} disabled={disabled} onClick={() => onEdit(rule, false)} />
                ) : null}
                {mine && !used && onRemove ? (
                  <IconAction label={words.remove} icon={Delete02Icon} disabled={disabled} onClick={() => onRemove(rule.key)} />
                ) : null}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function IconAction({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string;
  icon: typeof Copy01Icon;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Hint label={label}>
      <Button variant="ghost" size="icon" className="text-muted-foreground size-8" aria-label={label} disabled={disabled} onClick={onClick}>
        <HugeiconsIcon icon={icon} strokeWidth={1.75} />
      </Button>
    </Hint>
  );
}

/** A rule of the owner's own: a copy of another, changed, or one of theirs changed again. */
export function ReserveEditor({
  initial,
  currency,
  pending,
  putInUse,
  onCancel,
  onSave,
}: {
  initial: ReserveRule;
  // A copy is put in use on Save; a rule changed stays as it was, in use or not.
  putInUse: boolean;
  currency: string;
  pending: boolean;
  onCancel: () => void;
  onSave: (rule: ReserveRule) => void;
}) {
  const { t } = useI18n();
  const words = t.settings.howYouPay;
  const [name, setName] = useState(initial.name);
  const [weeks, setWeeks] = useState(String(initial.cover_weeks));
  const [floor, setFloor] = useState(String(Number(initial.floor)));
  const [onBreach, setOnBreach] = useState(initial.on_breach);
  const [stance, setStance] = useState(initial.stance);
  const figure = floor.trim().replace(",", ".");
  const valid =
    name.trim().length > 0 &&
    name.trim().length <= 60 &&
    /^\d{1,2}$/.test(weeks) &&
    Number(weeks) <= 12 &&
    /^\d{1,12}(\.\d{1,2})?$/.test(figure);
  return (
    <form
      className="border-border grid gap-1 rounded-xl border px-3 py-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid) return;
        onSave({
          ...initial,
          name: name.trim(),
          active: true,
          cover_weeks: Number(weeks),
          floor: Number(figure).toFixed(2),
          on_breach: onBreach,
          stance,
        });
      }}
    >
      <Row label={words.ruleName}>
        <Input value={name} onChange={(event) => setName(event.target.value)} aria-label={words.ruleName} className="w-48" autoFocus />
      </Row>
      <Row label={words.coverWeeks}>
        <Input inputMode="numeric" value={weeks} onChange={(event) => setWeeks(event.target.value)} aria-label={words.coverWeeks} className="w-20 text-end tabular-nums" />
      </Row>
      <Row label={words.floor}>
        <span className="flex items-center gap-2">
          <Input inputMode="decimal" value={floor} onChange={(event) => setFloor(event.target.value)} aria-label={words.floor} className="w-32 text-end tabular-nums" />
          <span className="text-muted-foreground text-sm">{currency}</span>
        </span>
      </Row>
      <Row label={words.onBreach}>
        <ToggleGroup type="single" variant="outline" spacing={0} value={onBreach} aria-label={words.onBreach} onValueChange={(value) => {
          if (value === "hold" || value === "ask") setOnBreach(value);
        }}>
          <ToggleGroupItem value="ask" className={SEGMENT}>{words.ask}</ToggleGroupItem>
          <ToggleGroupItem value="hold" className={SEGMENT}>{words.hold}</ToggleGroupItem>
        </ToggleGroup>
      </Row>
      <Row label={words.stance}>
        <ToggleGroup type="single" variant="outline" spacing={0} value={stance} aria-label={words.stance} onValueChange={(value) => {
          if (value === "careful" || value === "balanced" || value === "lean") setStance(value);
        }}>
          {(["careful", "balanced", "lean"] as const).map((value) => (
            <ToggleGroupItem key={value} value={value} className={SEGMENT}>{words.stances[value]}</ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Row>
      <div className="flex gap-2 py-2">
        <Button type="submit" size="sm" disabled={!valid || pending}>
          {putInUse ? words.saveRule : t.settings.payments.save}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t.payments.cancel}
        </Button>
      </div>
    </form>
  );
}

/** A short id for a rule of the owner's own. */
export function ownKey(): string {
  return `own:${Math.random().toString(36).slice(2, 10).replace(/[^a-z0-9]/g, "") || "1"}`;
}

/** What a copy of a rule starts as: the owner's, named after it, based on its preset. */
export function copyOf(rule: ReserveRule, name: string): ReserveRule {
  return {
    ...rule,
    key: ownKey(),
    name,
    based_on: rule.based_on ?? rule.key,
    active: true,
  };
}

/** The policy's fields this file sets, for a caller that keeps its own draft. */
export type PayingDraft = Pick<Policy, "methods" | "default_method" | "payment_weekday"> & { reserve_rule: string };
