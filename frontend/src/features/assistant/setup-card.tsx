"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { MethodsField, PayDayField, ReserveChoices, usePolicyChange } from "@/components/policy-settings";
import { Button } from "@/components/ui/button";
import { getMe, getPolicy, getReservePresets, type PaymentMethod } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/** Later is per company: another company on this browser is still asked. */
const later = (companyId: string) => `tamga.setup.later.${companyId}`;

function laterChosen(companyId: string | undefined): boolean {
  if (!companyId) return false;
  try {
    return window.localStorage.getItem(later(companyId)) === "1";
  } catch {
    return false;
  }
}

/**
 * The owner's first answers on how Tamga pays, on the chat's empty state until they have set a
 * policy: the ways they pay, the reserve and the pay day, saved together as its first version.
 * Everything here is changed later in Settings, or by telling Tamga in the chat. A sandbox runs on
 * the defaults, so a judge is never asked.
 */
export function SetupCard() {
  const { t } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const presets = useQuery({ queryKey: ["reserve-presets"], queryFn: getReservePresets, staleTime: Infinity });
  const [dismissed, setDismissed] = useState<string | null>(null);
  // What most firms here start from: paid by bank, test USDC beside it, two weeks kept.
  const [methods, setMethods] = useState<PaymentMethod[]>(["bank_transfer", "vault_usdc"]);
  const [first, setFirst] = useState<PaymentMethod>("bank_transfer");
  const [reserve, setReserve] = useState("steady");
  const [day, setDay] = useState<number | null>(null);
  const change = usePolicyChange();

  const companyId = me.data?.company.id;
  if (
    !companyId ||
    dismissed === companyId ||
    laterChosen(companyId) ||
    me.data?.role !== "owner" ||
    me.data.company.kind === "sandbox" ||
    policy.data?.version !== 0 ||
    !presets.data
  )
    return null;
  const words = t.settings.howYouPay;
  return (
    <section aria-label={t.setup.title} className="border-border bg-card grid gap-3 rounded-2xl border px-4 py-3.5">
      <div>
        <h2 className="text-base font-medium">{t.setup.title}</h2>
        <p className="text-muted-foreground text-xs text-pretty">{t.setup.body}</p>
      </div>
      <div className="flex flex-col">
        <MethodsField
          methods={methods}
          first={first}
          disabled={change.isPending}
          onChange={(chosen, proposed) => {
            setMethods(chosen);
            setFirst(proposed);
          }}
        />
      </div>
      <div className="grid gap-2">
        <span className="text-sm">
          {words.reserve}
          <span className="text-muted-foreground block text-xs text-pretty">{words.reserveHint}</span>
        </span>
        <ReserveChoices
          presets={presets.data}
          own={[]}
          inUse={reserve}
          currency={policy.data.currency}
          disabled={change.isPending}
          onUse={setReserve}
        />
      </div>
      <div className="flex flex-col">
        <PayDayField value={day} disabled={change.isPending} onChange={setDay} />
      </div>
      {change.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={change.isPending}
          onClick={() =>
            change.mutate({ methods, default_method: first, reserve_rule: reserve, payment_weekday: day ?? -1 })
          }
        >
          {t.setup.save}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            try {
              window.localStorage.setItem(later(companyId), "1");
            } catch {
              // A browser that keeps nothing asks again next time.
            }
            setDismissed(companyId);
          }}
        >
          {t.setup.later}
        </Button>
      </div>
    </section>
  );
}
