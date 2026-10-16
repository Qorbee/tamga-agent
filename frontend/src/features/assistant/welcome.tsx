"use client";

import { PaymentRunCard } from "@/components/payment-run";
import { useI18n } from "@/lib/i18n/client";

import { SetupCard } from "./setup-card";

/**
 * The chat's empty state, as plain as a chat app's: a greeting, the owner's first answers on how
 * Tamga pays until they are given, and the next payment run. Where the money stands is on Money.
 * Every figure comes from the backend; a card with nothing real to show is left out.
 */
export function Welcome() {
  const { t } = useI18n();

  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 fill-mode-both mb-8 flex flex-col gap-4 duration-300">
      <h1 className="mb-2 text-xl font-medium tracking-tight text-balance">{t.assistant.greeting}</h1>
      {/* On a phone the cards step aside while the owner types, so the keyboard leaves room for
          the box; they come back when it is left. */}
      <div className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-300 ease-out max-md:group-has-[.aui-composer-input:focus]/chat:grid-rows-[0fr] max-md:group-has-[.aui-composer-input:focus]/chat:opacity-0 motion-reduce:transition-none">
        <div className="flex min-h-0 flex-col gap-4 overflow-hidden">
          <SetupCard />
          <PaymentRunCard />
        </div>
      </div>
    </div>
  );
}
