"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Locale } from "@/lib/i18n";
import { I18nProvider } from "@/lib/i18n/client";

/** Client-side context for the whole app: server state, language, tooltips. */
export function Providers({ locale, children }: { locale: Locale; children: ReactNode }) {
  // One client per browser tab; created in state so a re-render never discards the cache.
  const [queryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000 } } }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider locale={locale}>
        <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
