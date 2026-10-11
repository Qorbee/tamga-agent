"use client";

import { ArrowRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError, createGuestSession, listScenarios } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/**
 * The judge's way in (ADR-0004): a scenario picked here opens a private sandbox company with it,
 * no account needed. Used on the landing page, and in the playground bar for a fresh sandbox.
 */
export function ScenarioPicker({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: listScenarios, enabled: open, staleTime: Infinity });
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
    <Dialog open={open} onOpenChange={(next) => !start.isPending && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t.playground.pickTitle}</DialogTitle>
          <DialogDescription>{t.playground.pickDescription}</DialogDescription>
        </DialogHeader>
        <ul className="grid gap-2">
          {scenarios.isPending
            ? [0, 1].map((key) => <Skeleton key={key} className="h-20 rounded-xl" />)
            : (scenarios.data ?? []).map((scenario) => {
                const opening = start.isPending && start.variables === scenario.key;
                return (
                  <li key={scenario.key}>
                    <button
                      type="button"
                      disabled={start.isPending}
                      onClick={() => start.mutate(scenario.key)}
                      className="border-border hover:bg-muted focus-visible:ring-ring group flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-start transition-colors outline-none focus-visible:ring-2 disabled:opacity-60"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{scenario.title}</span>
                        <span className="text-muted-foreground mt-0.5 block text-xs text-pretty">{scenario.summary}</span>
                      </span>
                      <span className="text-muted-foreground group-hover:text-foreground flex shrink-0 items-center gap-1 text-xs">
                        {opening ? t.auth.openingSandbox : t.playground.open}
                        <HugeiconsIcon icon={ArrowRight01Icon} className="size-4" />
                      </span>
                    </button>
                  </li>
                );
              })}
        </ul>
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
