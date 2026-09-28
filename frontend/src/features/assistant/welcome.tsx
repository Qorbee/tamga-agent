"use client";

import { useI18n } from "@/lib/i18n/client";

/**
 * The chat's empty state: a greeting and the Today strip (cash, approvals waiting, next run), which
 * replaces a separate dashboard. Figures come from the backend; until the books are imported the
 * strip shows dashes and says why, rather than numbers that are not real.
 */
export function Welcome() {
  const { t } = useI18n();
  const cells = [t.today.available, t.today.needsYou, t.today.nextRun];

  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 fill-mode-both mb-8 flex flex-col gap-6 duration-300">
      <h1 className="text-xl font-medium tracking-tight text-balance">{t.assistant.greeting}</h1>
      <section aria-label={t.today.title} className="border-border bg-card rounded-2xl border">
        <dl className="divide-border grid grid-cols-3 divide-x">
          {cells.map((label) => (
            <div key={label} className="flex min-w-0 flex-col justify-between gap-1 px-4 py-3.5">
              <dt className="text-muted-foreground text-xs leading-snug">{label}</dt>
              <dd className="text-muted-foreground text-lg font-medium">—</dd>
            </div>
          ))}
        </dl>
        <p className="border-border text-muted-foreground border-t px-4 py-2.5 text-xs">
          {t.today.pending}
        </p>
      </section>
    </div>
  );
}
