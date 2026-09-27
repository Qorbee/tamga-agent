"use client";

import { useQuery } from "@tanstack/react-query";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { getHealth } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

const tone = {
  connected: "bg-success",
  checking: "bg-warning animate-pulse motion-reduce:animate-none",
  offline: "bg-danger",
} as const;

/**
 * A dot showing whether the browser reaches the API. It calls `/health` from the browser, so it also
 * proves CORS is configured for this origin.
 */
export function ApiStatus() {
  const { t } = useI18n();
  const { status } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: 1,
  });
  const state = status === "success" ? "connected" : status === "error" ? "offline" : "checking";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span role="status" aria-label={t.api[state]} className="grid size-9 place-items-center">
          <span className={cn("size-2 rounded-full", tone[state])} />
        </span>
      </TooltipTrigger>
      <TooltipContent side="right">{t.api[state]}</TooltipContent>
    </Tooltip>
  );
}
