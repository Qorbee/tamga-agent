"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ApiError, getMe } from "@/lib/api";

/**
 * Sends a visitor with no session to sign-in. The session cookie belongs to the API's host, so only
 * the browser can tell; the API refuses every call without it anyway, so this is navigation, not the
 * boundary. An unreachable API is not a sign-out: the account menu says Tamga is offline. Sign-in
 * comes back to the whole address, the record open in the canvas (`?view=`) included.
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const here = search ? `${pathname}?${search}` : pathname;
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const signedOut = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (signedOut) router.replace(`/sign-in?next=${encodeURIComponent(here)}`);
  }, [signedOut, here, router]);

  return children;
}
