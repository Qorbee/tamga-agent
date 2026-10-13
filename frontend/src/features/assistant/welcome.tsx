"use client";

import { MoneyTiles } from "@/components/money-tiles";
import { PaymentRunCard } from "@/components/payment-run";
import { useI18n } from "@/lib/i18n/client";

import { SetupCard } from "./setup-card";

/**
 * The chat's empty state: a greeting, the owner's first answers on how Tamga pays until they are
 * given, where the money stands and the next payment run, which replace a separate dashboard.
 * Every figure comes from the backend; a card with nothing real to show is left out.
 */
export function Welcome() {
  const { t } = useI18n();

  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 fill-mode-both mb-8 flex flex-col gap-4 duration-300">
      <h1 className="mb-2 text-xl font-medium tracking-tight text-balance">{t.assistant.greeting}</h1>
      <SetupCard />
      <MoneyTiles />
      <PaymentRunCard />
    </div>
  );
}
