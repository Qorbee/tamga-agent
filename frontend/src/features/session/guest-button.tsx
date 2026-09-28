"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import type { ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { createGuestSession } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { authError } from "./errors";

/** One click into a private sandbox (ADR-0004): the judge's way in, no account needed. */
export function GuestButton({ label, ...props }: { label: string } & ComponentProps<typeof Button>) {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const open = useMutation({
    mutationFn: createGuestSession,
    onSuccess: (me) => {
      // A previous user's data must not show in the new sandbox.
      queryClient.clear();
      queryClient.setQueryData(["me"], me);
      router.push("/app");
    },
  });

  return (
    <div className="grid gap-1.5">
      <Button {...props} disabled={open.isPending} onClick={() => open.mutate()}>
        {open.isPending ? t.auth.openingSandbox : label}
      </Button>
      {open.isError ? (
        <p className="text-danger-foreground text-xs" role="alert">
          {authError(open.error, t)}
        </p>
      ) : null}
    </div>
  );
}
